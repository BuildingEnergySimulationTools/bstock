"""Readable representations of the park and its entities (SPEC §14.1).

Containers (park, site) are shown as a tree, the other entities on one line; ``describe()``
details the sourced fields of an entity.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from enum import Enum
from html import escape
from typing import TYPE_CHECKING, Any

import pandas as pd

from bstock._fields import field_names
from bstock.exceptions import InvalidArgumentError, UnknownFieldError

if TYPE_CHECKING:
    from bstock.building import Building
    from bstock.meters import Meter
    from bstock.park import BuildingPark
    from bstock.provenance import SourcedModel
    from bstock.site import Site
    from bstock.systems import System

__all__ = ["TreeView"]

REPR_MAX_LINES = 30
"""Number of lines of the tree shown by the repr of a park or a site."""


class _Node:
    """One line of a tree and the lines below it."""

    __slots__ = ("children", "label")

    def __init__(self, label: str, children: list[_Node] | None = None) -> None:
        self.label = label
        self.children = children or []


class TreeView:
    """Tree of the sites and buildings of a park or a site, returned by ``tree()``.

    ``str()`` and the repr give the full text tree, without truncation. In Jupyter, the tree is
    shown as HTML: the first level is listed and every node with content can be unfolded.

    It is built by :meth:`bstock.BuildingPark.tree` and :meth:`bstock.Site.tree`, not directly.
    It is a snapshot: it does not follow later changes of the park.
    """

    def __init__(self, header: str, nodes: list[_Node]) -> None:
        self._header = header
        self._nodes = nodes

    def lines(self) -> list[str]:
        """Return the lines of the text tree, header first.

        Returns
        -------
        list[str]
            One string per line, without line breaks.
        """
        lines = [self._header]
        for i, node in enumerate(self._nodes):
            _render(node, "", i == len(self._nodes) - 1, lines)
        return lines

    def truncated(self, max_lines: int) -> str:
        """Return the text tree cut to ``max_lines`` lines, the last one counting the others.

        Parameters
        ----------
        max_lines
            Maximum number of lines, header and counting line included (at least 2).

        Returns
        -------
        str
            The text tree, unchanged if it is short enough.
        """
        lines = self.lines()
        if len(lines) <= max_lines:
            return "\n".join(lines)
        kept = lines[: max(max_lines - 1, 1)]
        hidden = len(lines) - len(kept)
        return "\n".join([*kept, f"… {hidden} more lines: call .tree() for the full tree"])

    def __str__(self) -> str:
        return "\n".join(self.lines())

    def __repr__(self) -> str:
        return str(self)

    def _repr_html_(self) -> str:
        """Return the tree as nested ``<details>`` elements (no JavaScript), for Jupyter."""
        nodes = "".join(_node_html(node) for node in self._nodes)
        return (
            f'<div class="bstock-tree" style="{_HTML_STYLE}">'
            f"<div><strong>{escape(self._header)}</strong></div>"
            f'<div style="{_HTML_CHILDREN_STYLE}">{nodes}</div></div>'
        )


_HTML_STYLE = "font-family: monospace; font-size: 13px; line-height: 1.5; white-space: pre"
# The border colour has some transparency so that it suits light and dark themes.
_HTML_CHILDREN_STYLE = "margin-left: 0.4em; padding-left: 0.8em; border-left: 1px solid #8884"
# Leaves are shifted by the width of the disclosure triangle of the <summary> elements.
_HTML_LEAF_STYLE = "padding-left: 1.1em"


def _node_html(node: _Node) -> str:
    label = escape(node.label)
    if not node.children:
        return f'<div style="{_HTML_LEAF_STYLE}">{label}</div>'
    children = "".join(_node_html(child) for child in node.children)
    return (
        f'<details><summary style="cursor: pointer">{label}</summary>'
        f'<div style="{_HTML_CHILDREN_STYLE}">{children}</div></details>'
    )


def _render(node: _Node, prefix: str, last: bool, lines: list[str]) -> None:
    lines.append(f"{prefix}{'└── ' if last else '├── '}{node.label}")
    child_prefix = prefix + ("    " if last else "│   ")
    for i, child in enumerate(node.children):
        _render(child, child_prefix, i == len(node.children) - 1, lines)


# --- tree ------------------------------------------------------------------------------------


def build_tree(
    root: BuildingPark | Site,
    *,
    systems: bool = False,
    meters: bool = False,
    max_depth: int | None = None,
    fields: str | Sequence[str] | None = None,
    shown: Collection[str] | None = None,
    header: str | None = None,
) -> TreeView:
    """Build the tree of a park or a site; see :meth:`bstock.BuildingPark.tree`.

    ``shown`` restricts the sites and buildings to these ids (``None``: all); the building count
    of a site then counts the shown buildings, or all of them if no building is shown. ``header``
    replaces the first line of a park tree.
    """
    from bstock.park import BuildingPark

    if max_depth is not None and max_depth < 0:
        raise InvalidArgumentError(f"max_depth must be positive or None, not {max_depth!r}")
    fields = field_names(fields) or []
    _check_fields(fields, [*root.iter_sites(), *root.iter_buildings()])
    park = root if isinstance(root, BuildingPark) else root.park
    builder = _TreeBuilder(
        park,
        systems=systems,
        meters=meters,
        max_depth=max_depth,
        fields=fields,
        shown=None if shown is None else set(shown),
    )
    if isinstance(root, BuildingPark):
        nodes = builder.children(root, depth=1)
        if meters and shown is None:
            nodes.extend(
                _Node(f"{_meter_label(m)}  (no scope)") for m in root.meters if not m.scope
            )
        return TreeView(header or _park_header(root), nodes)
    return TreeView(builder.label(root), builder.content(root, depth=1))


class _TreeBuilder:
    """Build the nodes of a tree with the options of ``tree()``."""

    def __init__(
        self,
        park: BuildingPark | None,
        *,
        systems: bool,
        meters: bool,
        max_depth: int | None,
        fields: list[str],
        shown: set[str] | None,
    ) -> None:
        self.park = park
        self.systems = systems
        self.meters = meters
        self.max_depth = max_depth
        self.fields = fields
        self.shown = shown

    def sites(self, container: BuildingPark | Site) -> list[Site]:
        """Return the shown direct sub-sites of ``container``."""
        return [s for s in container.sites if self._is_shown(s)]

    def buildings(self, container: BuildingPark | Site) -> list[Building]:
        """Return the shown direct buildings of ``container``."""
        return [b for b in container.buildings if self._is_shown(b)]

    def building_count(self, site: Site) -> int:
        """Return the number of buildings counted on the line of ``site``."""
        every = list(site.iter_buildings())
        shown = [b for b in every if self._is_shown(b)]
        return len(shown) if shown or self.shown is None else len(every)

    def children(self, container: BuildingPark | Site, depth: int) -> list[_Node]:
        """Return the nodes of the shown sites then buildings of ``container``, at ``depth``."""
        sites, buildings = self.sites(container), self.buildings(container)
        if self.max_depth is not None and depth > self.max_depth:
            summary = self._counts(sites, buildings)
            return [_Node(f"… {summary}")] if summary else []
        return [
            _Node(self.label(entity), self.content(entity, depth + 1))
            for entity in [*sites, *buildings]
        ]

    def content(self, entity: Site | Building, depth: int) -> list[_Node]:
        """Return the nodes below ``entity``; they are at ``depth``."""
        from bstock.site import Site

        nodes: list[_Node] = []
        if self.max_depth is None or depth <= self.max_depth:
            if self.systems:
                nodes.extend(_Node(_system_label(s)) for s in entity.iter_systems())
            if self.meters and self.park is not None:
                nodes.extend(_Node(_meter_label(m)) for m in self.park._meters_for(entity))
        if isinstance(entity, Site):
            nodes.extend(self.children(entity, depth))
        return nodes

    def label(self, entity: Site | Building) -> str:
        """Return the line of ``entity``: kind, id, name, building count and ``fields``."""
        from bstock.site import Site

        is_site = isinstance(entity, Site)
        parts = [f"[{'site' if is_site else 'building'}] {_id_and_name(entity)}"]
        if isinstance(entity, Site):
            parts.append(f"({_plural(self.building_count(entity), 'building')})")
        values = [
            f"{name}={_format_value(getattr(entity, name), _unit(entity, name))}"
            for name in self.fields
            if name in entity.sourced_fields()
        ]
        if values:
            parts.append(", ".join(values))
        return "  ".join(parts)

    def _counts(self, sites: list[Site], buildings: list[Building]) -> str:
        """Return the number of shown sites and buildings in and below the given ones, as text."""
        nested_sites = [s for site in sites for s in site.iter_sites()]
        nested_buildings = [b for site in sites for b in site.iter_buildings()]
        site_count = len(sites) + sum(1 for s in nested_sites if self._is_shown(s))
        building_count = len(buildings) + sum(1 for b in nested_buildings if self._is_shown(b))
        pairs = [(site_count, "site"), (building_count, "building")]
        return ", ".join(_plural(n, word) for n, word in pairs if n)

    def _is_shown(self, entity: Site | Building) -> bool:
        return self.shown is None or entity.id in self.shown


def _check_fields(fields: list[str], entities: list[Any]) -> None:
    from bstock.description import DescribedEntity

    known = set(DescribedEntity.sourced_fields())
    for cls in {type(e) for e in entities}:
        known.update(cls.sourced_fields())
    unknown = [name for name in fields if name not in known]
    if unknown:
        raise UnknownFieldError(f"unknown sourced field(s): {', '.join(map(repr, unknown))}")


def _park_header(park: BuildingPark) -> str:
    counts = [
        _plural(sum(1 for _ in park.iter_sites()), "site"),
        _plural(sum(1 for _ in park.iter_buildings()), "building"),
        _plural(len(park.meters), "meter"),
    ]
    return f"{type(park).__name__} {park.name!r}: {', '.join(counts)}"


def _id_and_name(entity: Any) -> str:
    name = entity.name
    return entity.id if name is None else f"{entity.id}  {name}"


def _system_label(system: System) -> str:
    return f"[{system.kind}] {_id_and_name(system)}"


def _meter_label(meter: Meter) -> str:
    return f"[meter] {_id_and_name(meter)}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


# --- one-line repr -----------------------------------------------------------------------------


def one_line_repr(entity: SourcedModel) -> str:
    """Return ``Class(key=value, ...)`` with the non-empty items of ``entity._repr_items()``."""
    items = ", ".join(f"{key}={text}" for key, text in entity._repr_items())
    return f"{type(entity).__name__}({items})"


def sourced_items(entity: SourcedModel, names: Sequence[str]) -> list[tuple[str, str]]:
    """Return ``(name, text)`` for the sourced fields of ``names`` that have a value."""
    items = []
    for name in names:
        value = getattr(entity, name)
        if value is not None:
            items.append((name, _format_value(value, _unit(entity, name))))
    return items


def _format_value(value: Any, unit: str | None = None) -> str:
    if value is None:
        return "None"
    if isinstance(value, Enum):
        value = value.value
    text = repr(value)
    return f"{text} {unit}" if unit else text


def _unit(entity: SourcedModel, name: str) -> str | None:
    return entity.sourced_fields()[name].unit


# --- describe ----------------------------------------------------------------------------------


def describe(entity: SourcedModel) -> pd.DataFrame:
    """Return one row per sourced field of ``entity``; see :meth:`SourcedModel.describe`."""
    rows = []
    for name, field in entity.sourced_fields().items():
        candidate = entity.effective(name)
        provider = entity._provider(name)
        value = None if candidate is None else candidate.value
        rows.append(
            {
                "field": name,
                "value": value.value if isinstance(value, Enum) else value,
                "unit": field.unit,
                "source": None if candidate is None else candidate.source,
                "provider": None if provider is None else provider._entity_id(),
                "inherited": provider is not None and provider is not entity,
                "candidates": len(entity.values.get(name, [])),
            }
        )
    columns = ["value", "unit", "source", "provider", "inherited", "candidates"]
    frame = pd.DataFrame(rows, columns=["field", *columns]).set_index("field")
    return frame.astype({"value": object, "inherited": bool, "candidates": int})
