import importlib.util
import math
import sys

import pandas as pd
import pytest

from bstock.exceptions import (
    InvalidArgumentError,
    MissingDependencyError,
    NoDataWarning,
    UnknownFieldError,
)
from bstock.meters import Channel, Meter
from bstock.park import BuildingPark
from bstock.plot import CATEGORICAL, OTHER, OTHER_COLOR

requires_plotly = pytest.mark.skipif(
    importlib.util.find_spec("plotly") is None, reason="needs the [plot] extra"
)
YEAR = {"start": "2024-01-01", "end": "2025-01-01"}
USAGES = ["tertiary", "tertiary", "tertiary", "residential_collective", "agriculture", "industry"]


def square(lat, lon, size=0.001):
    ring = [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


@pytest.fixture
def park():
    """Sites s0 (b0, b1, b2) and s1 (b3, b4); b5 at the top level, without location nor area."""
    park = BuildingPark(name="P")
    for s in range(2):
        park.new_site(f"s{s}", lat=43.5 + s / 100, lon=-1.5)
    for i, usage in enumerate(USAGES):
        parent = "s0" if i < 3 else "s1" if i < 5 else None
        located = {} if i == 5 else {"footprint": square(43.5 + i / 1000, -1.5)}
        park.new_building(
            id=f"b{i}",
            parent=parent,
            usage=usage,
            construction_year=1950 + 10 * i,
            heated_area=None if i == 5 else 100.0 * (i + 1),
            source="dpe" if i % 2 else "bdnb",
            **located,
        )
    days = pd.date_range("2024-01-01", "2024-12-31", freq="D", tz="Europe/Paris")
    for s, energy in (("s0", "natural_gas"), ("s0", "electricity"), ("s1", "natural_gas")):
        meter = park.add_meter(
            Meter(
                id=f"{s}-{energy}",
                scope=[s],
                energy_type=energy,
                end_use="heating" if energy == "natural_gas" else None,
                channels=[Channel(name="energy", unit="kWh")],
            )
        )
        meter.data = pd.DataFrame({"energy": 1.0}, index=days)
    return park


def trace_names(fig):
    return [trace.name for trace in fig.data]


def subtitle(fig):
    return fig.layout.title.text.split("<sup>")[1].removesuffix("</sup>")


@requires_plotly
@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("completeness", {}),
        ("completeness", {"per": "entity"}),
        ("provenance", {}),
        ("aggregate", {"field": "heated_area"}),
        ("distribution", {"field": "usage"}),
        ("map", {}),
        ("consumption", YEAR),
        ("intensity", YEAR),
    ],
)
def test_every_method_returns_a_figure(park, method, kwargs):
    from plotly.graph_objects import Figure

    assert isinstance(getattr(park.select().plot, method)(**kwargs), Figure)


def test_missing_plotly_gives_a_clear_error(park, monkeypatch):
    monkeypatch.setitem(sys.modules, "plotly", None)
    with pytest.raises(MissingDependencyError, match=r"bstock\[plot\]"):
        park.select().plot.completeness()


@requires_plotly
def test_completeness_stacks_own_and_inherited_shares(park):
    fig = park.select().plot.completeness(fields=["usage", "heated_area"])
    assert trace_names(fig) == ["Own value", "Inherited from a site"]
    assert list(fig.data[0].x) == [100, pytest.approx(500 / 6)]
    assert [trace.marker.color for trace in fig.data] == list(CATEGORICAL[:2])
    assert fig.layout.barmode == "stack"


@requires_plotly
def test_completeness_per_entity_has_one_bar_per_entity(park):
    fig = park.select().plot.completeness(["heated_area"], per="entity")
    assert list(fig.data[0].y) == [f"b{i}" for i in (5, 0, 1, 2, 3, 4)]


@requires_plotly
def test_provenance_has_one_colour_per_source_and_grey_missing(park):
    fig = park.select().plot.provenance(["heated_area"])
    assert trace_names(fig) == ["bdnb", "dpe", "missing"]
    assert fig.data[-1].marker.color == OTHER_COLOR


@requires_plotly
def test_provenance_folds_sources_past_the_palette(park):
    # nine buildings, each with its own source: one more source than colours
    for i, building in enumerate(park.iter_buildings()):
        building.set("level_count", 2, source=f"extra-{i}")
    park.new_building(id="b6", level_count=1, source="extra-6")
    park.new_building(id="b7", level_count=1, source="extra-7")
    park.new_building(id="b8", level_count=1, source="extra-8")
    fig = park.select().plot.provenance(["level_count"])
    assert len(fig.data) == len(CATEGORICAL) + 1
    assert trace_names(fig)[-2:] == [OTHER, "missing"]


