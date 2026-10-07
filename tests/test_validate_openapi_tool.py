"""Tests for tools/validate_openapi_models.py (the weekly OpenAPI drift check).

No network: every case runs against the trimmed fixture spec in
``tests/fixtures/openapi/mini_openapi.json`` with test-local models, enums and tables
injected through ``CheckConfig``. The last test runs the real tool configuration against
a local checkout of affinity-api-docs when one is present ("green on day one").
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from enum import Enum, IntEnum
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from affinity.models.types import OpenIntEnum, OpenStrEnum

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = REPO_ROOT / "tools" / "validate_openapi_models.py"
FIXTURE_SPEC = REPO_ROOT / "tests" / "fixtures" / "openapi" / "mini_openapi.json"
COMMITTED_SNAPSHOT = REPO_ROOT / "tools" / "openapi_snapshot.json"
LOCAL_UPSTREAM_SPEC = REPO_ROOT.parent / "affinity-api-docs" / "docs" / "v2" / "openapi.json"
# Worktrees live under <repo>/.claude/worktrees/<name>; also look next to the main checkout.
_ALT_UPSTREAM = [
    LOCAL_UPSTREAM_SPEC,
    *(p.parent / "affinity-api-docs" / "docs" / "v2" / "openapi.json" for p in REPO_ROOT.parents),
]


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_openapi_models_under_test", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


# ---------------------------------------------------------------------------
# Test-local SDK stand-ins
# ---------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class WidgetModel(_Model):
    id: int
    created_at: str = Field(alias="createdAt")
    name: str | None = None
    kind: str
    is_public: bool = Field(False, alias="isPublic")
    tags: list[str] = Field(default_factory=list)


class WidgetPublicAliasModel(_Model):
    """Python name snake-cases to the spec's ``isPublic`` but the alias is ``public``."""

    id: int
    created_at: str = Field(alias="createdAt")
    name: str | None = None
    kind: str
    is_public: bool = Field(False, alias="public")
    tags: list[str] = Field(default_factory=list)


class NoteModel(_Model):
    id: int
    created_at: str = Field(alias="createdAt")
    type: str
    body: str | None = None
    parent: dict[str, Any] | None = None


class Kind(OpenStrEnum):
    ROUND = "round"


class ListKind(OpenIntEnum):
    PERSON = 0
    COMPANY = 1
    OPPORTUNITY = 8


class NoteKind(str, Enum):
    TEXT = "text"
    REPLY = "reply"


class FieldKind(OpenStrEnum):
    ENRICHED = "enriched"
    GLOBAL = "global"


LIST_KIND_MAP = {"person": 0, "company": 1, "opportunity": 8}


def _config(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "models": {"Widget": WidgetModel, "Note": NoteModel},
        "mappings": {"Widget": ("Widget",), "Note": ("Note",)},
        "skipped": {},
        "known_gaps": {},
        "known_extensions": {},
        "enum_checks": (
            tool.EnumCheck(
                Kind,
                (tool.EnumLocation("#/components/schemas/Widget/properties/kind"),),
            ),
        ),
    }
    base.update(overrides)
    return tool.CheckConfig(**base)


@pytest.fixture
def spec() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE_SPEC.read_text())
    return data


@pytest.fixture
def kind_gap() -> dict[tuple[str, str], str]:
    return {("Kind", "square"): "test: square not modelled"}


def _errors(spec: dict[str, Any], config: Any, snapshot: Any = None) -> list[str]:
    report = tool.run_checks(spec, config=config, snapshot=snapshot)
    return list(report.errors)


# ---------------------------------------------------------------------------
# §A schema resolution
# ---------------------------------------------------------------------------


class TestResolveProperties:
    def test_allof_plus_own_properties_are_merged(self, spec: dict[str, Any]) -> None:
        props = tool.resolve_properties(spec, {"$ref": "#/components/schemas/Widget"})
        assert set(props) == {"id", "createdAt", "name", "kind", "isPublic", "tags"}

    def test_oneof_returns_union_of_variants(self, spec: dict[str, Any]) -> None:
        # Regression: the old resolver returned {} for a oneOf + discriminator schema,
        # so any model mapped to it passed vacuously.
        props = tool.get_schema_properties(spec, "Note")
        assert props is not None
        assert set(props) == {"id", "createdAt", "type", "body", "parent"}

    def test_missing_component_returns_none(self, spec: dict[str, Any]) -> None:
        assert tool.get_schema_properties(spec, "Nope") is None

    def test_unresolvable_ref_is_an_error(self, spec: dict[str, Any]) -> None:
        with pytest.raises(tool.SchemaResolutionError):
            tool.resolve_properties(spec, {"$ref": "#/components/schemas/Missing"})

    def test_not_is_an_error(self, spec: dict[str, Any]) -> None:
        with pytest.raises(tool.SchemaResolutionError):
            tool.resolve_properties(spec, {"not": {"type": "string"}})

    def test_ref_cycle_does_not_recurse_forever(self, spec: dict[str, Any]) -> None:
        spec["components"]["schemas"]["Loop"] = {
            "allOf": [{"$ref": "#/components/schemas/Loop"}],
            "properties": {"x": {"type": "string"}},
        }
        props = tool.resolve_properties(spec, {"$ref": "#/components/schemas/Loop"})
        assert set(props) == {"x"}


