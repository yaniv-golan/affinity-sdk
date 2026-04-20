import httpx
import pytest

from affinity import Affinity
from affinity.filters import F


def _make_client(handler):
    return Affinity(
        api_key="test",
        v1_base_url="https://v1.example",
        v2_base_url="https://v2.example/v2",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )


def test_companies_list_rejects_string_filter():
    client = _make_client(
        lambda r: httpx.Response(200, json={"data": [], "pagination": {}}, request=r)
    )
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            client.companies.list(filter='name =~ "Acme"')
    finally:
        client.close()


def test_companies_list_rejects_filter_expression():
    client = _make_client(
        lambda r: httpx.Response(200, json={"data": [], "pagination": {}}, request=r)
    )
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            client.companies.list(filter=F.field("Industry").contains("Tech"))
    finally:
        client.close()


def test_companies_list_error_hints_at_alternative():
    client = _make_client(
        lambda r: httpx.Response(200, json={"data": [], "pagination": {}}, request=r)
    )
    try:
        with pytest.raises(ValueError) as exc_info:
            client.companies.list(filter='name =~ "Acme"')
        msg = str(exc_info.value)
        assert "search_pages" in msg or "lists.entries" in msg
    finally:
        client.close()


def test_companies_list_still_works_without_filter():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "filter" not in str(request.url)
        return httpx.Response(200, json={"data": [], "pagination": {}}, request=request)

    client = _make_client(handler)
    try:
        result = client.companies.list(limit=10)
        assert result.data == []
    finally:
        client.close()


def test_companies_get_first_rejects_filter():
    client = _make_client(
        lambda r: httpx.Response(200, json={"data": [], "pagination": {}}, request=r)
    )
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            client.companies.get_first(filter='name =~ "Acme"')
    finally:
        client.close()


def test_companies_all_rejects_filter():
    client = _make_client(
        lambda r: httpx.Response(200, json={"data": [], "pagination": {}}, request=r)
    )
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            list(client.companies.all(filter='name =~ "Acme"'))
    finally:
        client.close()


def test_companies_iter_rejects_filter():
    client = _make_client(
        lambda r: httpx.Response(200, json={"data": [], "pagination": {}}, request=r)
    )
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            list(client.companies.iter(filter='name =~ "Acme"'))
    finally:
        client.close()


def test_companies_pages_rejects_filter():
    client = _make_client(
        lambda r: httpx.Response(200, json={"data": [], "pagination": {}}, request=r)
    )
    try:
        with pytest.raises(ValueError, match="does not support server-side filter"):
            list(client.companies.pages(filter='name =~ "Acme"'))
    finally:
        client.close()


def test_companies_pages_still_works_without_filter():
    # Regression: pages() should NOT pass filter=None through to list()
    def handler(request: httpx.Request) -> httpx.Response:
        assert "filter" not in str(request.url)
        return httpx.Response(200, json={"data": [], "pagination": {}}, request=request)

    client = _make_client(handler)
    try:
        pages_list = list(client.companies.pages(limit=10))
        assert len(pages_list) == 1
        assert pages_list[0].data == []
    finally:
        client.close()
