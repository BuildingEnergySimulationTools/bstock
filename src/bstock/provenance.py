"""Multi-source provenance of descriptive values (SPEC §4).

A *sourced* attribute keeps a list of candidate values, one per source (``"bdnb"``, ``"dpe"``,
``"audit"``, ``"manual"``...). Reading the attribute returns the *effective* value, chosen among the
candidates by a :class:`SourcePriority`.

Sourced attributes are declared without annotation::

    class Building(SourcedModel):
        heated_area = sourced(float, unit="m²", ge=0, description="Heated area")

They are not Pydantic fields: candidates live in the regular ``values`` field, which is also
their JSON representation.
"""

from __future__ import annotations

import datetime
import warnings
from collections.abc import Iterable
from typing import Annotated, Any, ClassVar, Generic, Literal, Self, TypeVar, overload

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, TypeAdapter, model_validator

from bstock.exceptions import UnknownAttributeWarning, UnknownFieldError

__all__ = [
    "DEFAULT_SOURCE_PRIORITY",
    "MANUAL_SOURCE",
    "WILDCARD_SOURCE",
    "SourcePriority",
    "SourcedField",
    "SourcedModel",
    "SourcedValue",
    "sourced",
]

T = TypeVar("T")

MANUAL_SOURCE = "manual"
"""Source used for direct assignment and constructor keyword arguments."""

WILDCARD_SOURCE = "*"
"""Placeholder, in a priority list, for every source that is not listed explicitly."""


