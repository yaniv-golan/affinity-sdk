"""SDK fixes from the must-fix review: empty id filters, paging guards, cache keys, hooks,
field writes, merges, list caches, ids and enums, model aliases, and filter coercion."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from affinity import Affinity
from affinity.clients.http import (
    AsyncHTTPClient,
    ClientConfig,
    HTTPClient,
    _safe_follow_url,
)
from affinity.compare import compare_values
from affinity.exceptions import AffinityError, ConfigurationError, UnsafeUrlError
from affinity.hooks import HookEvent
from affinity.models import pagination as pagination_module
from affinity.models.entities import (
    Company,
    FieldValues,
    ListEntryWithEntity,
    Location,
    Opportunity,
    OpportunityCreate,
    Person,
)
from affinity.models.pagination import (
    AsyncPageIterator,
    PageIterator,
    PaginatedResponse,
    PaginationInfo,
)
from affinity.models.types import (
    CompanyId,
    EntityType,
    FieldId,
    FieldValueType,
    ListEntryId,
    ListId,
    ListType,
    PersonId,
    SavedViewId,
)
from affinity.services import tasks as tasks_module
from affinity.services._field_updates import _field_value_payload
from affinity.services.companies import AsyncCompanyService, CompanyService
from affinity.services.lists import ListService
from affinity.services.opportunities import AsyncOpportunityService, OpportunityService
from affinity.services.org import AsyncUserService, TeamService, UserService
from affinity.services.persons import AsyncPersonService, PersonService
from affinity.services.reminders import AsyncReminderService, ReminderService
from affinity.services.tasks import AsyncTaskService, TaskService
from affinity.services.webhooks import AsyncWebhookService, WebhookService

V1 = "https://v1.example"
V2 = "https://v2.example/v2"

Handler = Callable[[httpx.Request], httpx.Response]


def _sync(handler: Handler, **config: Any) -> HTTPClient:
    return HTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url=V1,
            v2_base_url=V2,
            max_retries=0,
            transport=httpx.MockTransport(handler),
            **config,
        )
    )


def _async(handler: Handler, **config: Any) -> AsyncHTTPClient:
    return AsyncHTTPClient(
        ClientConfig(
            api_key="k",
            v1_base_url=V1,
            v2_base_url=V2,
            max_retries=0,
            async_transport=httpx.MockTransport(handler),
            **config,
        )
    )


def _recorder(
    body: dict[str, Any] | None = None,
) -> tuple[list[httpx.Request], Handler]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body if body is not None else {"data": []})

    return seen, handler


# --- 1. empty id lists and limit <= 0 --------------------------------------------------------


def test_empty_ids_return_an_empty_page_without_a_request() -> None:
    seen, handler = _recorder()
    http = _sync(handler)
    try:
        for page in (
            CompanyService(http).list(ids=[]),
            PersonService(http).list(ids=[]),
            OpportunityService(http).list(ids=[]),
            WebhookService(http).list(ids=[]),
            TeamService(http).list(ids=[]),
            UserService(http).list(ids=[]),
            ReminderService(http).list(ids=[]),
        ):
            assert page.data == []
            assert page.has_next is False
        assert WebhookService(http).list(ids=[], total_count=True).total_count == 0
        assert seen == []
        # None still means "no filter"
        CompanyService(http).list(ids=None)
        assert len(seen) == 1
    finally:
        http.close()


async def test_async_empty_ids_return_an_empty_page_without_a_request() -> None:
    seen, handler = _recorder()
    http = _async(handler)
    try:
        for page in (
            await AsyncCompanyService(http).list(ids=[]),
            await AsyncPersonService(http).list(ids=[]),
            await AsyncOpportunityService(http).list(ids=[]),
            await AsyncWebhookService(http).list(ids=[]),
            await AsyncUserService(http).list(ids=[]),
            await AsyncReminderService(http).list(ids=[]),
        ):
            assert page.data == []
        assert seen == []
    finally:
        await http.close()


def test_limit_below_one_raises_instead_of_being_dropped() -> None:
    seen, handler = _recorder()
    http = _sync(handler)
    try:
        for call in (
            lambda: CompanyService(http).list(limit=0),
            lambda: PersonService(http).list(limit=0),
            lambda: OpportunityService(http).list(limit=-1),
            lambda: CompanyService(http).get_lists(CompanyId(12345), limit=0),
            lambda: ListService(http).entries(ListId(1)).from_saved_view(SavedViewId(2), limit=0),
        ):
            with pytest.raises(ValueError, match="limit"):
                call()
        assert seen == []
        CompanyService(http).list(limit=5)
        assert seen[0].url.params["limit"] == "5"
    finally:
        http.close()


# --- 2. get_all_pages at max_pages -------------------------------------------------------------


def test_get_all_pages_raises_and_caches_nothing_when_pages_remain() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        n = len(calls)
        return httpx.Response(
            200,
            json={
                "data": [{"id": n}],
                "pagination": {"nextUrl": f"{V2}/lists/1/fields?cursor={n + 1}"},
            },
        )

    http = _sync(handler, enable_cache=True)
    try:
        with pytest.raises(AffinityError, match="more pages after 3"):
            http.get_all_pages("/lists/1/fields", cache_key="things", max_pages=3)
        assert len(calls) == 3
        # The merged (cut-off) result was not cached: the first page is all there is
        cached = http.cache.get_entry(f"things{http._cache_suffix}") if http.cache else None
        assert cached is None or cached.value["pagination"]["nextUrl"] is not None
    finally:
        http.close()


async def test_async_get_all_pages_raises_when_pages_remain() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [], "pagination": {"nextUrl": f"{V2}/lists/1/fields?cursor=next"}}
        )

    http = _async(handler)
    try:
        with pytest.raises(AffinityError, match="more pages"):
            await http.get_all_pages("/lists/1/fields", max_pages=2)
    finally:
        await http.close()


# --- 3. followed URLs: exact (host, port) pairs ------------------------------------------------


def test_safe_follow_url_requires_an_exact_host_port_pair() -> None:
    v1, v2 = "https://a.example:8001", "https://b.example:8002/v2"
    assert _safe_follow_url("https://a.example:8001/x", v1_base_url=v1, v2_base_url=v2)
    assert _safe_follow_url("https://b.example:8002/v2/x", v1_base_url=v1, v2_base_url=v2)
    for url in ("https://a.example:8002/v2/x", "https://b.example:8001/x"):
        with pytest.raises(UnsafeUrlError):
            _safe_follow_url(url, v1_base_url=v1, v2_base_url=v2)


# --- 4. "pagination": null in association paging ---------------------------------------------


def test_associated_people_tolerates_null_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": 1}], "pagination": None})

    http = _sync(handler)
    try:
        svc = CompanyService(http)
        monkeypatch.setattr(svc, "get_associated_person_ids", lambda *_a, **_k: [PersonId(1)])
        people = svc.get_associated_people(CompanyId(12345))
        assert [p.id for p in people] == [1]
    finally:
        http.close()


# --- 5. tasks.wait never sleeps past the timeout -----------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_task_wait_caps_the_sleep_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(tasks_module, "time", clock)
    polls: list[float] = []
    svc = TaskService(None)  # type: ignore[arg-type]

    def get(_url: str) -> Any:
        polls.append(clock.now)
        return SimpleNamespace(status="in_progress")

    monkeypatch.setattr(svc, "get", get)
    with pytest.raises(AffinityError, match="did not complete within 5"):
        svc.wait("https://v2.example/v2/tasks/1", timeout=5, poll_interval=4)
    assert clock.now == pytest.approx(5.0)
    assert polls[-1] == pytest.approx(5.0)  # a final poll at the deadline
    assert max(clock.sleeps) <= 4 * 1.1


async def test_async_task_wait_caps_the_sleep_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(tasks_module.time, "monotonic", clock.monotonic)

    async def fake_sleep(seconds: float) -> None:
        clock.sleep(seconds)

    monkeypatch.setattr(tasks_module.asyncio, "sleep", fake_sleep)
    svc = AsyncTaskService(None)  # type: ignore[arg-type]

    async def get(_url: str) -> Any:
        return SimpleNamespace(status="in_progress")

    monkeypatch.setattr(svc, "get", get)
    with pytest.raises(AffinityError):
        await svc.wait("https://v2.example/v2/tasks/1", timeout=3, poll_interval=10)
    assert clock.sleeps[0] == pytest.approx(3.0)


# --- 6. datetime and bool field writes ---------------------------------------------------------


def test_field_write_datetimes_are_whole_second_utc_z() -> None:
    aware = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone(timedelta(hours=2)))
    assert _field_value_payload(aware) == {"type": "datetime", "data": "2026-01-02T01:04:05Z"}
    naive = datetime(2026, 1, 2, 3, 4, 5, 999)
    assert _field_value_payload(naive)["data"] == "2026-01-02T03:04:05Z"


def test_field_write_rejects_bool() -> None:
    with pytest.raises(TypeError, match="no boolean field type"):
        _field_value_payload(True)
    with pytest.raises(TypeError):
        _field_value_payload(False, "text")


# --- 7. async hooks on the sync client ---------------------------------------------------------


def test_sync_client_rejects_async_hooks_at_construction() -> None:
    async def on_event(_event: HookEvent) -> None:
        return None

    class AsyncCallable:
        async def __call__(self, _info: Any) -> None:
            return None

    with pytest.raises(ConfigurationError, match="on_event"):
        Affinity(api_key="k", on_event=on_event)
    with pytest.raises(ConfigurationError, match="on_request"):
        Affinity(api_key="k", on_request=AsyncCallable())  # type: ignore[arg-type]
    Affinity(api_key="k", on_event=lambda _e: None).close()


def test_awaitable_on_event_is_not_swallowed_by_the_default_policy() -> None:
    async def on_event(_event: HookEvent) -> None:
        return None

    http = _sync(lambda _r: httpx.Response(200, json={}), on_event=on_event)
    try:
        with pytest.raises(ConfigurationError, match="synchronous `on_event`"):
            http.get("/auth/whoami")
    finally:
        http.close()


# --- 9. cache key includes the version sent ----------------------------------------------------


def test_cache_key_includes_the_per_operation_version() -> None:
    sent: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("X-Affinity-Api-Version"))
        return httpx.Response(200, json={"data": [], "pagination": {"nextUrl": None}})

    http = _sync(handler, enable_cache=True)
    try:
        http.get("/lists/1/fields", cache_key="fields")
        http.get("/lists/1/fields", cache_key="fields")  # hit
        http.get("/lists/1/fields", cache_key="fields", min_api_version="2026-07-15")
        http.get("/lists/1/fields", cache_key="fields", min_api_version="2026-07-15")  # hit
        assert sent == [None, "2026-07-15"]
    finally:
        http.close()


# --- 10. merge without taskUrl -----------------------------------------------------------------


def test_merge_without_task_url_raises() -> None:
    _seen, handler = _recorder({})
    http = _sync(handler)
    try:
        with pytest.raises(AffinityError, match="no taskUrl"):
            CompanyService(http).merge(CompanyId(1), CompanyId(2))
        with pytest.raises(AffinityError, match="no taskUrl"):
            PersonService(http).merge(PersonId(1), PersonId(2))
    finally:
        http.close()
    ok = _sync(lambda _r: httpx.Response(200, json={"taskUrl": f"{V2}/tasks/company-merges/1"}))
    try:
        assert CompanyService(ok).merge(CompanyId(1), CompanyId(2)).endswith("/1")
    finally:
        ok.close()


async def test_async_merge_without_task_url_raises() -> None:
    _seen, handler = _recorder({"taskUrl": ""})
    http = _async(handler)
    try:
        with pytest.raises(AffinityError):
            await AsyncCompanyService(http).merge(CompanyId(1), CompanyId(2))
        with pytest.raises(AffinityError):
            await AsyncPersonService(http).merge(PersonId(1), PersonId(2))
    finally:
        await http.close()


# --- 11. resolve does not remember a miss ------------------------------------------------------


def test_resolve_does_not_cache_a_miss() -> None:
    lists: list[dict[str, Any]] = []
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"data": lists, "pagination": {"nextUrl": None}})

    http = _sync(handler)
    try:
        svc = ListService(http)
        assert svc.resolve(name="Deals") is None
        lists.append({"id": 7, "name": "Deals", "type": 8, "isPublic": True, "ownerId": 1})
        found = svc.resolve(name="Deals")
        assert found is not None and found.id == ListId(7)
        n = len(calls)
        assert svc.resolve(name="Deals") is not None  # a match is still cached
        assert len(calls) == n
    finally:
        http.close()


# --- 12. list size cache dropped on row writes -------------------------------------------------


def test_get_size_is_refetched_after_entry_add_delete_and_opportunity_create() -> None:
    size_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal size_reads
        path = request.url.path
        if request.method == "GET" and path == "/lists/100":
            size_reads += 1
            return httpx.Response(
                200,
                json={
                    "id": 100,
                    "name": "Deals",
                    "type": 8,
                    "public": False,
                    "owner_id": 1,
                    "list_size": size_reads,
                },
            )
        if request.method == "POST" and path == "/lists/100/list-entries":
            return httpx.Response(
                200,
                json={
                    "id": 5,
                    "list_id": 100,
                    "entity_id": 12345,
                    "entity_type": 1,
                    "created_at": "2026-01-01T00:00:00Z",
                },
            )
        if request.method == "DELETE":
            return httpx.Response(200, json={"success": True})
        if request.method == "POST" and path == "/opportunities":
            return httpx.Response(200, json={"id": 9, "name": "Acme Corp deal", "list_id": 100})
        return httpx.Response(404, json={})

    http = _sync(handler)
    try:
        lists = ListService(http)
        assert lists.get_size(ListId(100)) == 1
        assert lists.get_size(ListId(100)) == 1  # cached
        lists.entries(ListId(100)).add_company(CompanyId(12345))
        assert lists.get_size(ListId(100)) == 2
        lists.entries(ListId(100)).delete(ListEntryId(5))
        assert lists.get_size(ListId(100)) == 3
        OpportunityService(http).create(
            OpportunityCreate(name="Acme Corp deal", list_id=ListId(100))
        )
        # a separate ListService on the same client sees the dropped entry too
        assert ListService(http).get_size(ListId(100)) == 4
    finally:
        http.close()


# --- 13 / N2. FieldId(True) --------------------------------------------------------------------


def test_field_id_rejects_bool() -> None:
    for value in (True, False):
        with pytest.raises(ValueError):
            FieldId(value)
    assert FieldId(1) == "field-1"
    assert FieldId("field-1") == 1  # documented mixed equality is kept


# --- 19 / N1. open enums and bools / floats ----------------------------------------------------


def test_open_int_enum_rejects_fractional_floats_without_poisoning() -> None:
    with pytest.raises(ValueError):
        ListType(1.9)
    with pytest.raises(ValueError):
        EntityType(0.5)
    assert ListType(1) is ListType.COMPANY
    assert ListType("1") is ListType.COMPANY  # a digit string finds the real member
    assert ListType(1) is ListType.COMPANY
    unknown = ListType(4242)
    assert unknown.name == "UNKNOWN_4242"
    assert ListType("4242") is unknown


def test_field_value_type_rejects_bool() -> None:
    for value in (True, False):
        with pytest.raises(ValueError):
            FieldValueType(value)
    assert FieldValueType(1) is FieldValueType.COMPANY
    assert FieldValueType(0) is FieldValueType.PERSON


# --- 14 / 15. model aliases --------------------------------------------------------------------


def test_person_and_opportunity_company_id_aliases() -> None:
    person = Person.model_validate(
        {"id": 1, "companyIds": [10, 11], "current_organization_ids": [10]}
    )
    assert person.company_ids == [10, 11]
    assert person.current_company_ids == [10]
    assert Person.model_validate({"id": 1, "currentOrganizationIds": [3]}).current_company_ids == [
        3
    ]
    opp = Opportunity.model_validate({"id": 2, "name": "Acme Corp deal", "companyIds": [10]})
    assert opp.company_ids == [10]


def test_location_reads_street_address() -> None:
    assert Location.model_validate({"streetAddress": "1 Main St"}).street_address == "1 Main St"
    assert Location.model_validate({"street_address": "1 Main St"}).street_address == "1 Main St"


# --- 17. FieldValues.get id forms --------------------------------------------------------------


def test_field_values_get_accepts_every_id_form() -> None:
    fields = FieldValues.model_validate([{"id": "field-123", "value": {"data": "x"}}])
    for key in ("field-123", "123", 123, FieldId(123), " 123 "):
        assert fields.get(key) is not None, key
    assert fields.get_value(123) == "x"
    assert fields.get(True) is None  # type: ignore[arg-type]
    digits = FieldValues(requested=True, data={"456": {"id": "456", "value": {"data": 1}}})
    assert digits.get("field-456") is not None
    assert digits.get(FieldId(456)) is not None


# --- 18. V2 list entries use `type` ------------------------------------------------------------


def test_list_entry_with_entity_uses_type_to_pick_the_model() -> None:
    base = {"id": 1, "listId": 2, "createdAt": "2026-01-01T00:00:00Z"}
    opp = ListEntryWithEntity.model_validate(
        {**base, "type": "opportunity", "entity": {"id": 3, "name": "Acme Corp deal"}}
    )
    assert isinstance(opp.entity, Opportunity)
    company = ListEntryWithEntity.model_validate(
        {**base, "type": "company", "entity": {"id": 3, "name": "Acme Corp"}}
    )
    assert isinstance(company.entity, Company)
    person = ListEntryWithEntity.model_validate(
        {**base, "type": "person", "entity": {"id": 4, "firstName": "Jane"}}
    )
    assert isinstance(person.entity, Person)


# --- 20. `in` / has_any / has_all coerce as `eq` does ------------------------------------------


def test_in_has_any_has_all_coerce_like_eq() -> None:
    assert compare_values(5, "5", "eq")
    assert compare_values(5, ["5", "6"], "in")
    assert compare_values([5, 7], ["5"], "in")
    assert compare_values([5, 7], ["7"], "has_any")
    assert compare_values([5, 7], ["5", "7"], "has_all")
    assert not compare_values([5, 7], ["5", "8"], "has_all")
    assert compare_values(["5"], 5, "eq")
    assert not compare_values(["5"], 5, "neq")
    # `flag = true` still matches a real True, in `eq` and in `in`
    assert compare_values(True, "true", "eq")
    assert compare_values(True, ["true"], "in")
    assert not compare_values(False, ["true"], "in")
    assert not compare_values(None, ["None"], "in")


# --- 21. page iterators that can't progress ----------------------------------------------------


def _empty(cursor: str | None) -> PaginatedResponse[int]:
    return PaginatedResponse[int](data=[], pagination=PaginationInfo(nextUrl=cursor))


def test_page_iterator_stops_on_a_cursor_cycle() -> None:
    calls: list[str | None] = []

    def fetch(cursor: str | None) -> PaginatedResponse[int]:
        calls.append(cursor)
        return _empty("b" if cursor == "a" else "a")

    assert list(PageIterator(fetch)) == []
    assert len(calls) <= 3
    calls.clear()
    assert list(PageIterator(fetch).pages()) == []
    assert len(calls) <= 3


def test_page_iterator_caps_a_run_of_empty_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pagination_module, "_MAX_EMPTY_PAGES", 5)
    count = 0

    def fetch(_cursor: str | None) -> PaginatedResponse[int]:
        nonlocal count
        count += 1
        return _empty(f"c{count}")

    with pytest.warns(RuntimeWarning, match="empty pages"):
        assert list(PageIterator(fetch)) == []
    assert count == 5


def test_page_iterator_keeps_following_data_and_short_empty_runs() -> None:
    pages = {
        None: PaginatedResponse[int](data=[1], pagination=PaginationInfo(nextUrl="p2")),
        "p2": _empty("p3"),
        "p3": PaginatedResponse[int](data=[2], pagination=PaginationInfo(nextUrl=None)),
    }
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert list(PageIterator(lambda c: pages[c])) == [1, 2]
        assert [p.data for p in PageIterator(lambda c: pages[c]).pages()] == [[1], [2]]


async def test_async_page_iterator_stops_on_a_cursor_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch(cursor: str | None) -> PaginatedResponse[int]:
        return _empty("b" if cursor == "a" else "a")

    assert [x async for x in AsyncPageIterator(fetch)] == []
    assert [p async for p in AsyncPageIterator(fetch).pages()] == []

    monkeypatch.setattr(pagination_module, "_MAX_EMPTY_PAGES", 3)
    count = 0

    async def endless(_cursor: str | None) -> PaginatedResponse[int]:
        nonlocal count
        count += 1
        return _empty(f"c{count}")

    with pytest.warns(RuntimeWarning):
        assert [x async for x in AsyncPageIterator(endless)] == []
    assert count == 3
