"""``batch_get`` with interaction dates and field filters, in fewer calls.

``get(id, with_interaction_dates=True, field_ids=...|field_types=...)`` makes two calls per id: V1
``/organizations|persons/{id}`` for the dates and V2 ``/companies|persons/{id}`` for the fields.
For many ids the V2 half goes as one ``/companies|persons?ids=...`` call per 100 ids instead, and
each record is merged exactly as ``get`` merges it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, TypeVar

from ..exceptions import AffinityError, NotFoundError

IdT = TypeVar("IdT", bound=int)

V2_IDS_PER_CALL = 100  # V2 list endpoints return at most 100 records per page


async def batch_get_with_dates(
    client: Any,
    ids: Sequence[IdT],
    *,
    v1_path: str,
    v2_path: str,
    kind: str,
    v1_params: dict[str, Any],
    v2_params: dict[str, Any],
    max_concurrent: int,
    on_error: Literal["raise", "skip"],
    get_one: Callable[[IdT], Awaitable[Any]],
    build: Callable[[dict[str, Any]], Any],
) -> dict[IdT, Any]:
    """Records for ``ids`` (unique, in order): the V2 fields of each merged with its V1
    interaction dates and interactions. ``get_one`` (the plain per-id ``get``) re-reads the ids
    of a V2 call that failed under ``on_error="skip"``, so one bad id doesn't drop 99 others.
    """
    fields_by_id: dict[int, dict[str, Any]] = {}
    retry_one_by_one: set[int] = set()
    for start in range(0, len(ids), V2_IDS_PER_CALL):
        chunk = list(ids[start : start + V2_IDS_PER_CALL])
        try:
            data = await client.get(
                v2_path,
                params={**v2_params, "ids": [int(i) for i in chunk], "limit": V2_IDS_PER_CALL},
            )
            while True:
                for item in data.get("data", []):
                    fields_by_id[int(item["id"])] = item
                next_url = (data.get("pagination") or {}).get("nextUrl")
                if not next_url:
                    break
                data = await client.get_url(next_url)
        except AffinityError:
            if on_error == "raise":
                raise
            retry_one_by_one.update(int(i) for i in chunk)

    async def dates_of(record_id: IdT) -> tuple[IdT, Any | None]:
        try:
            if int(record_id) in retry_one_by_one:
                return record_id, await get_one(record_id)
            fields = fields_by_id.get(int(record_id))
            if fields is None:
                raise NotFoundError(f"{kind} {int(record_id)} not found", status_code=404)
            dates = await client.get(f"{v1_path}/{int(record_id)}", params=v1_params, v1=True)
            merged = dict(fields)
            merged["interaction_dates"] = dates.get("interaction_dates")
            merged["interactions"] = dates.get("interactions")
            return record_id, build(merged)
        except AffinityError:
            if on_error == "raise":
                raise
            return record_id, None

    results: dict[IdT, Any] = {}
    for start in range(0, len(ids), max_concurrent):
        tasks = [asyncio.create_task(dates_of(i)) for i in ids[start : start + max_concurrent]]
        try:
            for done in asyncio.as_completed(tasks):
                record_id, record = await done
                if record is not None:
                    results[record_id] = record
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    return results
