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
    park = example.main(
        tmp_path,
        rnb_client=FakeRnbClient(recorded, aliases=example.RNB_IDS),
        bdnb_client=FakeBdnbClient(recorded),
        dpe_client=FakeDpeClient(dpe_lines(recorded)),
    )
    assert (tmp_path / "bayonne.gpkg").exists()
    assert (tmp_path / "park" / "park.json").exists()
    group = park["bdnb-bg-F317-Z6BT-TLT8"]
    assert group.parent.id == "quartier-saint-esprit"
    assert park["BWB9SNCJHWVT"].parent is group
    assert park["5CWS74SZZ7EW"].dwelling_count == 192
    assert park["2E1X4DY5AY9T"].effective("construction_year").source == "dpe"
    assert park["5CWS74SZZ7EW"].effective("footprint_area").source == "computed"
    assert "RNB: 3 buildings created" in capsys.readouterr().out
