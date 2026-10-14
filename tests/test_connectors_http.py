import sys

import pytest
import requests

from bstock.connectors._http import HttpClient
from bstock.exceptions import ConnectorError, MissingDependencyError

BASE = "https://rnb-api.beta.gouv.fr/api/alpha/"
ADEME_BUDGET = (
    "Le temps de traitement cumulé de vos requêtes dépasse le budget alloué. Veuillez patienter."
)


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client_for(fake_session, sleeps):
    def make(*responses, **kwargs):
        session = fake_session(responses)
        client = HttpClient(BASE, session=session, sleep=sleeps.append, **kwargs)
        return client, session

    return make


def test_get_json_joins_path_and_sends_user_agent(client_for, recorded):
    body = recorded("rnb/building_6X3K8HHY9FFC.json")
    client, session = client_for(body, headers={"X-Key": "k"}, timeout=5)
    assert client.get_json("buildings/6X3K8HHY9FFC/") == body
    call = session.calls[0]
    assert call["url"] == BASE + "buildings/6X3K8HHY9FFC/"
    assert call["headers"]["User-Agent"].startswith("bstock/")
    assert call["headers"]["X-Key"] == "k"
    assert call["timeout"] == 5


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_status_is_retried_with_backoff(client_for, fake_response, sleeps, status):
    client, session = client_for(fake_response(status, {}), fake_response(status, {}), {"ok": 1})
    assert client.get_json("x/") == {"ok": 1}
    assert len(session.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_ademe_budget_message_is_retried(client_for, fake_response, sleeps):
    client, _ = client_for(fake_response(400, {"message": ADEME_BUDGET}), {"ok": 1})
    assert client.get_json("x/") == {"ok": 1}
    assert len(sleeps) == 1


def test_retry_after_header_sets_the_wait(client_for, fake_response, sleeps):
    client, _ = client_for(fake_response(429, {}, headers={"Retry-After": "7"}), {"ok": 1})
    client.get_json("x/")
    assert sleeps == [7.0]


def test_network_error_is_retried(client_for, sleeps):
    client, _ = client_for(requests.ConnectionError("down"), {"ok": 1})
    assert client.get_json("x/") == {"ok": 1}
    assert len(sleeps) == 1


def test_exhausted_retries_raise_connector_error(client_for, fake_response):
    client, session = client_for(*[fake_response(503, "busy")] * 3, max_retries=2)
    with pytest.raises(ConnectorError, match="after 3 attempts"):
        client.get_json("x/")
    assert len(session.calls) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_error_raises_without_retry(client_for, fake_response, sleeps, status):
    client, _ = client_for(fake_response(status, {"detail": "Pas trouvé."}))
    with pytest.raises(ConnectorError, match=f"HTTP {status}"):
        client.get_json("x/")
    assert sleeps == []


def test_not_found_can_return_none(client_for, fake_response):
    client, _ = client_for(fake_response(404, {"detail": "Pas trouvé."}))
    assert client.get_json("buildings/XXXXXXXXXXXX/", not_found_ok=True) is None


def test_non_json_body_raises_connector_error(client_for):
    client, _ = client_for("<html>")
    with pytest.raises(ConnectorError, match="JSON"):
        client.get_json("x/")


def test_pagination_follows_next(client_for):
    pages = [
        {"next": BASE + "buildings/?cursor=2", "results": [1, 2]},
        {"next": BASE + "buildings/?cursor=3", "results": [3]},
        {"next": None, "results": [4]},
    ]
    client, session = client_for(*pages)
    assert list(client.paginate("buildings/", {"insee_code": "64102"})) == [1, 2, 3, 4]
    assert session.calls[0]["params"] == {"insee_code": "64102"}
    assert session.calls[1] == {**session.calls[1], "url": pages[0]["next"], "params": None}


def test_pagination_of_recorded_rnb_listing(client_for, recorded):
    page = recorded("rnb/list_insee_64102.json")
    client, _ = client_for({**page, "next": None})
    assert len(list(client.paginate("buildings/", {"insee_code": "64102"}))) == len(page["results"])


def test_pagination_can_stop_after_max_pages(client_for):
    client, session = client_for({"next": BASE + "p2", "results": [1]})
    assert list(client.paginate("buildings/", max_pages=1)) == [1]
    assert len(session.calls) == 1


def test_page_without_results_raises(client_for):
    client, _ = client_for({"detail": "?"})
    with pytest.raises(ConnectorError, match="results"):
        list(client.paginate("buildings/"))


def test_missing_requests_raises_missing_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", None)  # makes the import fail
    with pytest.raises(MissingDependencyError, match=r"bstock\[connectors\]"):
        HttpClient(BASE)


@pytest.mark.network
def test_real_rnb_building_is_fetched():
    body = HttpClient(BASE).get_json("buildings/6X3K8HHY9FFC/")
    assert body["rnb_id"] == "6X3K8HHY9FFC"
