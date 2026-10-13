"""Exceptions raised by bstock."""


class BstockError(Exception):
    """Base class for all bstock errors."""


class DuplicateIdError(BstockError):
    """An id (or an RNB id) is already used by another entity of the park."""


class UnknownIdError(BstockError, KeyError):
    """No entity of the park has the requested id."""


class InvalidIdError(BstockError, ValueError):
    """An id does not respect the identifier rules (e.g. it contains ``__``)."""


class EntityInUseError(BstockError):
    """An entity cannot be removed because other entities still reference it."""