# ---------------------------------------------------------------------------
# §B model checks
# ---------------------------------------------------------------------------


class TestModelChecks:
    def test_clean_fixture_passes(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        assert _errors(spec, _config(known_gaps=kind_gap)) == []

    def test_missing_property_matched_on_alias_not_python_name(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        # is_public snake-cases to isPublic, but the model only reads "public".
        config = _config(models={"Widget": WidgetPublicAliasModel, "Note": NoteModel})
        config.known_gaps.update(kind_gap)
        errors = _errors(spec, config)
        assert any("Widget" in e and "isPublic" in e for e in errors), errors

    def test_missing_variant_property_is_reported(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        class NoParent(_Model):
            id: int
            created_at: str = Field(alias="createdAt")
            type: str
            body: str | None = None

        config = _config(models={"Widget": WidgetModel, "Note": NoParent})
        config.known_gaps.update(kind_gap)
        errors = _errors(spec, config)
        assert any("Note" in e and "parent" in e for e in errors), errors

    def test_known_gap_suppresses_missing_property(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        config = _config(
            models={"Widget": WidgetPublicAliasModel, "Note": NoteModel},
            known_gaps={**kind_gap, ("Widget", "isPublic"): "test"},
        )
        assert _errors(spec, config) == []

    def test_mapped_schema_not_found_exits_1(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        config = _config(mappings={"Widget": ("Gadget",), "Note": ("Note",)})
        config.known_gaps.update(kind_gap)
        report = tool.run_checks(spec, config=config, snapshot=None)
        assert report.exit_code == 1
        assert any("Gadget" in e and "not found" in e for e in report.errors)

    def test_multi_schema_mapping_unions_properties(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        config = _config(
            models={"Widget": WidgetModel, "Note": NoteModel},
            mappings={"Widget": ("Widget",), "Note": ("TextNote", "ReplyNote")},
        )
        config.known_gaps.update(kind_gap)
        assert _errors(spec, config) == []

    def test_stale_known_gap_sdk_now_has_it(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        config = _config(known_gaps={**kind_gap, ("Widget", "isPublic"): "test"})
        errors = _errors(spec, config)
        assert any("stale KNOWN_GAPS" in e and "isPublic" in e for e in errors), errors

    def test_stale_known_gap_spec_dropped_it(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        config = _config(known_gaps={**kind_gap, ("Widget", "gone"): "test"})
        errors = _errors(spec, config)
        assert any("stale KNOWN_GAPS" in e and "gone" in e for e in errors), errors

    def test_stale_known_extension_is_an_error(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        # "tags" is in the spec now, and "nonexistent" is not a model field.
        config = _config(known_gaps=kind_gap, known_extensions={"Widget": {"tags", "nonexistent"}})
        errors = _errors(spec, config)
        assert any("KNOWN_EXTENSIONS" in e and "tags" in e for e in errors), errors
        assert any("KNOWN_EXTENSIONS" in e and "nonexistent" in e for e in errors), errors

    def test_skipped_model_is_not_checked(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        class V1Only(_Model):
            whatever: int

        config = _config(
            models={"Widget": WidgetModel, "Note": NoteModel, "V1Only": V1Only},
            skipped={"V1Only": "V1 payload"},
            known_gaps=kind_gap,
        )
        report = tool.run_checks(spec, config=config, snapshot=None)
        assert report.errors == []
        assert report.exit_code == 0

    def test_unmapped_unskipped_model_without_schema_is_an_error(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        class Orphan(_Model):
            whatever: int

        config = _config(
            models={"Widget": WidgetModel, "Note": NoteModel, "Orphan": Orphan},
            known_gaps=kind_gap,
        )
        errors = _errors(spec, config)
        assert any("Orphan" in e and "not found" in e for e in errors), errors

    def test_summary_counts_each_model_once(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        report = tool.run_checks(spec, config=_config(known_gaps=kind_gap), snapshot=None)
        assert report.model_counts == {"ok": 2, "error": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# §C enum alignment
# ---------------------------------------------------------------------------


class TestEnumChecks:
    def test_spec_member_missing_from_sdk_is_an_error(self, spec: dict[str, Any]) -> None:
        errors = _errors(spec, _config())
        assert any("Kind" in e and "square" in e for e in errors), errors

    def test_unknown_value_constructed_on_open_enum_still_errors(
        self, spec: dict[str, Any]
    ) -> None:
        # OpenStrEnum._missing_ inserts unknown values into _value2member_map_, so a check
        # that constructs the enum (or reads that map) would always pass.
        assert Kind("square").value == "square"
        assert "square" in Kind._value2member_map_
        errors = _errors(spec, _config())
        assert any("Kind" in e and "square" in e for e in errors), errors

    def test_known_enum_gap_suppresses_and_goes_stale(self, spec: dict[str, Any]) -> None:
        assert _errors(spec, _config(known_gaps={("Kind", "square"): "test"})) == []
        errors = _errors(
            spec,
            _config(known_gaps={("Kind", "square"): "test", ("Kind", "oval"): "test"}),
        )
        assert any("stale KNOWN_GAPS" in e and "oval" in e for e in errors), errors

    def test_int_enum_adapter(self, spec: dict[str, Any]) -> None:
        check = tool.EnumCheck(
            ListKind,
            (tool.EnumLocation("#/components/schemas/ListThing/properties/type"),),
            value_map=LIST_KIND_MAP,
        )
        assert _errors(spec, _config(enum_checks=(check,))) == []

        spec["components"]["schemas"]["ListThing"]["properties"]["type"]["enum"].append("team")
        ListKind(42)  # pseudo-member via _missing_ must not count
        errors = _errors(spec, _config(enum_checks=(check,)))
        assert any("ListKind" in e and "team" in e for e in errors), errors

    def test_int_enum_adapter_mapped_value_absent_from_enum(self, spec: dict[str, Any]) -> None:
        class TwoKinds(IntEnum):
            PERSON = 0
            COMPANY = 1

        check = tool.EnumCheck(
            TwoKinds,
            (tool.EnumLocation("#/components/schemas/ListThing/properties/type"),),
            value_map=LIST_KIND_MAP,
        )
        errors = _errors(spec, _config(enum_checks=(check,)))
        assert any("TwoKinds" in e and "opportunity" in e for e in errors), errors

    def test_discriminator_adapter(self, spec: dict[str, Any]) -> None:
        check = tool.EnumCheck(
            NoteKind, (tool.EnumLocation("#/components/schemas/Note", kind="discriminator"),)
        )
        assert _errors(spec, _config(enum_checks=(check,))) == []

        mapping = spec["components"]["schemas"]["Note"]["discriminator"]["mapping"]
        mapping["voice"] = "#/components/schemas/TextNote"
        errors = _errors(spec, _config(enum_checks=(check,)))
        assert any("NoteKind" in e and "voice" in e for e in errors), errors

    def test_const_adapter(self, spec: dict[str, Any]) -> None:
        check = tool.EnumCheck(
            NoteKind,
            (
                tool.EnumLocation("#/components/schemas/TextNote/properties/type", kind="const"),
                tool.EnumLocation("#/components/schemas/ReplyNote/properties/type", kind="const"),
            ),
        )
        assert _errors(spec, _config(enum_checks=(check,))) == []
        spec["components"]["schemas"]["ReplyNote"]["properties"]["type"]["const"] = "comment"
        errors = _errors(spec, _config(enum_checks=(check,)))
        assert any("NoteKind" in e and "comment" in e for e in errors), errors

    def test_param_adapter(self, spec: dict[str, Any]) -> None:
        check = tool.EnumCheck(
            FieldKind, (tool.EnumLocation("GET /v2/widgets fieldTypes", kind="param"),)
        )
        assert _errors(spec, _config(enum_checks=(check,))) == []
        params = spec["paths"]["/v2/widgets"]["get"]["parameters"]
        params[1]["schema"]["items"]["enum"].append("list")
        errors = _errors(spec, _config(enum_checks=(check,)))
        assert any("FieldKind" in e and "list" in e for e in errors), errors

    def test_location_that_stops_resolving_is_an_error(self, spec: dict[str, Any]) -> None:
        check = tool.EnumCheck(
            Kind, (tool.EnumLocation("#/components/schemas/Widget/properties/shape"),)
        )
        errors = _errors(spec, _config(enum_checks=(check,)))
        assert any("shape" in e for e in errors), errors

        check = tool.EnumCheck(
            FieldKind, (tool.EnumLocation("GET /v2/widgets nope", kind="param"),)
        )
        errors = _errors(spec, _config(enum_checks=(check,)))
        assert any("nope" in e for e in errors), errors


# ---------------------------------------------------------------------------
# §D spec-drift snapshot
# ---------------------------------------------------------------------------


def _snapshot_for(spec: dict[str, Any]) -> dict[str, Any]:
    return tool.make_snapshot(spec, url="https://example.invalid/spec.json", sha="abc123")


def _drift(old_spec: dict[str, Any], new_spec: dict[str, Any]) -> list[str]:
    return tool.diff_digest(tool.build_digest(old_spec), tool.build_digest(new_spec))


class TestDrift:
    def test_identical_spec_has_no_drift(self, spec: dict[str, Any]) -> None:
        assert _drift(spec, copy.deepcopy(spec)) == []

    def test_description_only_change_is_not_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        new["components"]["schemas"]["Widget"]["properties"]["name"]["description"] = "changed"
        new["components"]["schemas"]["Base"]["description"] = "changed"
        new["components"]["schemas"]["Widget"]["properties"]["name"]["example"] = "x"
        new["paths"]["/v2/widgets"]["get"]["description"] = "changed"
        new["paths"]["/v2/widgets"]["get"]["summary"] = "changed"
        new["components"]["parameters"]["limit"]["description"] = "changed"
        new["info"]["description"] = "changed"
        assert _drift(spec, new) == []

    def test_property_named_description_is_not_ignored(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        new["components"]["schemas"]["Widget"]["properties"]["description"] = {"type": "string"}
        diff = _drift(spec, new)
        assert any("Widget" in d and "description" in d for d in diff), diff

    def test_new_operation_is_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        new["paths"]["/v2/widgets"]["delete"] = {"responses": {"204": {"description": "gone"}}}
        diff = _drift(spec, new)
        assert any("DELETE /v2/widgets" in d for d in diff), diff

    def test_new_oneof_variant_is_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        new["components"]["schemas"]["Note"]["oneOf"].append(
            {"$ref": "#/components/schemas/Widget"}
        )
        assert any("Note" in d and "oneOf" in d for d in _drift(spec, new))

    def test_max_items_added_is_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        new["components"]["schemas"]["TextNote"]["properties"]["tags"] = {
            "type": "array",
            "items": {"type": "string"},
        }
        old = copy.deepcopy(new)
        new["components"]["schemas"]["TextNote"]["properties"]["tags"]["maxItems"] = 100
        diff = _drift(old, new)
        assert any("maxItems" in d for d in diff), diff

    def test_param_added_is_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        new["paths"]["/v2/widgets"]["get"]["parameters"].append(
            {"name": "cursor", "in": "query", "schema": {"type": "string"}}
        )
        diff = _drift(spec, new)
        assert any("GET /v2/widgets" in d and "cursor" in d for d in diff), diff

    def test_param_reorder_is_not_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        new["paths"]["/v2/widgets"]["get"]["parameters"].reverse()
        assert _drift(spec, new) == []

    def test_enum_member_added_and_required_changed_are_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        new["components"]["schemas"]["Widget"]["properties"]["kind"]["enum"].append("oval")
        new["components"]["schemas"]["Widget"]["required"].append("tags")
        diff = _drift(spec, new)
        assert any("enum" in d for d in diff), diff
        assert any("required" in d for d in diff), diff

    def test_enum_reorder_is_not_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        new["components"]["schemas"]["Widget"]["properties"]["kind"]["enum"].reverse()
        assert _drift(spec, new) == []

    def test_nullable_type_array_change_is_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        new["components"]["schemas"]["Widget"]["properties"]["name"]["type"] = "string"
        assert any("name" in d and "type" in d for d in _drift(spec, new))

    def test_stability_level_change_is_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        del new["paths"]["/v2/widgets"]["get"]["x-stability-level"]
        diff = _drift(spec, new)
        assert any("x-stability-level" in d for d in diff), diff

    def test_response_ref_change_is_drift(self, spec: dict[str, Any]) -> None:
        new = copy.deepcopy(spec)
        content = new["paths"]["/v2/notes/{noteId}"]["get"]["responses"]["200"]["content"]
        content["application/json"]["schema"] = {"$ref": "#/components/schemas/TextNote"}
        assert any("GET /v2/notes/{noteId}" in d for d in _drift(spec, new))

    def test_drift_against_snapshot_fails_run(
        self, spec: dict[str, Any], kind_gap: dict[tuple[str, str], str]
    ) -> None:
        snapshot = _snapshot_for(spec)
        config = _config(known_gaps=kind_gap)
        assert tool.run_checks(spec, config=config, snapshot=snapshot).exit_code == 0
        new = copy.deepcopy(spec)
        new["paths"]["/v2/widgets"]["get"]["parameters"].append(
            {"name": "cursor", "in": "query", "schema": {"type": "string"}}
        )
        report = tool.run_checks(new, config=config, snapshot=snapshot)
        assert report.exit_code == 1
        assert report.drift


class TestMain:
    def test_update_snapshot_round_trip(
        self, tmp_path: Path, kind_gap: dict[tuple[str, str], str]
    ) -> None:
        snap = tmp_path / "snap.json"
        config = _config(known_gaps=kind_gap)
        base_args = ["--offline", str(FIXTURE_SPEC), "--snapshot", str(snap)]

        # Missing snapshot is a usage/setup error, not a silent pass.
        assert tool.main(base_args, config=config) == 2

        # --offline has no upstream sha: it must be given explicitly.
        assert tool.main([*base_args, "--update-snapshot"], config=config) == 2
        assert not snap.exists()

        assert (
            tool.main([*base_args, "--update-snapshot", "--source-sha", "f" * 40], config=config)
            == 0
        )
        data = json.loads(snap.read_text())
        assert data["source"]["sha"] == "f" * 40
        assert data["source"]["x-affinity-api-version"] == "2026-01-01"
        assert tool.main(base_args, config=config) == 0

        # Drift after the update -> exit 1.
        drifted = json.loads(FIXTURE_SPEC.read_text())
        drifted["paths"]["/v2/widgets"]["get"]["x-stability-level"] = "ga"
        drifted_path = tmp_path / "drifted.json"
        drifted_path.write_text(json.dumps(drifted))
        assert (
            tool.main(["--offline", str(drifted_path), "--snapshot", str(snap)], config=config) == 1
        )

    def test_sha_parsed_from_pinned_url(self) -> None:
        sha = "0123456789abcdef0123456789abcdef01234567"
        url = f"https://raw.githubusercontent.com/yaniv-golan/affinity-api-docs/{sha}/docs/v2/openapi.json"
        assert tool.sha_from_url(url) == sha
        assert tool.sha_from_url(tool.OPENAPI_SCHEMA_URL) is None
        assert tool.pinned_url(sha) == url

    def test_reports_spec_version_from_extension(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], kind_gap: Any
    ) -> None:
        snap = tmp_path / "snap.json"
        config = _config(known_gaps=kind_gap)
        args = ["--offline", str(FIXTURE_SPEC), "--snapshot", str(snap)]
        tool.main([*args, "--update-snapshot", "--source-sha", "abc"], config=config)
        capsys.readouterr()
        tool.main(args, config=config)
        assert "2026-01-01" in capsys.readouterr().out

    def test_model_error_exit_code(self, tmp_path: Path) -> None:
        snap = tmp_path / "snap.json"
        config = _config()  # Kind is missing "square" -> error
        args = ["--offline", str(FIXTURE_SPEC), "--snapshot", str(snap)]
        assert tool.main([*args, "--update-snapshot", "--source-sha", "abc"], config=config) == 0
        assert tool.main(args, config=config) == 1


# ---------------------------------------------------------------------------
# Real configuration
# ---------------------------------------------------------------------------


class TestRealConfiguration:
    def test_default_config_is_consistent(self) -> None:
        config = tool.default_config()
        for name in config.mappings:
            assert name in config.models, name
        for name in config.skipped:
            assert name in config.models, name
            assert name not in config.mappings, name
        for key, reason in config.known_gaps.items():
            assert isinstance(key, tuple) and len(key) == 2
            assert reason.strip(), key

    def test_committed_snapshot_records_source(self) -> None:
        data = json.loads(COMMITTED_SNAPSHOT.read_text())
        sha = data["source"]["sha"]
        assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
        assert data["source"]["url"] == tool.pinned_url(sha)
        assert data["digest"]["operations"]
        assert data["digest"]["schemas"]

    def test_green_on_day_one_against_local_upstream_spec(self) -> None:
        upstream = next((p for p in _ALT_UPSTREAM if p.is_file()), None)
        if upstream is None:
            pytest.skip("affinity-api-docs checkout not found next to this repo")
        snapshot = json.loads(COMMITTED_SNAPSHOT.read_text())
        spec = json.loads(upstream.read_text())
        if tool.build_digest(spec) != snapshot["digest"]:
            pytest.skip("local affinity-api-docs is at a different revision than the snapshot")
        report = tool.run_checks(spec, config=tool.default_config(), snapshot=snapshot)
        assert report.errors == []
        assert report.exit_code == 0
