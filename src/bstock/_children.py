"""Links between owners and their children: protected child lists, deep copies (SPEC §3.3)."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any, NoReturn, SupportsIndex, TypeVar

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from bstock.exceptions import HierarchyError

T = TypeVar("T")


class ChildList(list[T]):
    """A list of child entities that refuses in-place changes.

    Adding, removing or reordering items directly would bypass the park id index, the parent links
    and the consistency checks, so every mutating method raises :class:`HierarchyError` naming the
    methods to use instead. Reading (indexing, iteration, ``==``, ``len``) works as for a list.
    The owner methods change the list with :func:`append_child` and :func:`remove_child`.

    Parameters
    ----------
    items
        Initial items.
    name
        Name of the field holding the list, cited in the error message.
    hint
        Methods to use instead, cited in the error message, e.g. ``"envelope.add_component"``.
    """

    __slots__ = ("_hint", "_name")

    def __init__(self, items: Iterable[T] = (), name: str = "", hint: str = "") -> None:
        list.__init__(self, items)
        self._name = name
        self._hint = hint

    def _refuse(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise HierarchyError(f"{self._name!r} cannot be modified in place; use {self._hint}")

    append = extend = insert = remove = pop = clear = sort = reverse = _refuse  # type: ignore[assignment]
    __setitem__ = __delitem__ = __iadd__ = __imul__ = _refuse  # type: ignore[assignment]

    def __reduce_ex__(self, protocol: SupportsIndex) -> tuple[Any, ...]:
        # copy and pickle rebuild a list subclass with append/extend: rebuild it in one go instead
        return (ChildList, (list(self), self._name, self._hint))


def append_child(children: list[T], child: T) -> None:
    """Append ``child`` to ``children``, bypassing the :class:`ChildList` guard."""
    list.append(children, child)


def remove_child(children: list[T], child: T) -> None:
    """Remove ``child`` from ``children``, bypassing the :class:`ChildList` guard."""
    list.remove(children, child)


def guard_assignment(owner: Any, name: str) -> None:
    """Refuse the assignment of the child list ``name`` of ``owner``, if it is one.

    Raises
    ------
    HierarchyError
        If ``name`` is a child list of ``owner`` (listed in its ``_child_hints``).
    """
    hint = type(owner)._child_hints.get(name)
    if hint is not None:
        raise HierarchyError(f"{name!r} cannot be replaced; use {hint}")


def wrap_children(owner: type, name: str, value: list[T]) -> ChildList[T]:
    """Return ``value`` as a :class:`ChildList` with the hint of the field ``name`` of ``owner``."""
    return ChildList(value, name, owner._child_hints[name])  # type: ignore[attr-defined]


M = TypeVar("M", bound=BaseModel)


def deepcopy_linked(model: M, memo: dict[int, Any] | None) -> M:
    """Deep copy ``model``, keeping the owner links inside the copied tree.

    Pydantic copies the private attributes without registering the copy in ``memo`` first, so the
    ``_container`` link of a child made another copy of its owner. Here the copy is registered
    first, and an owner outside the copied tree is not copied: a copy of an entity alone is
    detached (no parent, no park).
    """
    memo = {} if memo is None else memo
    private = model.__pydantic_private__ or {}
    owner = private.get("_container")
    if owner is not None and id(owner) not in memo:
        memo[id(owner)] = None
    copied = type(model).__new__(type(model))
    memo[id(model)] = copied
    kept = {k: v for k, v in private.items() if v is not PydanticUndefined}
    object.__setattr__(copied, "__dict__", copy.deepcopy(model.__dict__, memo))
    object.__setattr__(copied, "__pydantic_extra__", copy.deepcopy(model.__pydantic_extra__, memo))
    object.__setattr__(copied, "__pydantic_fields_set__", set(model.__pydantic_fields_set__))
    object.__setattr__(copied, "__pydantic_private__", copy.deepcopy(kept, memo))
    return copied