@requires_plotly
def test_aggregate_bars_per_site_with_park_total(park):
    fig = park.select().plot.aggregate("heated_area")
    assert list(fig.data[0].x) == ["s0", "s1"]
    assert list(fig.data[0].y) == [600, 900]
    assert subtitle(fig) == "total: 1,500 m² · 2 / 2 sites drawn"
    assert fig.layout.yaxis.title.text == "heated_area (m²)"


@requires_plotly
def test_aggregate_by_attribute(park):
    fig = park.select().plot.aggregate("heated_area", by="usage")
    assert list(fig.data[0].x) == ["tertiary", "residential_collective", "agriculture"]
    assert list(fig.data[0].customdata) == [100, 100, 100]
    assert subtitle(fig) == "3 / 4 groups drawn"  # industry (b5) has no area


@requires_plotly
def test_distribution_of_a_number_is_a_histogram(park):
    fig = park.select().plot.distribution("heated_area", bins=5)
    assert fig.data[0].type == "histogram"
    assert sorted(fig.data[0].x) == [100, 200, 300, 400, 500]
    assert subtitle(fig) == "5 / 6 buildings with a value"
    assert fig.layout.xaxis.title.text == "heated_area (m²)"


@requires_plotly
def test_distribution_of_categories_is_sorted_by_frequency(park):
    fig = park.select().plot.distribution("usage")
    assert fig.data[0].type == "bar"
    assert (fig.data[0].x[0], fig.data[0].y[0]) == ("tertiary", 3)


@requires_plotly
@pytest.mark.parametrize(
    ("field", "error"), [("sub_usages", InvalidArgumentError), ("colour", UnknownFieldError)]
)
def test_distribution_refuses_list_and_unknown_attributes(park, field, error):
    park["b0"].sub_usages = ["school"]
    with pytest.raises(error):
        park.select().plot.distribution(field)


@requires_plotly
def test_map_of_points_keeps_three_colours_then_other(park):
    park["b5"].lat, park["b5"].lon = 43.6, -1.4  # b5 has no site to inherit a location from
    fig = park.select().plot.map(color="usage")
    # the most frequent first, ties in the order of the selection (b5 comes first)
    assert trace_names(fig) == ["tertiary", "industry", "residential_collective", OTHER]
    assert list(fig.data[-1].text) == ["b4"]
    assert fig.data[-1].marker.color == OTHER_COLOR
    assert fig.layout.map.style == "open-street-map"


@requires_plotly
def test_map_points_use_inherited_locations_and_skip_unlocated_entities(park):
    fig = park.select().plot.map()
    assert len(fig.data[0].lat) == 5
    assert subtitle(fig) == "5 / 6 buildings located"


@requires_plotly
def test_map_colours_numbers_on_a_ramp(park):
    fig = park.select().plot.map(color="heated_area")
    assert len(fig.data) == 1
    assert fig.data[0].marker.colorbar.title.text == "heated_area (m²)"


@requires_plotly
def test_map_of_footprints_draws_located_entities_only(park):
    fig = park.select().plot.map(geometry="footprint", color="heated_area")
    assert fig.data[0].type == "choroplethmap"
    assert list(fig.data[0].locations) == ["b0", "b1", "b2", "b3", "b4"]
    assert subtitle(fig) == "5 / 6 buildings located"
    assert math.isclose(fig.layout.map.center.lon, -1.4995, abs_tol=1e-6)


@requires_plotly
def test_map_of_both_draws_footprints_then_markers(park):
    park["b5"].lat, park["b5"].lon = 43.6, -1.4  # a point but no footprint
    fig = park.select().plot.map(geometry="both")
    assert [t.type for t in fig.data] == ["choroplethmap", "scattermap"]
    assert list(fig.data[0].locations) == ["b0", "b1", "b2", "b3", "b4"]
    assert set(fig.data[1].text) == {"b0", "b1", "b2", "b3", "b4", "b5"}
    assert subtitle(fig) == "6 / 6 buildings located"


