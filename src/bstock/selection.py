"""Selections of buildings or sites of a park, with criteria on their values (SPEC §14.2)."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from bstock.building import Building
from bstock.description import DescribedEntity, _check_kind
from bstock.display import TreeView, build_tree
from bstock.exceptions import (
    InvalidArgumentError,
    UnknownFieldError,
    UnknownIdError,
)
from bstock.site import Site

if TYPE_CHECKING:
    from bstock.park import BuildingPark
    from bstock.plot import SelectionPlot

__all__ = ["Selection"]

EntityType = Literal["building", "site"]
TimeLike = str | date | datetime | pd.Timestamp
Predicate = Callable[[Any], object]
"""Function called with each entity; the entity is kept when it returns a true value."""
_Test = Callable[[DescribedEntity], bool]

OPERATORS = ("in", "family", "lt", "le", "gt", "ge", "isnull", "contains")
"""Suffixes of the value criteria, as in ``construction_year__lt=1975``."""
STRUCTURE_CRITERIA = ("within", "parent", "has_rnb_id", "has_meter", "has_system")
"""Criteria on the position of the entity in the park and on what it is linked to."""
_PLAIN_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "building": ("rnb_id", "bdnb_batiment_groupe_id", "bdnb_batiment_construction_id", "dpe_ids"),
    "site": ("bdnb_batiment_groupe_id", "dpe_ids"),
}
_ENTITY_CLASSES: dict[str, type[DescribedEntity]] = {"building": Building, "site": Site}
_ORDERING = {
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
}


class Selection:
    """A snapshot of buildings or of sites of a park, created by :meth:`BuildingPark.select`.

    It keeps the ids of the selected entities and iterates over them in the order of the park
    tree. It does not follow later changes of the park: select again after a change. Using a
    selection whose entities have been removed from the park raises :class:`UnknownIdError`.

    Selections combine with ``|`` (union), ``&`` (intersection) and ``-`` (difference), and
    :meth:`filter` narrows them down.
    """

    def __init__(self, park: BuildingPark, entity: EntityType, ids: Sequence[str]) -> None:
        self._park = park
        self._entity_type = entity
        self._ids = tuple(ids)

    @property
    def park(self) -> BuildingPark:
        """The park of the selected entities."""
        return self._park

    @property
    def entity(self) -> EntityType:
        """``"building"`` or ``"site"``."""
        return self._entity_type

    @property
    def ids(self) -> tuple[str, ...]:
        """Ids of the selected entities, in the order of the park tree."""
        return self._ids

    def __len__(self) -> int:
        return len(self._ids)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._entities())

    def __contains__(self, item: object) -> bool:
        entity_id = item if isinstance(item, str) else getattr(item, "id", None)
        return entity_id in self._ids

    def _entities(self) -> list[Any]:
        """Return the selected entities; raise ``UnknownIdError`` if one has left the park."""
        cls = _ENTITY_CLASSES[self._entity_type]
        entities = []
        for entity_id in self._ids:
            entity = self._park.get(entity_id)
            if not isinstance(entity, cls):
                raise UnknownIdError(
                    f"{self._entity_type} {entity_id!r} is no longer in the park; select again"
                )
            entities.append(entity)
        return entities

    def filter(self, predicate: Predicate | None = None, **criteria: Any) -> Selection:
        """Return the entities of this selection that match every criterion.

        Parameters
        ----------
        predicate
            Function called with each entity; see :meth:`BuildingPark.select`.
        **criteria
            Criteria of :meth:`BuildingPark.select`.

        Returns
        -------
        Selection
            A new selection of the same entity type.

        Raises
        ------
        UnknownFieldError, InvalidArgumentError, UnknownIdError
            As :meth:`BuildingPark.select`.
        """
        tests = _compile(self._park, self._entity_type, predicate, criteria)
        kept = [e.id for e in self._entities() if all(test(e) for test in tests)]
        return Selection(self._park, self._entity_type, kept)

    # --- set operations -----------------------------------------------------------------------

    def __or__(self, other: Selection) -> Selection:
        return self._combine(other, lambda a, b: a | b)

    def __and__(self, other: Selection) -> Selection:
        return self._combine(other, lambda a, b: a & b)

    def __sub__(self, other: Selection) -> Selection:
        return self._combine(other, lambda a, b: a - b)

    def _combine(
        self, other: Selection, operation: Callable[[set[str], set[str]], set[str]]
    ) -> Selection:
        if not isinstance(other, Selection):
            return NotImplemented
        if other._park is not self._park or other._entity_type != self._entity_type:
            raise InvalidArgumentError(
                "selections can only be combined when they have the same park and entity type"
            )
        self._entities()
        other._entities()
        ids = operation(set(self._ids), set(other._ids))
        return Selection(
            self._park, self._entity_type, _tree_order(self._park, self._entity_type, ids)
        )

    # --- analysis -----------------------------------------------------------------------------

    def aggregate(
        self,
        fields: str | Sequence[str] | None = None,
        *,
        by: str | None = None,
        prefer: Literal["buildings", "site"] = "buildings",
    ) -> pd.DataFrame:
        """Sum the extensive fields of the selected buildings, per site (SPEC §14.3).

        The model is not modified. A missing value is never estimated: ``coverage`` shows the
        share of buildings having a value.

        Parameters
        ----------
        fields
            Extensive fields of the buildings (``sourced(extensive=True)``), a name or a list of
            names; all of them by default.
        by
            Without it, one row per site concerned (the sites containing selected buildings, or
            the selected sites) and a total row indexed ``"__park__"``. With a field name,
            one row per effective value of that field among the selected buildings, the
            buildings without value last (``<NA>``); the own values of sites are then ignored
            (``NoDataWarning``). Selections of buildings only.
        prefer
            Value retained in ``total`` when a site has its own value and its buildings theirs:
            ``"buildings"`` (the sum of the children, the site value when no child has one) or
            ``"site"`` (the site value when it has one). The own value of a site enters a total
            only if all its buildings are selected.

        Returns
        -------
        pandas.DataFrame
            Columns are a ``MultiIndex`` (quantity, measure). Per field: ``buildings`` (sum
            of the known building values), ``site`` (own value of the site), ``delta``
            (``site - buildings``), ``coverage`` (0 to 1) and ``total``; with ``by``, only
            ``buildings`` and ``coverage``. Then ``heating_power``, ``cooling_power``,
            ``dhw_power`` (kW) and ``pv_peak_power`` (kWp) with ``value`` and ``coverage`` (a
            system counts once, on its owner, and a site system counts if it serves a selected
            building), and the counts ``building_count``, ``<system kind>_count`` and
            ``meter_count`` (measure ``value``). Values are in the unit of the field.

        Raises
        ------
        UnknownFieldError
            If a field is unknown.
        InvalidArgumentError
            If a field is not extensive, ``prefer`` is invalid, ``by`` is used on a
            selection of sites or names a field with list values.
        """
        from bstock.analysis import aggregate  # circular import

        return aggregate(self, fields, by=by, prefer=prefer)

    def completeness(
        self,
        fields: str | Sequence[str] | None = None,
        *,
        per: Literal["field", "entity"] = "field",
    ) -> pd.DataFrame:
        """Return how complete the sourced fields of the selected entities are (SPEC §14.4).

        An inherited value counts as filled.

        Parameters
        ----------
        fields
            Fields to look at (a name or a list of names); by default every sourced field of
            the selected entities, project ones and ``footprint`` included.
        per
            ``"field"``: one row per sourced field, with ``filled`` (entities having an
            effective value), ``inherited`` (of which inherited), ``total`` and ``rate`` (0 to 1).
            ``"entity"``: one row per entity, with ``filled``, ``total``, ``rate`` and
            ``missing`` (names of the fields without value).

        Returns
        -------
        pandas.DataFrame
            Indexed by ``field`` name, or by entity ``id``.

        Raises
        ------
        UnknownFieldError
            If a field is unknown.
        InvalidArgumentError
            If ``per`` is neither ``"field"`` nor ``"entity"``.
        """
        from bstock.analysis import completeness  # circular import

        return completeness(self, fields=fields, per=per)

    def provenance(
        self, fields: str | Sequence[str] | None = None, *, normalize: bool = False
    ) -> pd.DataFrame:
        """Return the sources of the effective values of the selected entities (SPEC §14.4).

        Parameters
        ----------
        fields
            Fields to look at (a name or a list of names); by default every sourced field of
            the selected entities.
        normalize
            Return shares of the entities (0 to 1) rather than counts.

        Returns
        -------
        pandas.DataFrame
            One row per field (index ``field``) and one column per source of the
            effective values, the most frequent first, giving the number of entities whose value
            comes from it; then ``inherited`` (values inherited from a site, whatever their
            source) and ``missing``.

        Raises
        ------
        UnknownFieldError
            If a field is unknown.
        """
        from bstock.analysis import provenance  # circular import

        return provenance(self, fields=fields, normalize=normalize)

    def conflicts(
        self, fields: str | Sequence[str] | None = None, *, rtol: float = 0.05
    ) -> pd.DataFrame:
        """Return the fields whose own candidates disagree (SPEC §14.4).

        Inherited candidates are not compared: another level is not a conflict. Strings, enums,
        lists and integers are compared strictly; floats agree when they are within ``rtol`` of
        the largest absolute value.

        Parameters
        ----------
        fields
            Fields to compare (a name or a list of names); by default every sourced field of
            the selected entities except ``footprint`` (two geometries from different sources
            never match exactly).
        rtol
            Relative tolerance for floats.

        Returns
        -------
        pandas.DataFrame
            One row per entity and field in conflict, with ``entity``, ``field``,
            ``values`` (dict source to value) and ``retained`` (source of the effective value).

        Raises
        ------
        UnknownFieldError
            If a field is unknown.
        InvalidArgumentError
            If ``rtol`` is negative.
        """
        from bstock.analysis import conflicts  # circular import

        return conflicts(self, fields=fields, rtol=rtol)

    # --- measured consumption -----------------------------------------------------------------

    def consumption(
        self,
        *,
        start: TimeLike,
        end: TimeLike,
        freq: Literal["YS", "MS"] | None = None,
        tz: str = "Europe/Paris",
        breakdown: bool = False,
        split: str | None = None,
        load_data: bool = False,
    ) -> pd.DataFrame:
        """Return the measured final energy per entity, energy and end use (SPEC §6.5).

        Only head meters (``parent_meter=None``) are added up, so that a meter and its
        sub-meters are not counted twice. A PV production meter (``end_use="pv_production"``),
        head or sub-meter, gives its own lines with a **negative** energy: the sums are energy
        balances. An ``energy`` channel is summed over the period, an
        ``index`` channel gives its last reading minus the first one; power channels are not
        integrated (convert them with tide first). Nothing is extrapolated: ``coverage`` gives
        the share of the period covered by data.

        Parameters
        ----------
        start, end
            Period ``[start, end)``; a time without time zone is read in ``tz``.
        freq
            ``"YS"`` (calendar years) or ``"MS"`` (calendar months) to cut the period, in
            ``tz``; ``None`` for one period.
        tz
            Time zone of the calendar periods and of naive ``start`` / ``end``.
        breakdown
            Replace a head meter having direct sub-meters by one line per end use of its
            sub-meters, plus an ``"other"`` line for the rest (head minus sub-meters).
        split
            Extensive field (e.g. ``"heated_area"``) used to share the lines of a site or of
            several buildings between the selected buildings they cover; these lines have
            ``estimated=True``. Selections of buildings only.
        load_data
            Load the data of the meters that have none in memory.

        Returns
        -------
        pandas.DataFrame
            One row per entity, period, energy type and end use, with ``entity`` (id, or ids
            joined by ``+`` for a meter covering several entities), ``period`` (start of the
            period, only with ``freq``), ``energy_type``, ``end_use``, ``energy`` (kWh, negative
            for a production),
            ``coverage`` (0 to 1, the lowest of the row meters), ``estimated`` and ``meters``.
            In a selection of buildings, a meter appears at the level of its scope: a site
            meter on the site row. In a selection of sites, each row adds up the head meters of
            the subtree of a selected site.

        Raises
        ------
        InvalidArgumentError
            If the period or ``freq`` is invalid, ``split`` is not extensive or is used on a
            selection of sites.
        TimeSeriesError
            If an energy channel has another unit than Wh, kWh, MWh or GWh, or a sub-meter
            measures another energy than its head meter.

        Warns
        -----
        NoDataWarning
            For a meter without data or without energy channel (ignored), an index going down,
            sub-meters measuring more than their head meter, or a line that cannot be split.
        """
        from bstock.consumption import consumption  # circular import

        return consumption(
            self,
            start=start,
            end=end,
            freq=freq,
            tz=tz,
            breakdown=breakdown,
            split=split,
            load_data=load_data,
        )

    def intensity(
        self,
        *,
        start: TimeLike,
        end: TimeLike,
        area: str = "heated_area",
        prefer: Literal["buildings", "site"] = "buildings",
        freq: Literal["YS", "MS"] | None = None,
        tz: str = "Europe/Paris",
        breakdown: bool = False,
        split: str | None = None,
        load_data: bool = False,
    ) -> pd.DataFrame:
        """Return :meth:`consumption` with the area and the energy per unit of area (SPEC §6.5).

        Parameters
        ----------
        area
            Extensive field giving the reference area, ``"heated_area"`` by default.
        prefer
            For a site row, the area retained by :meth:`aggregate` with this rule.
        start, end, freq, tz, breakdown, split, load_data
            As :meth:`consumption`.

        Returns
        -------
        pandas.DataFrame
            The columns of :meth:`consumption`, plus ``area`` (in the unit of the field, m²
            for the core areas) and ``intensity`` (kWh per unit of area). The area of a row
            covering several entities is the sum of their areas; it is missing if one of them
            is, and ``intensity`` is then missing too.

        Raises
        ------
        UnknownFieldError
            If ``area`` is not a field of the buildings.
        InvalidArgumentError
            If ``area`` is not extensive, ``prefer`` is invalid, or as :meth:`consumption`.
        """
        from bstock.consumption import intensity  # circular import

        return intensity(
            self,
            area=area,
            prefer=prefer,
            start=start,
            end=end,
            freq=freq,
            tz=tz,
            breakdown=breakdown,
            split=split,
            load_data=load_data,
        )

    # --- exports and display ------------------------------------------------------------------

    def to_dataframe(self, *, provenance: bool = False) -> pd.DataFrame:
        """Return one row per selected entity; see :func:`bstock.export.to_dataframe`.

        Parameters
        ----------
        provenance
            Add the ``<attr>_source`` and ``<attr>_provider`` columns.

        Returns
        -------
        pandas.DataFrame
            The rows of :meth:`BuildingPark.to_dataframe` for the selected entities, in the
            order of the selection.
        """
        from bstock.export import _entities_frame  # circular import

        return _entities_frame(
            self._park, self._entity_type, self._entities(), provenance=provenance
        )

    def tree(
        self,
        *,
        systems: bool = False,
        meters: bool = False,
        max_depth: int | None = None,
        fields: str | Sequence[str] | None = None,
    ) -> TreeView:
        """Return the tree of the selected entities and of their ancestor sites.

        The parameters are those of :meth:`BuildingPark.tree`. A site shows the number of
        selected buildings below it; in a selection of sites, buildings are not shown and a
        site shows the number of all its buildings.

        Returns
        -------
        TreeView
            The tree; ``str()`` gives the text and Jupyter shows it as HTML.
        """
        shown = set(self._ids)
        for entity in self._entities():
            shown.update(site.id for site in entity._ancestor_sites())
        return build_tree(
            self._park,
            systems=systems,
            meters=meters,
            max_depth=max_depth,
            fields=fields,
            shown=shown,
            header=f"{self!r} of {type(self._park).__name__} {self._park.name!r}",
        )

    @property
    def plot(self) -> SelectionPlot:
        """Figures of the selection, built with plotly (extra ``[plot]``, SPEC §14.5).

        ``sel.plot.completeness()``, ``provenance``, ``aggregate``, ``distribution``, ``map``,
        ``consumption`` and ``intensity`` each return a ``plotly.graph_objects.Figure`` without
        showing it.
        """
        from bstock.plot import SelectionPlot  # circular import

        return SelectionPlot(self)

    def __repr__(self) -> str:
        count = len(self._ids)
        noun = "entity" if count == 1 else "entities"
        return f"Selection(entity={self._entity_type!r}, {count} {noun})"

    def _repr_html_(self) -> str:
        return self.tree()._repr_html_()


def select(
    park: BuildingPark,
    predicate: Predicate | None = None,
    *,
    entity: EntityType = "building",
    **criteria: Any,
) -> Selection:
    """Return the buildings or sites of ``park`` that match every criterion.

    See :meth:`BuildingPark.select`.
    """
    check_entity_type(entity)
    tests = _compile(park, entity, predicate, criteria)
    entities = park.iter_buildings() if entity == "building" else park.iter_sites()
    return Selection(park, entity, [e.id for e in entities if all(test(e) for test in tests)])


def check_entity_type(entity: str) -> None:
    """Check that ``entity`` is ``"building"`` or ``"site"``.

    Raises
    ------
    InvalidArgumentError
        Otherwise.
    """
    if entity not in _ENTITY_CLASSES:
        raise InvalidArgumentError(f"entity must be 'building' or 'site', not {entity!r}")


def _tree_order(park: BuildingPark, entity_type: str, ids: set[str]) -> list[str]:
    entities = park.iter_buildings() if entity_type == "building" else park.iter_sites()
    return [e.id for e in entities if e.id in ids]


# --- criteria ----------------------------------------------------------------------------------


def _compile(
    park: BuildingPark, entity_type: str, predicate: Predicate | None, criteria: dict[str, Any]
) -> list[_Test]:
    """Check the criteria and return one test per criterion."""
    tests: list[_Test] = []
    if predicate is not None:
        if not callable(predicate):
            raise InvalidArgumentError(f"the predicate must be callable, not {predicate!r}")
        tests.append(lambda entity: bool(predicate(entity)))
    for key, value in criteria.items():
        if key in STRUCTURE_CRITERIA:
            tests.append(_structure_test(park, entity_type, key, value))
        else:
            tests.append(_value_test(park, entity_type, key, value))
    return tests


def _value_test(park: BuildingPark, entity_type: str, key: str, value: Any) -> _Test:
    base = park.building_cls if entity_type == "building" else park.site_cls
    known = {*base.sourced_fields(), *_PLAIN_ATTRIBUTES[entity_type]}
    name, _, operator = key.rpartition("__")
    if not name:
        name, operator = key, "eq"
    elif operator not in OPERATORS:
        if name in known:
            raise InvalidArgumentError(
                f"unknown operator {operator!r} in {key!r}; expected one of {list(OPERATORS)}"
            )
        name, operator = key, "eq"
    if name not in known:
        raise UnknownFieldError(f"{name!r} is not a field of the {entity_type}s of this park")
    field = base.sourced_fields().get(name)
    enum = field.type_ if field is not None and _is_enum(field.type_) else None

    def get(entity: DescribedEntity) -> Any:
        return getattr(entity, name)

    if operator == "isnull":
        if not isinstance(value, bool):
            raise InvalidArgumentError(f"{key} expects True or False, not {value!r}")
        return lambda entity: (get(entity) is None) is value
    if operator == "in":
        if isinstance(value, str) or not hasattr(value, "__iter__"):
            raise InvalidArgumentError(f"{key} expects a list of values, not {value!r}")
        allowed = [_coerce(enum, v, key) for v in value]
        return lambda entity: get(entity) is not None and get(entity) in allowed
    if operator == "contains":
        return lambda entity: _contains(get(entity), value, key)
    if operator == "family":
        if enum is None:
            raise InvalidArgumentError(f"{key} only applies to enum fields")
        variants = _coerce(enum, value, key).variants()
        return lambda entity: get(entity) in variants
    target = _coerce(enum, value, key)
    if operator == "eq":
        return lambda entity: get(entity) is not None and get(entity) == target
    compare = _ORDERING[operator]
    return lambda entity: _ordered(compare, get(entity), target, key)


def _is_enum(type_: Any) -> bool:
    return isinstance(type_, type) and issubclass(type_, Enum)


def _coerce(enum: Any, value: Any, key: str) -> Any:
    if enum is None:
        return value
    try:
        return enum(value)
    except ValueError:
        expected = [member.value for member in enum]
        raise InvalidArgumentError(
            f"{key}: {value!r} is not a valid value; expected one of {expected}"
        ) from None


def _contains(current: Any, value: Any, key: str) -> bool:
    if current is None:
        return False
    if not isinstance(current, list):
        raise InvalidArgumentError(f"{key} only applies to list fields")
    return value in current


def _ordered(compare: Callable[[Any, Any], bool], current: Any, target: Any, key: str) -> bool:
    if current is None:
        return False
    try:
        return bool(compare(current, target))
    except TypeError:
        raise InvalidArgumentError(f"{key}: cannot compare {current!r} with {target!r}") from None


def _structure_test(park: BuildingPark, entity_type: str, key: str, value: Any) -> _Test:
    if key == "within":
        site = park._resolve_site(value)
        return lambda entity: any(a is site for a in entity._ancestor_sites())
    if key == "parent":
        parent = None if value is None else park._resolve_site(value)
        return lambda entity: entity.parent is parent
    if key == "has_rnb_id":
        if entity_type != "building":
            raise InvalidArgumentError("has_rnb_id only applies to buildings")
        flag = _flag(key, value)
        return lambda entity: (entity.rnb_id is not None) is flag
    if key == "has_meter":
        flag = _flag(key, value)
        return lambda entity: bool(entity.find_meters()) is flag
    # has_system: True / False, or a system kind
    if isinstance(value, bool):
        system_kind = None
        flag = value
    else:
        system_kind = _check_kind(value)
        flag = True

    def has_system(entity: DescribedEntity) -> bool:
        if isinstance(entity, Building):
            systems = entity.find_serving_systems(system_kind)
        else:
            systems = list(entity.iter_systems(system_kind))
        return bool(systems) is flag

    return has_system


def _flag(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise InvalidArgumentError(f"{key} expects True or False, not {value!r}")
    return value
