"""Measured consumption per entity, energy and end use, and kWh/m² indicators (SPEC §6.5).

bstock only adds energies up: an ``energy`` channel is summed, an ``index`` channel gives its last
reading minus the first one. Power is never integrated, and nothing is resampled or estimated.
"""

from __future__ import annotations

import itertools
import math
import warnings
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from bstock.building import Building
from bstock.exceptions import (
    InvalidArgumentError,
    NoDataWarning,
    TimeSeriesError,
    UnknownFieldError,
)
from bstock.site import Site
from bstock.systems import System

if TYPE_CHECKING:
    from bstock.meters import Channel, Meter
    from bstock.park import BuildingPark
    from bstock.selection import Selection

__all__: list[str] = []

ENERGY_UNITS = {"Wh": 1e-3, "kWh": 1.0, "MWh": 1e3, "GWh": 1e6}
"""Accepted units of the energy and index channels, with their factor to kWh."""
FREQUENCIES = ("YS", "MS")
"""Calendar periods accepted by ``freq``: years and months."""
OTHER_END_USE = "other"
"""End use of the meters without one, and of the remainder of a breakdown."""

TimeLike = str | date | datetime | pd.Timestamp
Owner = Site | Building


@dataclass
class _Line:
    """Energy of one meter (or remainder) for one period, before grouping."""

    entity: str
    period: pd.Timestamp
    energy_type: str | None
    end_use: str
    energy: float
    coverage: float
    meters: list[str]
    estimated: bool = False
    owners: list[Owner] = field(default_factory=list)


def consumption(
    selection: Selection,
    *,
    start: TimeLike,
    end: TimeLike,
    freq: Literal["YS", "MS"] | None = None,
    tz: str = "Europe/Paris",
    breakdown: bool = False,
    split: str | None = None,
    load_data: bool = False,
) -> pd.DataFrame:
    """Return the measured final energy of a selection; see :meth:`Selection.consumption`."""
    frame, _ = _consumption(
        selection,
        start=start,
        end=end,
        freq=freq,
        tz=tz,
        breakdown=breakdown,
        split=split,
        load_data=load_data,
    )
    return frame


def intensity(
    selection: Selection,
    *,
    area: str = "heated_area",
    prefer: Literal["buildings", "site"] = "buildings",
    **kwargs: Any,
) -> pd.DataFrame:
    """Return the consumption per unit of area; see :meth:`Selection.intensity`."""
    park = selection.park
    _check_extensive(park, area, "area")
    if prefer not in ("buildings", "site"):
        raise InvalidArgumentError(f"prefer must be 'buildings' or 'site', not {prefer!r}")
    frame, owners = _consumption(selection, **kwargs)
    areas = {label: _area(park, entities, area, prefer) for label, entities in owners.items()}
    frame["area"] = frame["entity"].map(areas).astype(float)
    frame["intensity"] = frame["energy"] / frame["area"].where(frame["area"] > 0)
    return frame


# --- main computation ----------------------------------------------------------------------------


def _consumption(
    selection: Selection,
    *,
    start: TimeLike,
    end: TimeLike,
    freq: str | None = None,
    tz: str = "Europe/Paris",
    breakdown: bool = False,
    split: str | None = None,
    load_data: bool = False,
) -> tuple[pd.DataFrame, dict[str, list[Owner]]]:
    """Return the consumption frame and, for each ``entity`` label, the entities it covers."""
    park = selection.park
    periods = _periods(start, end, freq, tz)
    if split is not None:
        if selection.entity != "building":
            raise InvalidArgumentError("split= only applies to a selection of buildings")
        _check_extensive(park, split, "split")
    notes: list[str] = []
    calculator = _Calculator(park, periods, load_data, notes)
    lines: list[_Line] = []
    for label, owners, meter in _rows(selection):
        for line in calculator.lines(meter, breakdown):
            line.entity, line.owners = label, owners
            lines.append(line)
    if split is not None:
        selected = set(selection.ids)
        lines = [s for line in lines for s in _split(line, split, selected, notes)]
    for note in dict.fromkeys(notes):
        warnings.warn(note, NoDataWarning, stacklevel=4)
    owners_by_label = {line.entity: line.owners for line in lines}
    return _frame(lines, with_period=freq is not None), owners_by_label


