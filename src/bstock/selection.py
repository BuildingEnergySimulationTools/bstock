"""Selections of buildings or sites of a park, with criteria on their values (SPEC §14.2)."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from bstock.building import Building
from bstock.description import DescribedEntity, _check_kind
from bstock.display import TreeView, build_tree
from bstock.exceptions import (
    InvalidArgumentError,
    UnknownFieldError,
    UnknownIdError,
    UnknownKindError,
)
from bstock.site import Site

if TYPE_CHECKING:
    import pandas as pd

    from bstock.park import BuildingPark

__all__ = ["Selection"]

SelectionKind = Literal["building", "site"]
Predicate = Callable[[Any], object]
"""Function called with each entity; the entity is kept when it returns a true value."""
_Test = Callable[[DescribedEntity], bool]

OPERATORS = ("in", "lt", "le", "gt", "ge", "isnull", "contains")
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

    def __init__(self, park: BuildingPark, kind: SelectionKind, ids: Sequence[str]) -> None:
        self._park = park
        self._kind = kind
        self._ids = tuple(ids)

    @property
    def park(self) -> BuildingPark:
        """The park of the selected entities."""
        return self._park

    @property
    def kind(self) -> SelectionKind:
        """``"building"`` or ``"site"``."""
        return self._kind

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
        cls = _ENTITY_CLASSES[self._kind]
        entities = []
        for entity_id in self._ids:
            entity = self._park.get(entity_id)
            if not isinstance(entity, cls):
                raise UnknownIdError(
                    f"{self._kind} {entity_id!r} is no longer in the park; select again"
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
            A new selection of the same kind.

        Raises
        ------
        UnknownFieldError, InvalidArgumentError, UnknownIdError
            As :meth:`BuildingPark.select`.
        """
        tests = _compile(self._park, self._kind, predicate, criteria)
        kept = [e.id for e in self._entities() if all(test(e) for test in tests)]
        return Selection(self._park, self._kind, kept)

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
        if other._park is not self._park or other._kind != self._kind:
            raise InvalidArgumentError(
                "selections can only be combined when they have the same park and kind"
            )
        self._entities()
        other._entities()
        ids = operation(set(self._ids), set(other._ids))
        return Selection(self._park, self._kind, _tree_order(self._park, self._kind, ids))

    # --- analysis -----------------------------------------------------------------------------

    def aggregate(
        self,
        fields: Sequence[str] | None = None,
        *,
        by: str | None = None,
        prefer: Literal["buildings", "site"] = "buildings",
    ) -> pd.DataFrame:
        """Sum the extensive attributes of the selected buildings, per site (SPEC §14.3).

        The model is not modified. A missing value is never estimated: ``coverage`` shows the
        share of buildings having a value.

        Parameters
        ----------
        fields
            Extensive attributes of the buildings (``sourced(extensive=True)``); all of them by
            default.
        by
            Without it, one row per site concerned (the sites containing selected buildings, or
            the selected sites) and a total row indexed ``"__park__"``. With an attribute name,
            one row per effective value of that attribute among the selected buildings, the
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
            Columns are a ``MultiIndex`` (quantity, measure). Per attribute: ``buildings`` (sum
            of the known building values), ``site`` (own value of the site), ``delta``
            (``site - buildings``), ``coverage`` (0 to 1) and ``total``; with ``by``, only
            ``buildings`` and ``coverage``. Then ``heating_power``, ``cooling_power``,
            ``dhw_power`` (kW) and ``pv_peak_power`` (kWp) with ``value`` and ``coverage`` (a
            system counts once, on its owner, and a site system counts if it serves a selected
            building), and the counts ``building_count``, ``<system kind>_count`` and
            ``meter_count`` (measure ``value``). Values are in the unit of the attribute.

        Raises
        ------
        UnknownFieldError
            If an attribute is unknown.
        InvalidArgumentError
            If an attribute is not extensive, ``prefer`` is invalid, ``by`` is used on a
            selection of sites or names an attribute with list values.
        """
        from bstock.analysis import aggregate  # circular import

        return aggregate(self, fields, by=by, prefer=prefer)

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
        from bstock.export import entities_frame  # circular import

        return entities_frame(self._park, self._kind, self._entities(), provenance=provenance)

    def tree(
        self,
        *,
        systems: bool = False,
        meters: bool = False,
        max_depth: int | None = None,
        fields: Sequence[str] | None = None,
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

    def __repr__(self) -> str:
        count = len(self._ids)
        return f"Selection(kind={self._kind!r}, {count} {'entity' if count == 1 else 'entities'})"

    def _repr_html_(self) -> str:
        return self.tree()._repr_html_()


def select(
    park: BuildingPark,
    predicate: Predicate | None = None,
    *,
    kind: SelectionKind = "building",
    **criteria: Any,
) -> Selection:
    """Return the buildings or sites of ``park`` that match every criterion.

    See :meth:`BuildingPark.select`.
    """
    if kind not in _ENTITY_CLASSES:
        raise UnknownKindError(f"unknown selection kind {kind!r}; expected 'building' or 'site'")
    tests = _compile(park, kind, predicate, criteria)
    entities = park.iter_buildings() if kind == "building" else park.iter_sites()
    return Selection(park, kind, [e.id for e in entities if all(test(e) for test in tests)])


def _tree_order(park: BuildingPark, kind: str, ids: set[str]) -> list[str]:
    entities = park.iter_buildings() if kind == "building" else park.iter_sites()
    return [e.id for e in entities if e.id in ids]


# --- criteria ----------------------------------------------------------------------------------


def _compile(
    park: BuildingPark, kind: str, predicate: Predicate | None, criteria: dict[str, Any]
) -> list[_Test]:
    """Check the criteria and return one test per criterion."""
    tests: list[_Test] = []
    if predicate is not None:
        if not callable(predicate):
            raise InvalidArgumentError(f"the predicate must be callable, not {predicate!r}")
        tests.append(lambda entity: bool(predicate(entity)))
    for key, value in criteria.items():
        if key in STRUCTURE_CRITERIA:
            tests.append(_structure_test(park, kind, key, value))
        else:
            tests.append(_value_test(park, kind, key, value))
    return tests


def _value_test(park: BuildingPark, kind: str, key: str, value: Any) -> _Test:
    base = park.building_cls if kind == "building" else park.site_cls
    known = {*base.sourced_fields(), *_PLAIN_ATTRIBUTES[kind]}
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
        raise UnknownFieldError(f"{name!r} is not an attribute of the {kind}s of this park")
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
        raise InvalidArgumentError(f"{key} only applies to list attributes")
    return value in current


def _ordered(compare: Callable[[Any, Any], bool], current: Any, target: Any, key: str) -> bool:
    if current is None:
        return False
    try:
        return bool(compare(current, target))
    except TypeError:
        raise InvalidArgumentError(f"{key}: cannot compare {current!r} with {target!r}") from None


def _structure_test(park: BuildingPark, kind: str, key: str, value: Any) -> _Test:
    if key == "within":
        site = park._resolve_site(value)
        return lambda entity: any(a is site for a in entity._ancestor_sites())
    if key == "parent":
        parent = None if value is None else park._resolve_site(value)
        return lambda entity: entity.parent is parent
    if key == "has_rnb_id":
        if kind != "building":
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
            systems = entity.serving_systems(system_kind)
        else:
            systems = list(entity.iter_systems(system_kind))
        return bool(systems) is flag

    return has_system


def _flag(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise InvalidArgumentError(f"{key} expects True or False, not {value!r}")
    return value
