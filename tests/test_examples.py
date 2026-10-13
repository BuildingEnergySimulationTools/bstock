import importlib.util
from pathlib import Path

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