@requires_plotly
def test_map_of_both_shares_colours_with_one_legend(park):
    fig = park.select().plot.map(geometry="both", color="usage")
    footprints = [t for t in fig.data if t.type == "choroplethmap"]
    markers = [t for t in fig.data if t.type == "scattermap"]
    assert [t.name for t in footprints] == [t.name for t in markers]
    assert [t.colorscale[0][1] for t in footprints] == [t.marker.color for t in markers]
    assert not any(t.showlegend for t in footprints)
    assert all(t.showlegend for t in markers)
    assert [t.legendgroup for t in markers] == [t.name for t in markers]


@requires_plotly
def test_map_of_both_has_one_colour_scale_for_numbers(park):
    fig = park.select().plot.map(geometry="both", color="heated_area")
    footprints, markers = fig.data
    assert footprints.showscale is False
    assert markers.marker.showscale is True
    assert (footprints.zmin, footprints.zmax) == (markers.marker.cmin, markers.marker.cmax)


@requires_plotly
def test_map_refuses_an_unknown_geometry(park):
    with pytest.raises(InvalidArgumentError):
        park.select().plot.map(geometry="polygon")


@requires_plotly
def test_consumption_stacks_energies_per_entity(park):
    fig = park.select(entity="site").plot.consumption(**YEAR)
    assert trace_names(fig) == ["natural_gas", "electricity"]
    assert list(fig.data[0].x) == ["s0", "s1"]
    assert list(fig.data[0].y) == [366, 366]
    assert subtitle(fig) == "lowest data coverage: 100 %"


@requires_plotly
def test_consumption_by_end_use_and_per_month(park):
    fig = park.select(entity="site").plot.consumption(**YEAR, by="end_use", freq="MS")
    assert trace_names(fig) == ["heating", "other"]
    assert len(fig.data[0].x) == 12
    assert fig.data[0].y[0] == 62  # two gas meters, 31 days


@requires_plotly
def test_consumption_horizontal_puts_entities_on_y(park):
    fig = park.select(entity="site").plot.consumption(**YEAR, orientation="h")
    assert trace_names(fig) == ["natural_gas", "electricity"]
    assert {trace.orientation for trace in fig.data} == {"h"}
    assert list(fig.data[0].y) == ["s0", "s1"]
    assert list(fig.data[0].x) == [366, 366]
    assert fig.layout.barmode == "relative"
    assert fig.layout.xaxis.title.text == "Energy (kWh)"
    assert fig.layout.yaxis.autorange == "reversed"  # first entity on top


@requires_plotly
def test_horizontal_height_grows_with_the_rows(park):
    small = park.select(entity="site").plot.consumption(**YEAR, orientation="h")
    assert small.layout.height == 400
    monthly = park.select(entity="site").plot.consumption(**YEAR, orientation="h", freq="MS")
    assert len(monthly.data[0].y) == 12


@requires_plotly
def test_consumption_sorted_by_total(park):
    days = park["s1-natural_gas"].data.index
    extra = park.add_meter(
        Meter(
            id="s1-extra",
            scope=["s1"],
            energy_type="electricity",
            channels=[Channel(name="energy", unit="kWh")],
        )
    )
    extra.data = pd.DataFrame({"energy": 5.0}, index=days)
    fig = park.select(entity="site").plot.consumption(**YEAR, orientation="h", sort=True)
    assert list(fig.data[0].y) == ["s1", "s0"]  # s1: 6 kWh a day, s0: 2


@requires_plotly
def test_consumption_refuses_another_orientation(park):
    with pytest.raises(InvalidArgumentError, match="orientation"):
        park.select().plot.consumption(**YEAR, orientation="horizontal")


@requires_plotly
def test_intensity_horizontal(park):
    fig = park.select(entity="site").plot.intensity(**YEAR, orientation="h", sort=True)
    assert list(fig.data[0].y) == ["s0", "s1"]  # 366/600 > 366/900
    assert fig.layout.xaxis.title.text == "Intensity (kWh/m²)"


@requires_plotly
def test_consumption_refuses_another_stack(park):
    with pytest.raises(InvalidArgumentError):
        park.select().plot.consumption(**YEAR, by="entity")


@requires_plotly
def test_intensity_per_entity(park):
    fig = park.select(entity="site").plot.intensity(**YEAR)
    assert list(fig.data[0].x) == ["s0", "s1"]
    assert list(fig.data[0].y) == [pytest.approx(366 / 600), pytest.approx(366 / 900)]
    assert fig.layout.yaxis.title.text == "Intensity (kWh/m²)"


