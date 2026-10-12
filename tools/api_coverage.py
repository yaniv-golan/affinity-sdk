"""
Affinity API coverage ledger: which API operations the SDK supports, and how.

The ledger (``tools/api_coverage.json``) has one row per Affinity API operation:

- ``op``: ``"METHOD /path"`` as in the spec (V2 paths start with ``/v2``; V1 parameter names are
  normalized to ``{}``)
- ``api``: ``"v1"`` or ``"v2"``
- ``status``: ``supported`` (``call`` is the SDK call that sends it), ``equivalent`` (the
  capability is offered through another operation, named in ``via``), ``planned`` (``phase``
  says when), or ``unsupported`` (``reason`` says why; only by decision)
- ``call``: a Python expression on the client, e.g. ``"companies.get(CompanyId(1))"``;
  ``async_call`` overrides it for ``AsyncAffinity`` (``null``: no async form)
- ``when``: ``"v2-forbidden"`` for a V1 row used only as the retry after V2 answers 403 (the
  test refuses the first request and checks the retry)
- ``stability``: ``"beta"`` or ``"ga"`` (V2 ``x-stability-level``); ``min_api_version`` is the
  first V2 snapshot that has the operation (computed, not stored)

The operations come from committed snapshots: ``tools/openapi_snapshots/`` (V2 digests) and
``tools/api_coverage_v1_snapshot.json`` (the V1 docs), so the check runs offline.
``tests/test_api_coverage.py`` runs every supported row's call against a recording HTTP layer
and asserts the method, path and API version it requests.

Usage:
    python tools/api_coverage.py --check              # ledger vs snapshots (CI)
    python tools/api_coverage.py --markdown PATH      # coverage page for the docs
    python tools/api_coverage.py --update-v1-snapshot DOCS_MD --sha SHA

Exit codes: 0 ok, 1 ledger problems.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
LEDGER_PATH = TOOLS / "api_coverage.json"
SNAPSHOT_DIR = TOOLS / "openapi_snapshots"
V1_SNAPSHOT = TOOLS / "api_coverage_v1_snapshot.json"
STATUSES = ("supported", "equivalent", "planned", "unsupported")

_V1_OP = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH) +(?:https://api\.affinity\.co)?(/[A-Za-z0-9_/{}\-]+)"
)


def _names() -> dict[str, Any]:
    """Names available to ``call`` expressions: the SDK's ID types and models."""
    from affinity import models, types

    names: dict[str, Any] = {}
    for module in (types, models):
        names.update({n: getattr(module, n) for n in getattr(module, "__all__", dir(module))})
    from datetime import datetime, timezone

    names.update(datetime=datetime, timezone=timezone)
    return {n: v for n, v in names.items() if not n.startswith("_")}


#: Names for ``call`` expressions, loaded on first use (importing this module stays cheap).
CALL_NAMES: dict[str, Any] = {}


def call_names() -> dict[str, Any]:
    if not CALL_NAMES:
        CALL_NAMES.update(_names())
    return CALL_NAMES


def normalize_path(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def v2_snapshots() -> list[tuple[str, set[str]]]:
    """(version, operations) for each committed V2 snapshot, oldest first."""
    out = []
    for p in sorted(SNAPSHOT_DIR.glob("[0-9]*.json")):
        ops = json.loads(p.read_text())["digest"]["operations"]
        out.append((p.stem, set(ops)))
    return out


def v2_operations() -> dict[str, str]:
    """V2 operation -> first API version that has it."""
    first: dict[str, str] = {}
    for version, ops in v2_snapshots():
        for op in ops:
            first.setdefault(op, version)
    return first


def v1_operations() -> list[str]:
    return list(json.loads(V1_SNAPSHOT.read_text())["operations"])


def parse_v1_docs(text: str) -> list[str]:
    """Operations named in the V1 docs (examples with literal ids are dropped)."""
    ops = set()
    for method, path in _V1_OP.findall(text):
        if re.search(r"/\d", path):  # an example with a literal id
            continue
        if any(seg.count("{") != seg.count("}") for seg in path.split("/")):  # e.g. "list_id}"
            continue
        ops.add(f"{method} {normalize_path(path)}")
    return sorted(ops)


def load_ledger(path: Path = LEDGER_PATH) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text())["operations"])


