import importlib.util
from pathlib import Path

from fakes import FakeBdnbClient, FakeDpeClient, FakeRnbClient, dpe_lines

EXAMPLES = Path(__file__).parents[1] / "examples"


def load_example(name):
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bayonne_example_runs(tmp_path, capsys):
    park = load_example("bayonne").main(tmp_path)
    assert (tmp_path / "park" / "park.json").exists()
    assert (tmp_path / "park" / "timeseries" / "PDL_123.parquet").exists()
    assert type(park["A1B2C3D4E5F6"]).__name__ == "BayonneBuilding"
    assert "Reloaded 2 buildings" in capsys.readouterr().out


def test_connectors_example_runs_with_fake_clients(tmp_path, capsys, recorded):
    example = load_example("connectors")
    addresses = {
        address: f"rnb/address_search_{site_id.replace('-', '_')}.json"
        for site_id, (_name, address) in example.SITES.items()
    }
    park = example.main(
        tmp_path,
        rnb_client=FakeRnbClient(recorded, addresses=addresses),
        bdnb_client=FakeBdnbClient(recorded),
        dpe_client=FakeDpeClient(dpe_lines(recorded)),
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