@requires_plotly
def test_intensity_per_period_divides_by_the_total_area(park):
    fig = park.select(entity="site").plot.intensity(**YEAR, freq="YS")
    gas = dict(zip(trace_names(fig), fig.data, strict=True))["natural_gas"]
    assert list(gas.y) == [pytest.approx(2 * 366 / 1500)]


@requires_plotly
@pytest.mark.parametrize("method", ["consumption", "intensity"])
def test_consumption_plots_reject_an_unknown_argument(park, method):
    with pytest.raises(TypeError, match="breakdwn"):
        getattr(park.select(entity="site").plot, method)(**YEAR, breakdwn=True)


@requires_plotly
def test_consumption_plot_passes_its_arguments(park):
    fig = park.select(entity="site").plot.consumption(**YEAR, tz="UTC", breakdown=True)
    assert fig.data


# --- coverage heatmap ---------------------------------------------------------------------------


def heatmap(fig):
    (trace,) = fig.data
    return trace


@requires_plotly
def test_coverage_has_one_row_per_meter_and_one_column_per_day(park):
    fig = park.select(entity="site").plot.coverage(start="2024-01-01", end="2024-01-11")
    trace = heatmap(fig)
    assert trace.type == "heatmap"
    # sorted by energy type, then id
    assert list(trace.y) == ["s0-electricity", "s0-natural_gas", "s1-natural_gas"]
    assert len(trace.x) == 10
    assert {v for row in trace.z for v in row} == {100.0}
    assert fig.layout.yaxis.autorange == "reversed"


@requires_plotly
def test_coverage_shows_the_gaps(park):
    meter = park["s1-natural_gas"]
    meter.data = meter.data.iloc[::2]
    trace = heatmap(park.select(entity="site").plot.coverage(start="2024-01-01", end="2024-01-05"))
    row = list(trace.z[list(trace.y).index("s1-natural_gas")])
    assert row == [100.0, 0.0, 100.0, 0.0]


@requires_plotly
def test_coverage_per_month_over_the_data_span(park):
    trace = heatmap(park.select(entity="site").plot.coverage(freq="MS"))
    assert len(trace.x) == 12  # data of 2024: up to the day after 2024-12-31
    assert {v for row in trace.z for v in row} == {100.0}


@requires_plotly
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"energy_type": "electricity"}, ["s0-electricity"]),
        ({"energy_type": ["natural_gas"]}, ["s0-natural_gas", "s1-natural_gas"]),
        ({"unit": "kWh"}, ["s0-electricity", "s0-natural_gas", "s1-natural_gas"]),
        ({"unit": "°C"}, []),
        ({"meters": ["s1-natural_gas"]}, ["s1-natural_gas"]),
    ],
)
def test_coverage_filters(park, kwargs, expected):
    fig = park.select(entity="site").plot.coverage(start="2024-01-01", end="2024-01-03", **kwargs)
    assert (list(fig.data[0].y) if fig.data else []) == expected


@requires_plotly
def test_coverage_of_a_selection_keeps_its_meters(park):
    fig = park.select(lambda s: s.id == "s1", entity="site").plot.coverage(
        start="2024-01-01", end="2024-01-03"
    )
    assert list(heatmap(fig).y) == ["s1-natural_gas"]


@requires_plotly
def test_coverage_skips_meters_without_data(park):
    park["s0-electricity"].data = None
    with pytest.warns(NoDataWarning, match="s0-electricity"):
        fig = park.select(entity="site").plot.coverage(start="2024-01-01", end="2024-01-03")
    assert "s0-electricity" not in list(heatmap(fig).y)


@requires_plotly
def test_coverage_of_mixed_time_steps(park):
    sensor = park.add_meter(
        Meter(
            id="t0",
            scope=["s0"],
            timestep="1h",
            channels=[Channel(name="temperature", unit="°C")],
        )
    )
    hours = pd.date_range("2024-01-01", periods=48, freq="h", tz="Europe/Paris")
    sensor.data = pd.DataFrame({"temperature": 20.0}, index=hours)
    trace = heatmap(park.select(entity="site").plot.coverage(start="2024-01-01", end="2024-01-03"))
    assert {v for row in trace.z for v in row} == {100.0}


@requires_plotly
def test_coverage_refuses_an_invalid_window(park):
    with pytest.raises(InvalidArgumentError):
        park.select(entity="site").plot.coverage(start="2024-02-01", end="2024-01-01")
