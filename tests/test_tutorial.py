import importlib.util
from pathlib import Path

import pytest

TUTORIAL = Path(__file__).parents[1] / "tutorial" / "bstock_tutorial.ipynb"

requires_jupyter = pytest.mark.skipif(
    any(importlib.util.find_spec(m) is None for m in ("nbclient", "nbformat", "ipykernel")),
    reason="needs the [tutorial] extra",
)


def _code_cells():
    nbformat = pytest.importorskip("nbformat")
    notebook = nbformat.read(TUTORIAL, as_version=4)
    return [c for c in notebook.cells if c.cell_type == "code"]


def test_tutorial_is_saved_after_a_full_run():
    code_cells = _code_cells()
    assert code_cells
    assert [c.execution_count for c in code_cells] == list(range(1, len(code_cells) + 1))
    assert not [o for c in code_cells for o in c.outputs if o.output_type == "error"]


def test_tutorial_figures_are_saved_as_html_and_png():
    figures = [
        o
        for c in _code_cells()
        for o in c.outputs
        if o.output_type in ("display_data", "execute_result")
        and "text/html" in o.data
        and "Plotly.newPlot" in o.data["text/html"]
    ]
    assert figures
    assert all("image/png" in o.data for o in figures)


@pytest.mark.network
@requires_jupyter
def test_tutorial_runs(tmp_path, monkeypatch):
    import nbformat
    from nbclient import NotebookClient

    for name in ("TMPDIR", "TEMP", "TMP"):  # the tutorial writes to tempfile.gettempdir()
        monkeypatch.setenv(name, str(tmp_path))
    notebook = nbformat.read(TUTORIAL, as_version=4)
    NotebookClient(notebook, timeout=600, kernel_name="python3").execute(cwd=str(tmp_path))
    output = tmp_path / "bstock_tutorial"
    assert (output / "park" / "park.json").exists()
    assert (output / "intensity.html").exists()
