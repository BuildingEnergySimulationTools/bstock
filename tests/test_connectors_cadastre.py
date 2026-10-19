import json
import math

import pytest

from bstock.connectors import cadastre
from bstock.connectors.rnb import RnbClient
from bstock.exceptions import InvalidArgumentError
from fakes import GABARRE, GABARRE_PLOT, GABARRE_POINT

LAT, LON = GABARRE_POINT


def test_point_search_sends_a_geojson_point(fake_session, recorded):
    body = recorded("cadastre/parcelle_point_gabarre.json")
    session = fake_session([body])
    features = cadastre.CadastreClient(session=session).plots(LAT, LON)
    assert [cadastre.plot_id(f) for f in features] == [GABARRE_PLOT]
    [call] = session.calls
    assert call["url"] == cadastre.BASE_URL + "parcelle"
    assert json.loads(call["params"]["geom"]) == {"type": "Point", "coordinates": [LON, LAT]}


def test_radius_search_sends_a_circle(fake_session, recorded):
    body = recorded("cadastre/parcelle_radius20_gabarre.json")
    session = fake_session([body])
    features = cadastre.CadastreClient(session=session).plots(LAT, LON, radius=20)
    assert GABARRE_PLOT in {cadastre.plot_id(f) for f in features}
    geometry = json.loads(session.calls[0]["params"]["geom"])
    [ring] = geometry["coordinates"]
    assert geometry["type"] == "Polygon"
    assert ring[0] == ring[-1]
    dlat = ring[4][1] - LAT  # quarter turn: due north
    assert dlat * math.pi / 180 * cadastre.EARTH_RADIUS == pytest.approx(20)


@pytest.mark.parametrize("radius", [-1, 1001])
def test_invalid_radius_is_rejected(fake_session, radius):
    with pytest.raises(InvalidArgumentError):
        cadastre.CadastreClient(session=fake_session([])).plots(LAT, LON, radius)


def test_feature_without_idu_has_no_plot_id():
    assert cadastre.plot_id({"properties": {}}) is None


def test_plot_buildings_are_read_from_the_rnb(fake_session, recorded):
    session = fake_session([recorded(f"rnb/plot_{GABARRE_PLOT}.json")])
    records = RnbClient(session=session).plot_buildings(GABARRE_PLOT)
    assert records[0]["rnb_id"] == GABARRE
    assert records[0]["bdg_cover_ratio"] == pytest.approx(0.979, abs=1e-3)
    assert session.calls[0]["url"].endswith(f"buildings/plot/{GABARRE_PLOT}/")


def test_unknown_plot_has_no_building(fake_session, fake_response):
    session = fake_session([fake_response(404, {"detail": "Plot unknown"})])
    assert RnbClient(session=session).plot_buildings("64102000ZZ9999") == []


def test_plot_buildings_follow_the_pages(fake_session):
    pages = [
        {"next": "https://rnb.example/p2", "results": [{"rnb_id": "A"}]},
        {"next": None, "results": [{"rnb_id": "B"}]},
    ]
    records = RnbClient(session=fake_session(pages)).plot_buildings(GABARRE_PLOT)
    assert [r["rnb_id"] for r in records] == ["A", "B"]


@pytest.mark.network
def test_real_plot_is_found_at_the_point():
    features = cadastre.CadastreClient().plots(LAT, LON)
    assert [cadastre.plot_id(f) for f in features] == [GABARRE_PLOT]