def _periods(
    start: TimeLike, end: TimeLike, freq: str | None, tz: str
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Return the ``[start, end)`` periods: one window, or its calendar years or months."""
    first, last = _timestamp(start, tz), _timestamp(end, tz)
    if first >= last:
        raise InvalidArgumentError(f"start ({first}) must be before end ({last})")
    if freq is not None and freq not in FREQUENCIES:
        raise InvalidArgumentError(f"freq must be one of {list(FREQUENCIES)} or None, not {freq!r}")
    bounds = [first]
    if freq is not None:
        bounds += [t for t in pd.date_range(first, last, freq=freq) if first < t < last]
    bounds.append(last)
    return list(itertools.pairwise(bounds))


def _timestamp(value: TimeLike, tz: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(tz) if stamp.tzinfo is None else stamp.tz_convert(tz)


def _owner(park: BuildingPark, entity_id: str) -> Owner:
    """Return the site or building of a scope id: the entity itself, or the owner of a system."""
    entity = park.get(entity_id)
    if isinstance(entity, System):
        entity = entity._container
    if not isinstance(entity, Site | Building):
        raise InvalidArgumentError(f"scope id {entity_id!r} is not an entity of the park")
    return entity


def _owners(park: BuildingPark, meter: Meter) -> list[Owner]:
    owners = [_owner(park, entity_id) for entity_id in meter.scope]
    return list({id(o): o for o in owners}.values())


def _rows(selection: Selection) -> Iterable[tuple[str, list[Owner], Meter]]:
    """Yield ``(entity label, entities covered, head meter)`` for each retained head meter."""
    park = selection.park
    heads = [m for m in park.meters if m.parent_meter is None and m.scope]
    entities = selection._entities()
    if selection.entity == "building":
        relevant = {b.id for b in entities} | {s.id for b in entities for s in b._ancestor_sites()}
        for meter in heads:
            owners = _owners(park, meter)
            if any(o.id in relevant for o in owners):
                yield "+".join(o.id for o in owners), owners, meter
        return
    for site in entities:
        subtree = {
            site.id,
            *(s.id for s in site.iter_sites()),
            *(b.id for b in site.iter_buildings()),
        }
        for meter in heads:
            if any(o.id in subtree for o in _owners(park, meter)):
                yield site.id, [site], meter


class _Calculator:
    """Energy of the meters over the periods, with the notes for the warnings."""

    def __init__(
        self,
        park: BuildingPark,
        periods: list[tuple[pd.Timestamp, pd.Timestamp]],
        load_data: bool,
        notes: list[str],
    ) -> None:
        self.park = park
        self.periods = periods
        self.load_data = load_data
        self.notes = notes
        self._cache: dict[str, list[tuple[float, float]] | None] = {}

    def lines(self, meter: Meter, breakdown: bool) -> list[_Line]:
        """Return the lines of a head meter, broken down by its direct sub-meters if asked."""
        energies = self.energies(meter)
        if energies is None:
            return []
        subs = [m for m in self.park.meters if m.parent_meter == meter.id] if breakdown else []
        if not subs:
            return self._lines(meter, energies, _end_use(meter), [meter.id])
        lines: list[_Line] = []
        remainder = list(energies)
        for sub in subs:
            if None not in (sub.energy_type, meter.energy_type) and (
                sub.energy_type != meter.energy_type
            ):
                raise TimeSeriesError(
                    f"sub-meter {sub.id!r} measures {sub.energy_type}, its head meter "
                    f"{meter.id!r} {meter.energy_type}"
                )
            sub_energies = self.energies(sub)
            if sub_energies is None:
                continue
            lines += self._lines(meter, sub_energies, _end_use(sub), [sub.id])
            remainder = [
                (energy - (0.0 if math.isnan(part) else part), min(coverage, part_coverage))
                for (energy, coverage), (part, part_coverage) in zip(
                    remainder, sub_energies, strict=True
                )
            ]
        for (start, _), (energy, _) in zip(self.periods, remainder, strict=True):
            if energy < 0:
                self.notes.append(
                    f"meter {meter.id!r}: its sub-meters measure more than it does from {start}"
                )
        lines += self._lines(meter, remainder, OTHER_END_USE, [meter.id, *(s.id for s in subs)])
        return lines

    def _lines(
        self, meter: Meter, energies: list[tuple[float, float]], end_use: str, meter_ids: list[str]
    ) -> list[_Line]:
        energy_type = _plain(meter.energy_type)
        return [
            _Line("", start, energy_type, end_use, energy, coverage, meter_ids)
            for (start, _), (energy, coverage) in zip(self.periods, energies, strict=True)
        ]

    def energies(self, meter: Meter) -> list[tuple[float, float]] | None:
        """Return ``(energy in kWh, coverage)`` per period, or ``None`` if the meter is skipped."""
        if meter.id not in self._cache:
            self._cache[meter.id] = self._energies(meter)
        return self._cache[meter.id]

    def _energies(self, meter: Meter) -> list[tuple[float, float]] | None:
        channel = _energy_channel(meter)
        if channel is None:
            self.notes.append(f"meter {meter.id!r} has no 'energy' or 'index' channel: ignored")
            return None
        if channel.unit not in ENERGY_UNITS:
            raise TimeSeriesError(
                f"meter {meter.id!r}: unit {channel.unit!r} is not an energy unit "
                f"({', '.join(ENERGY_UNITS)})"
            )
        if meter.data is None and self.load_data:
            meter.load_data()
        if meter.data is None:
            self.notes.append(f"meter {meter.id!r} has no data in memory: ignored")
            return None
        series = (
            meter.data[meter.tide_columns()[channel.name]].dropna() * ENERGY_UNITS[channel.unit]
        )
        if channel.name == "index":
            return [self._index_energy(meter, series, start, end) for start, end in self.periods]
        step = _timestep(meter, series)
        return [_summed_energy(series, step, start, end) for start, end in self.periods]

    def _index_energy(
        self, meter: Meter, series: pd.Series, start: pd.Timestamp, end: pd.Timestamp
    ) -> tuple[float, float]:
        window = series[(series.index >= start) & (series.index < end)]
        if len(window) < 2:
            return math.nan, 0.0
        energy = float(window.iloc[-1] - window.iloc[0])
        if energy < 0:
            self.notes.append(f"meter {meter.id!r}: index going down from {start} (reset?)")
            return math.nan, 0.0
        return energy, (window.index[-1] - window.index[0]) / (end - start)


def _energy_channel(meter: Meter) -> Channel | None:
    by_name = {c.name: c for c in meter.channels}
    return by_name.get("energy") or by_name.get("index")


def _timestep(meter: Meter, series: pd.Series) -> pd.Timedelta | None:
    """Return the nominal time step of the meter, else the median gap of its data."""
    if meter.timestep is not None:
        try:
            return pd.Timedelta(pd.tseries.frequencies.to_offset(meter.timestep))
        except ValueError:
            pass  # calendar offsets such as "MS" have no fixed duration
    if len(series) < 2:
        return None
    return pd.Series(series.index).diff().median()


def _summed_energy(
    series: pd.Series, step: pd.Timedelta | None, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[float, float]:
    window = series[(series.index >= start) & (series.index < end)]
    if window.empty:
        return math.nan, 0.0
    coverage = math.nan if step is None else min(1.0, len(window) * step / (end - start))
    return float(window.sum()), coverage


def _end_use(meter: Meter) -> str:
    return _plain(meter.end_use) or OTHER_END_USE


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


# --- split and grouping --------------------------------------------------------------------------


def _split(line: _Line, name: str, selected: set[str], notes: list[str]) -> list[_Line]:
    """Share the line of a site or of several entities between the buildings it covers."""
    if len(line.owners) == 1 and isinstance(line.owners[0], Building):
        return [line]
    covered = list(
        {
            b.id: b
            for owner in line.owners
            for b in (owner.iter_buildings() if isinstance(owner, Site) else [owner])
        }.values()
    )
    values = [getattr(b, name) for b in covered]
    total = sum(v for v in values if v is not None)
    if not covered or any(v is None for v in values) or total <= 0:
        notes.append(f"{line.entity!r}: not split, {name} is missing on some of its buildings")
        return [line]
    return [
        _Line(
            building.id,
            line.period,
            line.energy_type,
            line.end_use,
            line.energy * value / total,
            line.coverage,
            line.meters,
            estimated=True,
            owners=[building],
        )
        for building, value in zip(covered, values, strict=True)
        if building.id in selected
    ]


COLUMNS = [
    "entity",
    "period",
    "energy_type",
    "end_use",
    "energy",
    "coverage",
    "estimated",
    "meters",
]


def _frame(lines: list[_Line], *, with_period: bool) -> pd.DataFrame:
    """Group the lines by entity, period, energy, end use and estimation, in entity order."""
    entity_order = {entity: i for i, entity in enumerate(dict.fromkeys(li.entity for li in lines))}
    lines = sorted(lines, key=lambda li: (entity_order[li.entity], li.period))
    groups: dict[tuple[Any, ...], list[_Line]] = {}
    for line in lines:
        key = (line.entity, line.period, line.energy_type, line.end_use, line.estimated)
        groups.setdefault(key, []).append(line)
    rows = []
    for (entity, period, energy_type, end_use, estimated), group in groups.items():
        known = [g.energy for g in group if not math.isnan(g.energy)]
        rows.append(
            {
                "entity": entity,
                "period": period,
                "energy_type": energy_type,
                "end_use": end_use,
                "energy": math.fsum(known) if known else math.nan,
                "coverage": min(g.coverage for g in group),
                "estimated": estimated,
                "meters": list(dict.fromkeys(m for g in group for m in g.meters)),
            }
        )
    columns = COLUMNS if with_period else [c for c in COLUMNS if c != "period"]
    frame = pd.DataFrame(rows, columns=COLUMNS)[columns]
    return frame.astype({"energy": float, "coverage": float, "estimated": bool})


# --- areas -------------------------------------------------------------------------------------


def _check_extensive(park: BuildingPark, name: str, argument: str) -> None:
    field_ = park.building_cls.sourced_fields().get(name)
    if field_ is None:
        raise UnknownFieldError(f"{argument}: {name!r} is not a field of the buildings")
    if not field_.extensive:
        raise InvalidArgumentError(f"{argument}: {name!r} is not extensive")


def _area(park: BuildingPark, owners: list[Owner], name: str, prefer: str) -> float:
    """Return the area of the entities of a line: sum of their areas, NaN if one is missing."""
    from bstock.analysis import _Scope, _total
    from bstock.selection import Selection

    total = 0.0
    for owner in owners:
        if isinstance(owner, Site):
            value = _total(_Scope(Selection(park, "site", [owner.id])), owner, name, prefer)
        else:
            raw = getattr(owner, name)
            value = math.nan if raw is None else float(raw)
        if math.isnan(value):
            return math.nan
        total += value
    return total
