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


class UnknownFieldError(BstockError, AttributeError):
    """The requested name is not a sourced attribute of the model."""


class IncompleteDataError(BstockError, ValueError):
    """A computation lacks some of the values it needs."""


class BstockWarning(UserWarning):
    """Base class for all bstock warnings."""


class UnknownAttributeWarning(BstockWarning):
    """Loaded data contains a sourced attribute unknown to the model class; it is kept as is."""