def check_ledger(rows: list[dict[str, Any]]) -> list[str]:
    """Problems with the ledger: missing or unknown operations, bad rows."""
    problems: list[str] = []
    expected = {("v2", op) for op in v2_operations()} | {("v1", op) for op in v1_operations()}
    seen: dict[tuple[str, str], int] = {}
    for i, row in enumerate(rows):
        key = (row.get("api", "?"), row.get("op", "?"))
        seen[key] = seen.get(key, 0) + 1
        where = f"row {i} ({key[0]} {key[1]})"
        status = row.get("status")
        if status not in STATUSES:
            problems.append(f"{where}: status {status!r} not one of {STATUSES}")
        if status == "supported" and not row.get("call"):
            problems.append(f"{where}: supported without a call")
        if status == "equivalent" and not row.get("via"):
            problems.append(f"{where}: equivalent without via")
        if status == "planned" and not row.get("phase"):
            problems.append(f"{where}: planned without phase")
        if status == "unsupported" and not row.get("reason"):
            problems.append(f"{where}: unsupported without reason")
        if key[0] == "v2" and row.get("stability") not in ("beta", "ga"):
            problems.append(f"{where}: stability must be beta or ga")
    for key, n in seen.items():
        if n > 1:
            problems.append(f"{key[0]} {key[1]}: {n} rows")
        if key not in expected:
            problems.append(f"{key[0]} {key[1]}: not in the pinned specs")
    for key in sorted(expected - set(seen)):
        problems.append(f"{key[0]} {key[1]}: no ledger row")
    return problems


def markdown(rows: list[dict[str, Any]]) -> str:
    first = v2_operations()
    lines = [
        "# API coverage",
        "",
        "Every Affinity API operation and how the SDK supports it. Generated from",
        "`tools/api_coverage.json` by `python tools/api_coverage.py --markdown`; CI checks it",
        "against the pinned API specs and checks that each supported call requests its operation.",
        "",
    ]
    for api, title in (("v2", "V2 API"), ("v1", "V1 API")):
        sub = sorted(
            (r for r in rows if r["api"] == api), key=lambda r: (r["op"].split(" ", 1)[1], r["op"])
        )
        counts = {s: sum(r["status"] == s for r in sub) for s in STATUSES}
        lines += [
            f"## {title}",
            "",
            ", ".join(f"{n} {s}" for s, n in counts.items() if n),
            "",
            "| Operation | Status | SDK | Notes |",
            "|---|---|---|---|",
        ]
        for r in sub:
            notes = []
            if r.get("stability") == "beta":
                notes.append("beta")
            if api == "v2" and first.get(r["op"], "2024-01-01") != "2024-01-01":
                notes.append(f"API {first[r['op']]}+")
            if r.get("via"):
                notes.append(f"via {r['via']}")
            if r.get("phase"):
                notes.append(f"phase {r['phase']}")
            if r.get("reason"):
                notes.append(r["reason"])
            if r.get("note") and r["status"] in ("supported", "equivalent"):
                notes.append(r["note"])
            sdk = f"`{r['call']}`" if r.get("call") else ""
            lines.append(f"| `{r['op']}` | {r['status']} | {sdk} | {'; '.join(notes)} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--markdown", metavar="PATH")
    ap.add_argument("--update-v1-snapshot", metavar="DOCS_MD")
    ap.add_argument("--sha")
    args = ap.parse_args(argv)

    if args.update_v1_snapshot:
        if not args.sha:
            ap.error("--update-v1-snapshot needs --sha (the affinity-api-docs commit)")
        ops = parse_v1_docs(Path(args.update_v1_snapshot).read_text())
        V1_SNAPSHOT.write_text(
            json.dumps(
                {
                    "source": {
                        "repo": "yaniv-golan/affinity-api-docs",
                        "sha": args.sha,
                        "path": "docs/v1/affinity_api_docs.md",
                    },
                    "operations": ops,
                },
                indent=1,
            )
            + "\n"
        )
        print(f"wrote {V1_SNAPSHOT} ({len(ops)} operations)")
        return 0

    rows = load_ledger()
    if args.markdown:
        Path(args.markdown).write_text(markdown(rows))
        print(f"wrote {args.markdown}")
    if args.check or not args.markdown:
        problems = check_ledger(rows)
        for p in problems:
            print(p, file=sys.stderr)
        counts = {s: sum(r["status"] == s for r in rows) for s in STATUSES}
        print(f"{len(rows)} operations: " + ", ".join(f"{n} {s}" for s, n in counts.items()))
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
