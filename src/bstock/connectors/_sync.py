"""Idempotent writing of connector values into the park (SPEC §8.2).

Running a connector again replaces the candidates of its source. The systems and envelope
components it creates have deterministic ids, so they are updated instead of duplicated, and those
a new response no longer produces are removed (or kept and reported when a meter references them).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

from bstock.building import Building
from bstock.connectors._report import ImportReport
from bstock.description import DescribedEntity
from bstock.enums import ComponentKind, SystemKind
from bstock.envelope import Envelope, EnvelopeComponent, Window
from bstock.exceptions import UnknownIdError
from bstock.provenance import SourcedModel, SourcedValue
from bstock.systems import SYSTEM_CLASSES, System

if TYPE_CHECKING:
    from bstock.park import BuildingPark

__all__ = [
    "component_id",
    "put",
    "select_buildings",
    "sync_components",
    "sync_systems",
    "system_id",
]

Candidates = Mapping[str, SourcedValue]
"""Candidates produced by a correspondence function, by sourced attribute name."""


def put(model: SourcedModel, candidates: Candidates, *, source: str, clear: bool = False) -> None:
    """Store ``candidates`` on ``model``, replacing the previous candidates of ``source``.

    Parameters
    ----------
    model
        Entity, system, envelope or component receiving the values.
    candidates
        Candidates by attribute name; each one must come from ``source``.
    source
        Source of the connector.
    clear
        Also remove the ``source`` candidates of the attributes absent from ``candidates``, so
        that a value the source no longer gives disappears.

    Raises
    ------
    ValueError
        If a candidate does not come from ``source`` (programming error in a connector).
    """
    for name, candidate in candidates.items():
        if candidate.source != source:
            raise ValueError(
                f"candidate of {name!r} comes from {candidate.source!r}, not {source!r}"
            )
        model.set(
            name,
            candidate.value,
            source=source,
            date=candidate.date,
            comment=candidate.comment,
            ref=candidate.ref,
            raw=candidate.raw,
        )
    if clear:
        for name in model.sourced_fields():
            if name not in candidates and any(c.source == source for c in model.candidates(name)):
                model.unset(name, source=source)


def system_id(kind: SystemKind | str, owner_id: str, source: str, number: int) -> str:
    """Return the id of the ``number``-th system (from 1) of ``kind`` created by ``source``."""
    return f"{SystemKind(kind)}-{owner_id}-{source}-{number}"


def sync_systems(
    owner: DescribedEntity,
    kind: SystemKind | str,
    source: str,
    produced: Sequence[Candidates],
    report: ImportReport,
) -> list[System]:
    """Make the ``source`` systems of ``kind`` owned by ``owner`` match ``produced``.

    The ``n``-th item of ``produced`` updates (or creates) the system
    ``<kind>-<owner_id>-<source>-<n>``. Systems of this pattern beyond ``len(produced)`` are
    removed, except those referenced by a meter: they are kept and a message is added to
    ``report``. Systems created by other sources or by hand are left untouched.

    Returns
    -------
    list of System
        The systems matching ``produced``, in order.
    """
    kind = SystemKind(kind)
    cls = SYSTEM_CLASSES[kind]
    owned = {s.id: s for s in owner.iter_systems(kind)}
    systems: list[System] = []
    for number, candidates in enumerate(produced, start=1):
        sid = system_id(kind, owner.id, source, number)
        system = owned.get(sid)
        if system is None:
            system = owner.add_system(cls(id=sid))
        put(system, candidates, source=source, clear=True)
        systems.append(system)

    pattern = re.compile(re.escape(f"{kind}-{owner.id}-{source}-") + r"(\d+)$")
    for sid in owned:
        match = pattern.match(sid)
        if match is None or int(match.group(1)) <= len(produced):
            continue
        park = owner.park
        referencing = [] if park is None else sorted({r for r, _ in park._references_to({sid})})
        if referencing:
            report.note(
                owner.id,
                f"system {sid!r} is no longer given by {source} but kept: "
                f"referenced by {referencing}",
            )
        else:
            owner.remove_system(sid)
    return systems


def component_id(source: str, kind: ComponentKind | str) -> str:
    """Return the id of the per-kind component of ``kind`` created by ``source`` (``dpe-wall``)."""
    return f"{source}-{ComponentKind(kind)}"


def sync_components(
    envelope: Envelope,
    source: str,
    produced: Mapping[ComponentKind, Candidates],
) -> list[EnvelopeComponent]:
    """Make the per-kind ``source`` components of ``envelope`` match ``produced``.

    The component ``<source>-<kind>`` of each kind of ``produced`` is updated (or created, as a
    :class:`Window` for windows). The ``<source>-<kind>`` components of the other kinds are
    removed; components created by other sources or by hand are left untouched.

    Returns
    -------
    list of EnvelopeComponent
        The components matching ``produced``, in order.
    """
    existing = {c.id: c for c in envelope.components}
    components: list[EnvelopeComponent] = []
    for kind, candidates in produced.items():
        cid = component_id(source, kind)
        component = existing.get(cid)
        if component is None:
            new = (
                Window(id=cid)
                if kind == ComponentKind.WINDOW
                else EnvelopeComponent(id=cid, kind=kind)
            )
            component = envelope.add_component(new)
        put(component, candidates, source=source, clear=True)
        components.append(component)
    for kind in ComponentKind:
        cid = component_id(source, kind)
        if kind not in produced and cid in existing:
            envelope.remove_component(cid)
    return components


def select_buildings(
    park: BuildingPark, buildings: Iterable[Building | str] | None
) -> Iterator[Building]:
    """Yield the requested buildings, or every building with an ``rnb_id`` when ``None``.

    Raises
    ------
    UnknownIdError
        If an id is not a building of the park.
    """
    if buildings is None:
        yield from (b for b in park.iter_buildings() if b.rnb_id)
        return
    for item in buildings:
        building = park[item] if isinstance(item, str) else item
        if not isinstance(building, Building):
            raise UnknownIdError(f"{item!r} is not a building of the park")
        yield building
