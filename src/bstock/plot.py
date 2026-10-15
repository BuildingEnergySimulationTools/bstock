"""Plotly figures of a selection, as ``sel.plot.<name>(...)`` (SPEC §14.5, extra ``[plot]``).

Every method returns a ``plotly.graph_objects.Figure`` without showing it; customise it with
``fig.update_layout(...)``. plotly is imported when a figure is built.

Colours follow a validated categorical order (identity), a single blue ramp (magnitude) and a
neutral grey for "Other" and missing values. Categories past the palette fold into "Other".
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from bstock._deps import require
from bstock.exceptions import InvalidArgumentError, UnknownFieldError

if TYPE_CHECKING:
    from plotly.graph_objects import Figure

    from bstock.selection import Selection, TimeLike

__all__: list[str] = []

CATEGORICAL = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
)
"""Categorical colours, assigned in this order (validated for colour vision deficiencies)."""
OTHER_COLOR = "#898781"
"""Colour of "Other", of missing values and of the secondary parts of a bar."""
SEQUENTIAL = (
    "#cde2fb",
    "#9ec5f4",
    "#6da7ec",
    "#3987e5",
    "#256abf",
    "#184f95",
    "#0d366b",
)
"""Single-hue ramp for magnitudes, light to dark."""
MAP_CATEGORIES = 3
"""Colours on a map, where every colour sits next to every other one; the rest is "Other"."""
OTHER = "Other"

_SURFACE = "#fcfcfb"
_INK = "#0b0b0b"
_SECONDARY_INK = "#52514e"
_MUTED_INK = "#898781"
_GRID = "#e1e0d9"
_AXIS = "#c3c2b7"
_FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


class SelectionPlot:
    """Figures of a :class:`~bstock.Selection`, reached through ``sel.plot``.

    Each method returns a ``plotly.graph_objects.Figure`` (extra ``[plot]``) and does not show
    it. The subtitle tells how many entities have a value: entities without one are not drawn.
    """

    def __init__(self, selection: Selection) -> None:
        self._selection = selection

    # --- reports ------------------------------------------------------------------------------

    def completeness(
        self,
        by: Literal["attribute", "entity"] = "attribute",
        *,
        fields: Sequence[str] | None = None,
    ) -> Figure:
        """Plot the share of filled attributes (horizontal bars, 0 to 100 %).

        Parameters
        ----------
        by
            ``"attribute"``: one bar per attribute, own and inherited values stacked.
            ``"entity"``: one bar per entity.
        fields
            Attributes to look at; see :meth:`Selection.completeness`.

        Returns
        -------
        plotly.graph_objects.Figure

        Raises
        ------
        MissingDependencyError
            If plotly is not installed.
        """
        go = _plotly()
        frame = self._selection.completeness(by, fields=fields)
        count = _plural(len(self._selection), self._selection.kind)
        fig = go.Figure()
        if by == "attribute":
            total = frame["total"].where(frame["total"] > 0)
            own = 100 * (frame["filled"] - frame["inherited"]) / total
            inherited = 100 * frame["inherited"] / total
            for name, values, color in (
                ("Own value", own, CATEGORICAL[0]),
                ("Inherited from a site", inherited, CATEGORICAL[1]),
            ):
                fig.add_trace(
                    go.Bar(
                        x=values,
                        y=list(frame.index),
                        orientation="h",
                        name=name,
                        marker_color=color,
                        hovertemplate="%{y}: %{x:.0f} %<extra>" + name + "</extra>",
                    )
                )
        else:
            fig.add_trace(
                go.Bar(
                    x=100 * frame["rate"],
                    y=list(frame.index),
                    orientation="h",
                    marker_color=CATEGORICAL[0],
                    customdata=[", ".join(m) for m in frame["missing"]],
                    hovertemplate="%{y}: %{x:.0f} %<br>missing: %{customdata}<extra></extra>",
                )
            )
        _style(
            fig,
            f"Completeness of {count}",
            None,
            x_title="Filled (%)",
            stacked=True,
            value_axis="x",
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(range=[0, 100])
        return fig

    def provenance(self, fields: Sequence[str] | None = None) -> Figure:
        """Plot the share of each source in the effective values (stacked horizontal bars).

        Parameters
        ----------
        fields
            Attributes to look at; see :meth:`Selection.provenance`.

        Returns
        -------
        plotly.graph_objects.Figure
            One bar per attribute, one colour per source (the most frequent first), missing
            values in grey; the hover gives the inherited share.

        Raises
        ------
        MissingDependencyError
            If plotly is not installed.
        """
        go = _plotly()
        frame = self._selection.provenance(fields, normalize=True)
        sources = [c for c in frame.columns if c not in ("inherited", "missing")]
        kept, folded = _fold(sources, len(CATEGORICAL))
        columns = {source: frame[source] for source in kept}
        if folded:
            columns[OTHER] = frame[folded].sum(axis=1)
        columns["missing"] = frame["missing"]
        fig = go.Figure()
        for i, (name, values) in enumerate(columns.items()):
            color = CATEGORICAL[i] if name in kept else OTHER_COLOR
            fig.add_trace(
                go.Bar(
                    x=100 * values,
                    y=list(frame.index),
                    orientation="h",
                    name=name,
                    marker_color=color,
                    customdata=100 * frame["inherited"],
                    hovertemplate=(
                        "%{y}: %{x:.0f} %<br>inherited (all sources): %{customdata:.0f} %"
                        "<extra>" + name + "</extra>"
                    ),
                )
            )
        title = f"Sources of the values of {_plural(len(self._selection), self._selection.kind)}"
        _style(fig, title, None, x_title="Share of the entities (%)", stacked=True, value_axis="x")
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(range=[0, 100])
        return fig

    def aggregate(
        self,
        field: str,
        *,
        by: str | None = None,
        prefer: Literal["buildings", "site"] = "buildings",
    ) -> Figure:
        """Plot an extensive attribute summed per site, or per value of ``by`` (bars).

        Parameters
        ----------
        field
            Extensive attribute; see :meth:`Selection.aggregate`.
        by, prefer
            As :meth:`Selection.aggregate`. Without ``by``, the bars show the ``total`` of each
            site and the park total is given in the subtitle.

        Returns
        -------
        plotly.graph_objects.Figure
            The hover gives the coverage.

        Raises
        ------
        MissingDependencyError
            If plotly is not installed.
        """
        from bstock.analysis import PARK_ROW

        go = _plotly()
        frame = self._selection.aggregate([field], by=by, prefer=prefer)
        unit = _unit_of(self._selection, field)
        if by is None:
            values = frame[(field, "total")]
            subtitle = f"total: {_number(values.get(PARK_ROW, math.nan))} {unit or ''}".strip()
            values = values.drop(PARK_ROW, errors="ignore")
            coverage = frame[(field, "coverage")].drop(PARK_ROW, errors="ignore")
        else:
            values = frame[(field, "buildings")]
            coverage = frame[(field, "coverage")]
            subtitle = None
        labels = ["(no value)" if v is pd.NA else str(v) for v in values.index]
        known = values.notna().to_numpy()
        fig = go.Figure(
            go.Bar(
                x=[label for label, k in zip(labels, known, strict=True) if k],
                y=values[known],
                marker_color=CATEGORICAL[0],
                customdata=100 * coverage[known],
                hovertemplate="%{x}: %{y:,.0f}<br>coverage: %{customdata:.0f} %<extra></extra>",
            )
        )
        drawn = f"{int(known.sum())} / {len(values)} {'sites' if by is None else 'groups'} drawn"
        subtitle = drawn if subtitle is None else f"{subtitle} · {drawn}"
        _style(fig, f"{field} per {by or 'site'}", subtitle, y_title=_label(field, unit))
        return fig

    # --- values -------------------------------------------------------------------------------

    def distribution(self, field: str, *, bins: int | None = None) -> Figure:
        """Plot the distribution of the effective values of an attribute.

        Parameters
        ----------
        field
            Sourced attribute with single values (number, enum or string).
        bins
            Number of bins of the histogram of a number (plotly chooses by default).

        Returns
        -------
        plotly.graph_objects.Figure
            A histogram for a number, bars of counts for an enum or a string (the most frequent
            first).

        Raises
        ------
        UnknownFieldError
            If ``field`` is not a sourced attribute of the selected entities.
        InvalidArgumentError
            If its values are lists or dicts.
        MissingDependencyError
            If plotly is not installed.
        """
        go = _plotly()
        entities = list(self._selection)
        _check_field(self._selection, field)
        values = [_plain(getattr(e, field)) for e in entities if field in e.sourced_fields()]
        known = [v for v in values if v is not None]
        if any(isinstance(v, list | dict) for v in known):
            raise InvalidArgumentError(f"{field!r} has list or dict values: no distribution")
        unit = _unit_of(self._selection, field)
        subtitle = f"{len(known)} / {len(entities)} {self._selection.kind}s with a value"
        if known and all(isinstance(v, int | float) and not isinstance(v, bool) for v in known):
            fig = go.Figure(
                go.Histogram(
                    x=known,
                    nbinsx=bins,
                    marker_color=CATEGORICAL[0],
                    hovertemplate="%{x}: %{y}<extra></extra>",
                )
            )
            _style(fig, f"Distribution of {field}", subtitle, x_title=_label(field, unit))
            fig.update_layout(yaxis_title="Count", bargap=0.04)
            fig.update_yaxes(dtick=_count_step(len(known)), rangemode="tozero")
            return fig
        counts = Counter(str(v) for v in known).most_common()
        fig = go.Figure(
            go.Bar(
                x=[c for c, _ in counts],
                y=[n for _, n in counts],
                marker_color=CATEGORICAL[0],
                hovertemplate="%{x}: %{y}<extra></extra>",
            )
        )
        _style(fig, f"Distribution of {field}", subtitle, y_title="Count")
        fig.update_yaxes(dtick=_count_step(len(known)))
        return fig

    def map(
        self,
        *,
        geometry: Literal["point", "footprint"] = "point",
        color: str | None = None,
    ) -> Figure:
        """Plot the entities on an OpenStreetMap background (no API key).

        Parameters
        ----------
        geometry
            ``"point"``: the effective ``lat`` / ``lon`` (inherited from the site if needed).
            ``"footprint"``: the effective ``footprint`` (GeoJSON in WGS84; no ``[geo]`` extra
            needed).
        color
            Sourced attribute colouring the entities: a number on a blue ramp, other values in
            categorical colours (the three most frequent, the rest in grey as "Other").

        Returns
        -------
        plotly.graph_objects.Figure
            Centred on the drawn entities.

        Raises
        ------
        InvalidArgumentError
            If ``geometry`` is invalid.
        UnknownFieldError
            If ``color`` is not a sourced attribute of the selected entities.
        MissingDependencyError
            If plotly is not installed.
        """
        go = _plotly()
        if geometry not in ("point", "footprint"):
            raise InvalidArgumentError(f"geometry must be 'point' or 'footprint', not {geometry!r}")
        if color is not None:
            _check_field(self._selection, color)
        entities = list(self._selection)
        shapes = [(e, _geometry(e, geometry)) for e in entities]
        shapes = [(e, g) for e, g in shapes if g is not None]
        values = [_color_value(e, color) for e, _ in shapes]
        numeric = color is not None and _all_numbers(values)
        unit = _unit_of(self._selection, color) if color else None
        fig = go.Figure()
        hover = [_hover_name(e) for e, _ in shapes]
        if geometry == "point":
            lats = [g[0] for _, g in shapes]
            lons = [g[1] for _, g in shapes]
            if numeric:
                fig.add_trace(_points(go, lats, lons, hover, values, color, unit))
            else:
                for name, indexes, colour in _map_groups(values, color):
                    fig.add_trace(
                        go.Scattermap(
                            lat=[lats[i] for i in indexes],
                            lon=[lons[i] for i in indexes],
                            mode="markers",
                            name=name,
                            marker={"size": 10, "color": colour},
                            text=[hover[i] for i in indexes],
                            hovertemplate="%{text}<extra>" + name + "</extra>",
                            showlegend=color is not None,
                        )
                    )
            center, zoom = _view([g for _, g in shapes])
        else:
            features = [
                {"type": "Feature", "id": e.id, "geometry": g, "properties": {}} for e, g in shapes
            ]
            geojson = {"type": "FeatureCollection", "features": features}
            ids = [e.id for e, _ in shapes]
            if numeric:
                z = [math.nan if v is None else v for v in values]
                fig.add_trace(_choropleth(go, geojson, ids, z, hover, SEQUENTIAL, color, unit))
            else:
                for name, indexes, colour in _map_groups(values, color):
                    trace = _choropleth(
                        go,
                        geojson,
                        [ids[i] for i in indexes],
                        [0.0] * len(indexes),
                        [hover[i] for i in indexes],
                        (colour, colour),
                        name,
                        None,
                    )
                    trace.update(showscale=False, showlegend=color is not None, name=name)
                    fig.add_trace(trace)
            center, zoom = _view([p for _, g in shapes for p in _positions(g)])
        subtitle = f"{len(shapes)} / {len(entities)} {self._selection.kind}s located"
        _style(fig, f"Map of {_plural(len(entities), self._selection.kind)}", subtitle)
        fig.update_layout(
            map={"style": "open-street-map", "center": center, "zoom": zoom},
            margin={"l": 0, "r": 0, "t": 100, "b": 0},
        )
        return fig

    # --- consumption --------------------------------------------------------------------------

    def consumption(
        self,
        *,
        start: TimeLike,
        end: TimeLike,
        freq: Literal["YS", "MS"] | None = None,
        by: Literal["energy_type", "end_use"] = "energy_type",
        **kwargs: Any,
    ) -> Figure:
        """Plot the measured consumption in kWh (stacked bars).

        Parameters
        ----------
        start, end, freq
            Period; see :meth:`Selection.consumption`.
        by
            Stack the bars by ``"energy_type"`` or by ``"end_use"`` (then use
            ``breakdown=True`` to split the head meters by end use).
        **kwargs
            Other arguments of :meth:`Selection.consumption` (``tz``, ``breakdown``, ``split``,
            ``load_data``).

        Returns
        -------
        plotly.graph_objects.Figure
            One bar per entity, or with ``freq`` one bar per period adding up the selected
            entities (in a selection of sites, nested sites overlap). The subtitle gives the
            lowest data coverage.

        Raises
        ------
        InvalidArgumentError
            If ``by`` is invalid, or as :meth:`Selection.consumption`.
        MissingDependencyError
            If plotly is not installed.
        """
        go = _plotly()
        _check_stack(by)
        frame = self._selection.consumption(start=start, end=end, freq=freq, **kwargs)
        frame = frame[frame["energy"].notna()]
        x = "period" if freq else "entity"
        table = frame.pivot_table(
            index=x, columns=by, values="energy", aggfunc="sum", sort=False, dropna=False
        )
        fig = go.Figure()
        _stacked_bars(go, fig, table, "%{y:,.0f} kWh")
        subtitle = _coverage_subtitle(frame)
        _style(fig, f"Consumption per {by.replace('_', ' ')}", subtitle, y_title="Energy (kWh)")
        fig.update_layout(barmode="stack")
        return fig

    def intensity(
        self,
        *,
        start: TimeLike,
        end: TimeLike,
        area: str = "heated_area",
        freq: Literal["YS", "MS"] | None = None,
        **kwargs: Any,
    ) -> Figure:
        """Plot the consumption per unit of area, stacked by energy (bars).

        Parameters
        ----------
        start, end, area, freq
            See :meth:`Selection.intensity`.
        **kwargs
            Other arguments of :meth:`Selection.intensity` (``prefer``, ``tz``, ``split``,
            ``load_data``...).

        Returns
        -------
        plotly.graph_objects.Figure
            One bar per entity having an area; with ``freq``, one bar per period for the whole
            selection: energy of the rows having an area divided by the sum of their areas.

        Raises
        ------
        MissingDependencyError
            If plotly is not installed.
        """
        go = _plotly()
        frame = self._selection.intensity(start=start, end=end, area=area, freq=freq, **kwargs)
        frame = frame[frame["intensity"].notna()]
        unit = _unit_of(self._selection, area) or "unit"
        if freq:
            energy = frame.pivot_table(
                index="period", columns="energy_type", values="energy", aggfunc="sum", sort=False
            )
            areas = frame.drop_duplicates(["period", "entity"]).groupby("period", sort=False)[
                "area"
            ]
            table = energy.div(areas.sum(), axis=0)
        else:
            table = frame.pivot_table(
                index="entity",
                columns="energy_type",
                values="intensity",
                aggfunc="sum",
                sort=False,
            )
        fig = go.Figure()
        _stacked_bars(go, fig, table, "%{y:,.1f} kWh/" + unit)
        located = frame["entity"].nunique()
        subtitle = f"{located} entities with a {area} · {_coverage_subtitle(frame)}"
        _style(fig, f"Consumption per {area}", subtitle, y_title=f"Intensity (kWh/{unit})")
        fig.update_layout(barmode="stack")
        return fig


# --- helpers -------------------------------------------------------------------------------------


def _plotly() -> Any:
    require("plotly", "plot")
    return require("plotly.graph_objects", "plot")


def _style(
    fig: Any,
    title: str,
    subtitle: str | None,
    *,
    x_title: str | None = None,
    y_title: str | None = None,
    stacked: bool = False,
    value_axis: Literal["x", "y"] | None = "y",
) -> None:
    """Apply the bstock look to ``fig``.

    Recessive axes, a grid on the value axis only, thin rounded bars, the legend on top in the
    order of the traces.
    """
    text = title if subtitle is None else f"{title}<br><sup>{subtitle}</sup>"
    fig.update_layout(
        title={
            "text": text,
            "x": 0,
            "xref": "paper",
            "xanchor": "left",
            "font": {"size": 16, "color": _INK},
        },
        font={"family": _FONT, "size": 12, "color": _SECONDARY_INK},
        paper_bgcolor=_SURFACE,
        plot_bgcolor=_SURFACE,
        barmode="stack" if stacked else None,
        bargap=0.3,
        barcornerradius=4,
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.0,
            "x": 0,
            "title": None,
            "traceorder": "normal",
        },
        hoverlabel={"bgcolor": _SURFACE, "font": {"color": _INK, "family": _FONT}},
        margin={"t": 90},
    )
    axis = {
        "gridcolor": _GRID,
        "linecolor": _AXIS,
        "zerolinecolor": _AXIS,
        "tickfont": {"color": _MUTED_INK},
        "title": {"font": {"color": _SECONDARY_INK}},
    }
    fig.update_xaxes(**axis, title_text=x_title, showgrid=value_axis == "x")
    fig.update_yaxes(**axis, title_text=y_title, showgrid=value_axis == "y")
    # A thin surface-coloured outline separates adjacent and stacked bars.
    fig.update_traces(marker_line={"color": _SURFACE, "width": 1}, selector={"type": "bar"})


def _stacked_bars(go: Any, fig: Any, table: pd.DataFrame, value_format: str) -> None:
    """Add one bar trace per column of ``table`` (categories), folded past the palette."""
    columns = [c for c in table.columns if table[c].notna().any()]
    names = [
        "(none)" if c is None or (isinstance(c, float) and math.isnan(c)) else c for c in columns
    ]
    kept, folded = _fold(names, len(CATEGORICAL))
    series = {
        name: table[column] for name, column in zip(names, columns, strict=True) if name in kept
    }
    if folded:
        folded_columns = [c for n, c in zip(names, columns, strict=True) if n in folded]
        series[OTHER] = table[folded_columns].sum(axis=1, min_count=1)
    x = [_x_label(i) for i in table.index]
    for i, (name, values) in enumerate(series.items()):
        fig.add_trace(
            go.Bar(
                x=x,
                y=values.to_numpy(),
                name=str(name),
                marker_color=CATEGORICAL[i] if name != OTHER else OTHER_COLOR,
                hovertemplate="%{x}: " + value_format + "<extra>" + str(name) + "</extra>",
            )
        )


def _fold(names: Sequence[Any], limit: int) -> tuple[list[Any], list[Any]]:
    """Return the names kept with their own colour, and those folded into "Other"."""
    names = list(names)
    if len(names) <= limit:
        return names, []
    return names[: limit - 1], names[limit - 1 :]


def _map_groups(values: list[Any], color: str | None) -> list[tuple[str, list[int], str]]:
    """Return ``(legend name, indexes, colour)``: the most frequent values, then the rest."""
    if color is None:
        return [("", list(range(len(values))), CATEGORICAL[0])]
    labels = [None if v is None else str(v) for v in values]
    ranked = [label for label, _ in Counter(lb for lb in labels if lb is not None).most_common()]
    kept = ranked[:MAP_CATEGORIES]
    groups = [
        (label, [i for i, lb in enumerate(labels) if lb == label], CATEGORICAL[n])
        for n, label in enumerate(kept)
    ]
    rest = [i for i, lb in enumerate(labels) if lb not in kept]
    if rest:
        name = OTHER if len(ranked) > len(kept) else "(no value)"
        groups.append((name, rest, OTHER_COLOR))
    return groups


def _points(
    go: Any,
    lats: list[float],
    lons: list[float],
    hover: list[str],
    values: list[Any],
    color: str,
    unit: str | None,
) -> Any:
    known = [v for v in values if v is not None]
    return go.Scattermap(
        lat=lats,
        lon=lons,
        mode="markers",
        marker={
            "size": 10,
            "color": [math.nan if v is None else v for v in values],
            "colorscale": _colorscale(SEQUENTIAL),
            "cmin": min(known) if known else None,
            "cmax": max(known) if known else None,
            "colorbar": {"title": {"text": _label(color, unit)}},
        },
        text=hover,
        hovertemplate="%{text}: %{marker.color}<extra></extra>",
        showlegend=False,
    )


def _choropleth(
    go: Any,
    geojson: dict[str, Any],
    ids: list[str],
    z: list[float],
    hover: list[str],
    colors: Sequence[str],
    color: str | None,
    unit: str | None,
) -> Any:
    return go.Choroplethmap(
        geojson=geojson,
        locations=ids,
        featureidkey="id",
        z=z,
        colorscale=_colorscale(colors),
        marker={"opacity": 0.8, "line": {"color": _SURFACE, "width": 1}},
        text=hover,
        hovertemplate="%{text}<extra></extra>",
        colorbar={"title": {"text": _label(color, unit) if color else ""}},
    )


def _colorscale(colors: Sequence[str]) -> list[list[Any]]:
    return [[i / (len(colors) - 1), c] for i, c in enumerate(colors)]


def _geometry(entity: Any, kind: str) -> Any:
    if kind == "footprint":
        return entity.footprint
    if entity.lat is None or entity.lon is None:
        return None
    return (entity.lat, entity.lon)


def _positions(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    """Return the ``(lat, lon)`` of every position of a Polygon or MultiPolygon."""
    polygons = geometry["coordinates"]
    if geometry["type"] == "Polygon":
        polygons = [polygons]
    return [(lat, lon) for polygon in polygons for ring in polygon for lon, lat, *_ in ring]


def _view(points: list[tuple[float, float]]) -> tuple[dict[str, float], float]:
    """Return the centre and a zoom level showing every point."""
    if not points:
        return {"lat": 46.6, "lon": 2.4}, 4.5  # mainland France
    lats, lons = [p[0] for p in points], [p[1] for p in points]
    center = {"lat": (min(lats) + max(lats)) / 2, "lon": (min(lons) + max(lons)) / 2}
    extent = max(max(lats) - min(lats), max(lons) - min(lons), 0.002)
    return center, max(1.0, min(17.0, math.log2(360 / extent) - 1))


def _color_value(entity: Any, color: str | None) -> Any:
    if color is None or color not in entity.sourced_fields():
        return None
    return _plain(getattr(entity, color))


def _all_numbers(values: list[Any]) -> bool:
    known = [v for v in values if v is not None]
    return bool(known) and all(
        isinstance(v, int | float) and not isinstance(v, bool) for v in known
    )


def _hover_name(entity: Any) -> str:
    return entity.id if entity.name is None else f"{entity.id} ({entity.name})"


def _check_field(selection: Selection, name: str) -> None:
    park = selection.park
    base = park.building_cls if selection.kind == "building" else park.site_cls
    known = set(base.sourced_fields())
    for entity in selection:
        known.update(entity.sourced_fields())
    if name not in known:
        raise UnknownFieldError(f"{name!r} is not an attribute of the {selection.kind}s")


def _check_stack(by: str) -> None:
    if by not in ("energy_type", "end_use"):
        raise InvalidArgumentError(f"by must be 'energy_type' or 'end_use', not {by!r}")


def _unit_of(selection: Selection, name: str) -> str | None:
    park = selection.park
    base = park.building_cls if selection.kind == "building" else park.site_cls
    field = base.sourced_fields().get(name)
    return None if field is None else field.unit


def _label(name: str, unit: str | None) -> str:
    return name if not unit else f"{name} ({unit})"


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _number(value: float) -> str:
    return "no value" if math.isnan(value) else f"{value:,.0f}"


def _x_label(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.tz_localize(None) if value.tzinfo is not None else value
    return value


def _count_step(count: int) -> int | None:
    """Return a tick step of 1 for small counts, so that no fractional count is shown."""
    return 1 if count <= 10 else None


def _coverage_subtitle(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "no data"
    return f"lowest data coverage: {100 * frame['coverage'].min():.0f} %"