class SourcedValue(BaseModel):
    """One candidate value of a sourced attribute, with its provenance.

    Parameters
    ----------
    value
        The value, validated against the attribute type.
    source
        Origin of the information, e.g. ``"bdnb"``, ``"dpe"``, ``"audit"``, ``"bayonne_xlsx_2024"``.
    date
        Date of the information itself (not of its entry in bstock).
    comment
        Free comment.
    ref
        Reference inside the source: DPE number, BDNB id, file name, row...
    raw
        Raw value in the source, before translation (e.g. the original DPE label).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Any
    source: str = Field(min_length=1)
    date: datetime.date | None = None
    comment: str | None = None
    ref: str | None = None
    raw: Any | None = None


class SourcePriority(BaseModel):
    """Rules choosing the effective value among the candidates of an attribute (SPEC §4.3).

    Parameters
    ----------
    default
        Sources from the most to the least trusted. ``"*"`` gives its rank to unlisted sources;
        without it, unlisted sources come after every listed one.
    per_attribute
        Priority lists overriding ``default`` for given attribute names.
    """

    model_config = ConfigDict(extra="forbid")

    default: list[str] = Field(
        default_factory=lambda: [
            "manual",
            "audit",
            "computed",
            "dpe",
            "bdnb",
            "rnb",
            WILDCARD_SOURCE,
            "assumption",
        ]
    )
    per_attribute: dict[str, list[str]] = Field(default_factory=dict)

    def order_for(self, attribute: str) -> list[str]:
        """Return the priority list that applies to ``attribute``."""
        return self.per_attribute.get(attribute, self.default)

    def rank(self, attribute: str, source: str) -> int:
        """Return the rank of ``source`` for ``attribute`` (lower is more trusted)."""
        order = self.order_for(attribute)
        if source in order:
            return order.index(source)
        if WILDCARD_SOURCE in order:
            return order.index(WILDCARD_SOURCE)
        return len(order)

    def select(self, attribute: str, candidates: Iterable[SourcedValue]) -> SourcedValue | None:
        """Return the effective candidate, or ``None`` if there is none.

        Ties on the source rank are broken by the most recent ``date`` (a missing date counts as
        the oldest), then by the candidate added last.
        """
        best: SourcedValue | None = None
        best_key: tuple[int, int, int] | None = None
        for index, candidate in enumerate(candidates):
            date_key = candidate.date.toordinal() if candidate.date is not None else -1
            key = (self.rank(attribute, candidate.source), -date_key, -index)
            if best_key is None or key < best_key:
                best, best_key = candidate, key
        return best


DEFAULT_SOURCE_PRIORITY = SourcePriority()
"""Priority used by entities that are not attached to a park."""


class SourcedField(Generic[T]):
    """Descriptor of a sourced attribute; create it with :func:`sourced`.

    On an instance it returns the effective value (``T | None``); on the class it returns itself,
    which exposes the field definition (``Building.heated_area.unit``).

    Assigning sets the ``"manual"`` candidate. Assigning ``None`` removes that candidate only: the
    attribute may keep a value from another source. Use :meth:`SourcedModel.unset` without
    ``source`` to remove every candidate.
    """

    def __init__(
        self,
        type_: type[T] | Any,
        *,
        unit: str | None = None,
        inherit: bool = False,
        description: str | None = None,
        **constraints: Any,
    ) -> None:
        self.type_ = type_
        self.unit = unit
        self.inherit = inherit
        self.description = description
        self.constraints = constraints
        self.name = ""
        self._adapter: TypeAdapter[Any] | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    @overload
    def __get__(self, obj: None, owner: type | None = None) -> Self: ...

    @overload
    def __get__(self, obj: SourcedModel, owner: type | None = None) -> T | None: ...

    def __get__(self, obj: SourcedModel | None, owner: type | None = None) -> Self | T | None:
        if obj is None:
            return self
        candidate = obj.effective(self.name)
        return None if candidate is None else candidate.value

    def __set__(self, obj: SourcedModel, value: T | None) -> None:
        obj.set(self.name, value, source=MANUAL_SOURCE)

    def validate(self, value: Any) -> Any:
        """Validate ``value`` against the field type and constraints and return it, coerced.

        Raises
        ------
        pydantic.ValidationError
            If the value is invalid.
        """
        if self._adapter is None:
            self._adapter = TypeAdapter(Annotated[self.type_, Field(**self.constraints)])
        return self._adapter.validate_python(value)

    def __repr__(self) -> str:
        type_name = getattr(self.type_, "__name__", repr(self.type_))
        return (
            f"SourcedField({self.name!r}, {type_name}, unit={self.unit!r}, inherit={self.inherit})"
        )


def sourced(
    type_: type[T] | Any,
    *,
    unit: str | None = None,
    inherit: bool = False,
    description: str | None = None,
    **constraints: Any,
) -> SourcedField[T]:
    """Declare a sourced attribute on a :class:`SourcedModel` subclass.

    Parameters
    ----------
    type_
        Type of the value, e.g. ``float``, ``int``, ``EnergyType``, ``list[str]``.
    unit
        SI unit of the value, e.g. ``"m²"``, ``"W/(m².K)"``, ``"kW"``. ``None`` for dimensionless or
        non-physical values.
    inherit
        Whether an entity without own value takes the value of its parent site (intensive quantities
        only, SPEC §4.4).
    description
        Human readable description.
    **constraints
        Pydantic ``Field`` constraints such as ``ge``, ``gt``, ``le``, ``lt``, ``min_length``.

    Returns
    -------
    SourcedField
        The descriptor, to assign to a class attribute without annotation.
    """
    return SourcedField(type_, unit=unit, inherit=inherit, description=description, **constraints)


class SourcedModel(BaseModel):
    """Base class of every bstock entity carrying sourced attributes.

    Constructor keyword arguments named after sourced attributes become candidates of the source
    given by ``source`` (``"manual"`` by default); ``None`` values are ignored::

        Building(rnb_id="A1B2C3D4E5F6", year_construction=1970, source="bdnb")
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        ignored_types=(SourcedField,),
    )

    values: dict[str, list[SourcedValue]] = Field(default_factory=dict)
    """Candidates of every sourced attribute, by attribute name (low-level storage)."""

    __sourced_fields__: ClassVar[dict[str, SourcedField[Any]]] = {}
    # Owning object (entity or park); a park exposes ``source_priority``. Set by the owners.
    _container: Any = PrivateAttr(default=None)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Collect the sourced fields declared on the class and its bases."""
        super().__pydantic_init_subclass__(**kwargs)
        fields: dict[str, SourcedField[Any]] = {}
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                if isinstance(attr, SourcedField):
                    fields[name] = attr
        reserved = set(cls.model_fields) | set(dir(SourcedModel))
        clashes = sorted(set(fields) & reserved)
        if clashes:
            raise TypeError(f"{cls.__name__}: sourced attribute names {clashes} are reserved")
        cls.__sourced_fields__ = fields

    @classmethod
    def sourced_fields(cls) -> dict[str, SourcedField[Any]]:
        """Return the sourced fields of the class, by name."""
        return dict(cls.__sourced_fields__)

    @model_validator(mode="before")
    @classmethod
    def _collect_sourced_kwargs(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        fields = cls.__sourced_fields__
        if not any(key in fields for key in data) and "source" not in data:
            return data
        data = dict(data)
        source = data.pop("source", None) or MANUAL_SOURCE
        values = {name: list(candidates) for name, candidates in data.get("values", {}).items()}
        for name in [key for key in data if key in fields]:
            value = data.pop(name)
            if value is None:
                continue
            candidate = value if isinstance(value, SourcedValue) else {"value": value}
            if isinstance(candidate, dict):
                candidate = {**candidate, "source": source}
            values[name] = [
                c for c in values.get(name, []) if _source_of(c) != _source_of(candidate)
            ] + [candidate]
        data["values"] = values
        return data

    @model_validator(mode="after")
    def _validate_candidates(self) -> Self:
        fields = self.__sourced_fields__
        for name, candidates in self.values.items():
            field = fields.get(name)
            if field is None:
                warnings.warn(
                    f"{type(self).__name__} has no sourced attribute {name!r}; "
                    "its values are kept as is",
                    UnknownAttributeWarning,
                    stacklevel=2,
                )
                continue
            self.values[name] = [
                c.model_copy(update={"value": field.validate(c.value)}) for c in candidates
            ]
        return self

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.__sourced_fields__:
            self.__sourced_fields__[name].__set__(self, value)
        else:
            super().__setattr__(name, value)

    def __eq__(self, other: object) -> bool:
        # Compare declared fields only: private back-references would recurse through the tree.
        if not isinstance(other, BaseModel):
            return NotImplemented
        return type(self) is type(other) and self.__dict__ == other.__dict__

    __hash__ = None  # type: ignore[assignment]

    def _field(self, name: str) -> SourcedField[Any]:
        try:
            return self.__sourced_fields__[name]
        except KeyError:
            raise UnknownFieldError(
                f"{type(self).__name__} has no sourced attribute {name!r}"
            ) from None

    def active_source_priority(self) -> SourcePriority:
        """Return the priority in force: the one of the park owning this entity, or the default."""
        node: Any = self._container
        while node is not None:
            priority = getattr(node, "source_priority", None)
            if isinstance(priority, SourcePriority):
                return priority
            node = getattr(node, "_container", None)
        return DEFAULT_SOURCE_PRIORITY

    def set(
        self,
        name: str,
        value: Any,
        *,
        source: str = MANUAL_SOURCE,
        date: datetime.date | None = None,
        comment: str | None = None,
        ref: str | None = None,
        raw: Any = None,
    ) -> SourcedValue | None:
        """Add or replace the candidate of ``source`` for the attribute ``name``.

        Parameters
        ----------
        name
            Sourced attribute name.
        value
            New value. ``None`` removes the candidate of ``source`` instead.
        source, date, comment, ref, raw
            Provenance of the value, see :class:`SourcedValue`.

        Returns
        -------
        SourcedValue or None
            The stored candidate, or ``None`` when ``value`` is ``None``.

        Raises
        ------
        UnknownFieldError
            If ``name`` is not a sourced attribute.
        pydantic.ValidationError
            If ``value`` does not match the attribute type or constraints.
        """
        field = self._field(name)
        if value is None:
            self.unset(name, source=source)
            return None
        candidate = SourcedValue(
            value=field.validate(value),
            source=source,
            date=date,
            comment=comment,
            ref=ref,
            raw=raw,
        )
        others = [c for c in self.values.get(name, []) if c.source != source]
        self.values[name] = [*others, candidate]
        return candidate

    def unset(self, name: str, *, source: str | None = None) -> None:
        """Remove the candidate of ``source`` for ``name`` (every candidate if ``source`` is None).

        Raises
        ------
        UnknownFieldError
            If ``name`` is not a sourced attribute.
        """
        self._field(name)
        if source is None:
            self.values.pop(name, None)
            return
        remaining = [c for c in self.values.get(name, []) if c.source != source]
        if remaining:
            self.values[name] = remaining
        else:
            self.values.pop(name, None)

    @overload
    def candidates(self, name: str, *, inherited: Literal[False] = False) -> list[SourcedValue]: ...

    @overload
    def candidates(
        self, name: str, *, inherited: Literal[True]
    ) -> list[tuple[str | None, SourcedValue]]: ...

    def candidates(
        self, name: str, *, inherited: bool = False
    ) -> list[SourcedValue] | list[tuple[str | None, SourcedValue]]:
        """Return the candidates of ``name``, in insertion order.

        Parameters
        ----------
        name
            Attribute name. Names unknown to the class but present in loaded data are accepted.
        inherited
            If False (default), return the own candidates only. If True, return
            ``(entity_id, candidate)`` pairs for this entity and, for an inherited attribute, for
            every ancestor up the inheritance chain, nearest first.

        Raises
        ------
        UnknownFieldError
            If ``name`` is neither a sourced attribute nor present in ``values``.
        """
        if name not in self.values:
            self._field(name)
        if not inherited:
            return list(self.values.get(name, []))
        return [
            (node.entity_id(), candidate)
            for node in self._inheritance_chain(name)
            for candidate in node.values.get(name, [])
        ]

    def effective(self, name: str) -> SourcedValue | None:
        """Return the candidate retained for ``name``, or ``None``.

        The source priority chooses among the own candidates. For an inherited attribute without
        own candidate, the nearest ancestor having one provides the value (SPEC §4.4).

        Raises
        ------
        UnknownFieldError
            If ``name`` is not a sourced attribute.
        """
        provider = self._provider(name)
        if provider is None:
            return None
        return provider.active_source_priority().select(name, provider.values[name])

    def provider_id(self, name: str) -> str | None:
        """Return the id of the entity providing the effective value of ``name``, or ``None``.

        Raises
        ------
        UnknownFieldError
            If ``name`` is not a sourced attribute.
        """
        provider = self._provider(name)
        return None if provider is None else provider.entity_id()

    def entity_id(self) -> str | None:
        """Return the id identifying this object, or the one of its owner if it has none."""
        own_id = getattr(self, "id", None)
        if isinstance(own_id, str):
            return own_id
        owner = self._container
        return owner.entity_id() if isinstance(owner, SourcedModel) else None

    def inheritance_parent(self) -> SourcedModel | None:
        """Return the object this one inherits values from, or ``None``.

        The base implementation inherits from nothing. Sites, buildings and envelopes override it.
        """
        return None

    def _inheritance_chain(self, name: str) -> list[SourcedModel]:
        """Return self, then the ancestors that also declare ``name``, if it is inherited."""
        field = self._field(name) if name not in self.values else self.__sourced_fields__.get(name)
        chain: list[SourcedModel] = [self]
        if field is None or not field.inherit:
            return chain
        node = self.inheritance_parent()
        while node is not None and name in node.__sourced_fields__:
            if any(node is seen for seen in chain):
                raise RuntimeError(f"inheritance cycle detected on {name!r}")
            chain.append(node)
            node = node.inheritance_parent()
        return chain

    def _provider(self, name: str) -> SourcedModel | None:
        """Return the nearest entity of the inheritance chain having candidates for ``name``."""
        self._field(name)
        return next((node for node in self._inheritance_chain(name) if node.values.get(name)), None)


def _source_of(candidate: SourcedValue | dict[str, Any]) -> Any:
    if isinstance(candidate, SourcedValue):
        return candidate.source
    return candidate.get("source")
