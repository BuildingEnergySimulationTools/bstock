import importlib.util
import runpy
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from bstock.connectors import bdnb, dpe, rnb
from bstock.exceptions import NoDataWarning
from fakes import FakeBdnbClient, FakeDpeClient, FakeRnbClient, dpe_lines

EXAMPLES = Path(__file__).parents[1] / "examples"


def load_example(name):
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_interactive(name, output, monkeypatch):
    """Run ``examples/<name>_interactive.py`` with its ``OUTPUT`` under ``output``."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(output))
    return runpy.run_path(str(EXAMPLES / f"{name}_interactive.py"))


def fake_clients(recorded):
    """Return fake RNB, BDNB and DPE clients serving the responses recorded for the examples."""
    addresses = {
        address: f"rnb/address_search_{site_id.replace('-', '_')}.json"
        for site_id, (_name, address) in load_example("connectors").SITES.items()
    }
    return (
        FakeRnbClient(recorded, addresses=addresses),
        FakeBdnbClient(recorded),
        FakeDpeClient(dpe_lines(recorded)),
    )


POWER_IGNORED = "'PDL_123' has no 'energy' or 'index' channel"
"""The Bayonne electricity meter measures power, which the consumption does not integrate."""


def test_bayonne_example_runs(tmp_path, capsys):
    with pytest.warns(NoDataWarning, match=POWER_IGNORED):
        park = load_example("bayonne").main(tmp_path)
    assert (tmp_path / "park" / "park.json").exists()
    assert (tmp_path / "park" / "timeseries" / "PDL_123.parquet").exists()
    assert type(park["A1B2C3D4E5F6"]).__name__ == "BayonneBuilding"
    assert "Reloaded 2 buildings" in capsys.readouterr().out


def test_connectors_example_runs_with_fake_clients(tmp_path, capsys, recorded):
    rnb_client, bdnb_client, dpe_client = fake_clients(recorded)
    park = load_example("connectors").main(
        tmp_path, rnb_client=rnb_client, bdnb_client=bdnb_client, dpe_client=dpe_client
    )
    assert (tmp_path / "nobatek.gpkg").exists()
    assert (tmp_path / "park" / "park.json").exists()
    anglet, bordeaux = park["nobatek-anglet"], park["nobatek-bordeaux"]
    assert {b.id for b in anglet.iter_buildings()} == {
        "GJ2P6KGVQGAA",
        "QEQC8WX97T4B",
        "8PDAQ6GZAHZ3",
    }
    group = park["bdnb-bg-TCE9-WLPK-ZQTR"]
    assert group.parent is anglet
    assert park["GJ2P6KGVQGAA"].parent is group
    assert park["GJ2P6KGVQGAA"].provider_id("construction_year") == group.id
    assert park["QEQC8WX97T4B"].parent is anglet  # not in the BDNB
    assert [b.id for b in bordeaux.iter_buildings()] == ["TRKDH5MARXP4"]
    assert park["TRKDH5MARXP4"].bdnb_batiment_groupe_id == "bdnb-bg-N7QB-BASD-YD69"
    assert park["TRKDH5MARXP4"].effective("footprint_area").source == "computed"
    assert bordeaux.effective("address").source == "nobatek"
    assert "Nobatek Anglet: 3 RNB building(s)" in capsys.readouterr().out


def test_bayonne_interactive_example_matches_the_script(tmp_path, monkeypatch):
    with pytest.warns(NoDataWarning, match=POWER_IGNORED):
        park = load_example("bayonne").main(tmp_path)
    with pytest.warns(NoDataWarning, match=POWER_IGNORED):
        namespace = run_interactive("bayonne", tmp_path / "interactive", monkeypatch)
    assert namespace["reloaded"] == namespace["park"]
    pd.testing.assert_frame_equal(
        namespace["park"].to_dataframe(provenance=True), park.to_dataframe(provenance=True)
    )


def test_connectors_interactive_example_matches_the_script(tmp_path, monkeypatch, recorded):
    script_clients = fake_clients(recorded)
    park = load_example("connectors").main(
        tmp_path,
        rnb_client=script_clients[0],
        bdnb_client=script_clients[1],
        dpe_client=script_clients[2],
    )
    rnb_client, bdnb_client, dpe_client = fake_clients(recorded)
    monkeypatch.setattr(rnb, "RnbClient", lambda: rnb_client)
    monkeypatch.setattr(bdnb, "BdnbClient", lambda: bdnb_client)
    monkeypatch.setattr(dpe, "DpeClient", lambda: dpe_client)
    namespace = run_interactive("connectors", tmp_path / "interactive", monkeypatch)
    pd.testing.assert_frame_equal(
        namespace["park"].to_dataframe(provenance=True), park.to_dataframe(provenance=True)
    )


requires_plotly = pytest.mark.skipif(
    importlib.util.find_spec("plotly") is None, reason="needs the [plot] extra"
)


@requires_plotly
def test_plots_example_writes_every_figure(tmp_path, capsys):
    park = load_example("plots").main(tmp_path)
    assert len(list(tmp_path.glob("*.html"))) == 9
    assert len(list(park.iter_buildings())) == 6
    assert "Wrote 9 figures" in capsys.readouterr().out


@requires_plotly
def test_plots_interactive_example_matches_the_script(tmp_path, monkeypatch):
    from plotly.basedatatypes import BaseFigure

    shown = []
    monkeypatch.setattr(BaseFigure, "show", lambda figure, *args, **kwargs: shown.append(figure))
    park = load_example("plots").main(tmp_path)
    namespace = run_interactive("plots", tmp_path / "interactive", monkeypatch)
    assert len(shown) == 9
    pd.testing.assert_frame_equal(
        namespace["park"].to_dataframe(provenance=True), park.to_dataframe(provenance=True)
    )
