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


class InvalidTagError(BstockError, ValueError):
    """A tide tag is empty or contains the separator ``__``."""


class UnknownKindError(BstockError, ValueError):
    """A system kind is not one of :class:`~bstock.enums.SystemKind`."""


class HierarchyError(BstockError, ValueError):
    """An operation would break the park hierarchy.

    For example: a site moved or attached inside itself, an inheritance cycle, or ``serves`` set on
    a system owned by a building.
    """


class ProjectClassError(BstockError, TypeError):
    """A site or building is not an instance of the class configured on the park (SPEC §5)."""


class UnknownFieldError(BstockError, AttributeError):
    """The requested name is not a sourced attribute of the model."""


class IncompleteDataError(BstockError, ValueError):
    """A computation lacks some of the values it needs."""


class TimeSeriesError(BstockError, ValueError):
    """Metered data does not respect the time series format (SPEC §6.3)."""


class DataSourceError(BstockError):
    """A data source cannot be loaded."""


class UnsupportedSchemaError(BstockError):
    """The park file was written with an unsupported format version."""


class BstockWarning(UserWarning):
    """Base class for all bstock warnings."""


class UnknownAttributeWarning(BstockWarning):
    """Loaded data contains a sourced attribute unknown to the model class; it is kept as is."""


class PerformanceRatioWarning(BstockWarning):
    """A generator performance ratio looks inconsistent with its technology."""


class IdentifierFormatWarning(BstockWarning):
    """An external identifier (RNB, BDNB, DPE) does not have the expected format."""


class NoDataWarning(BstockWarning):
    """A meter selected for a time series has no data in memory and was skipped."""


class ClassMismatchWarning(BstockWarning):
    """A park file was saved with other site or building classes than the ones used to load it."""
