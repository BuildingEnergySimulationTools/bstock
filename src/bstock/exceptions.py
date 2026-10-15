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


class InvalidArgumentError(BstockError, ValueError):
    """An argument of a bstock function has a value outside the accepted ones.

    For example ``geometry="centroid"`` in an export, or a search radius out of range.
    """


class UnknownKindError(InvalidArgumentError):
    """A kind argument is not one of the accepted values.

    For example a system kind outside :class:`~bstock.enums.SystemKind`, or an export kind other
    than ``"building"`` and ``"site"``.
    """


class HierarchyError(BstockError, ValueError):
    """An operation would break the park hierarchy.

    For example: a site moved or attached inside itself, an inheritance cycle, ``serves`` set on
    a system owned by a building, or a list of children (``site.buildings``...) changed in place
    instead of through the park or entity methods.
    """


class ClassMismatchError(BstockError, TypeError):
    """A site or building is not an instance of the class configured on the park (SPEC §5).

    Raised when an entity is attached; :class:`ClassMismatchWarning` is its counterpart when a park
    file is loaded with other classes than the ones it was saved with.
    """


class UnknownFieldError(BstockError, AttributeError):
    """The requested name is not a sourced field of the model."""


class IncompleteDataError(BstockError, ValueError):
    """A computation lacks some of the values it needs."""


class TimeSeriesError(BstockError, ValueError):
    """Metered data does not respect the time series format (SPEC §6.3)."""


class DataReadError(BstockError):
    """The data of a meter cannot be read (no reader, unreadable file, missing column...)."""


class UnsupportedSchemaError(BstockError):
    """The park file was written with an unsupported format version."""


class ConnectorError(BstockError):
    """An API called by a connector failed persistently or returned an unexpected response."""


class MissingDependencyError(BstockError, ImportError):
    """An optional dependency is not installed; the message gives the extra to install."""


class BstockWarning(UserWarning):
    """Base class for all bstock warnings."""


class UnknownFieldWarning(BstockWarning):
    """Loaded data contains a sourced field unknown to the model class; it is kept as is."""


class PerformanceRatioWarning(BstockWarning):
    """A generator performance ratio looks inconsistent with its technology."""


class IdentifierFormatWarning(BstockWarning):
    """An external identifier (RNB, BDNB, DPE) does not have the expected format."""


class NoDataWarning(BstockWarning):
    """A meter selected for a time series has no data in memory and was skipped."""


class ClassMismatchWarning(BstockWarning):
    """A park file was saved with other site or building classes than the ones used to load it."""


class UnmappedValueWarning(BstockWarning):
    """A source label is missing from the correspondence tables; ``raw`` keeps the label."""
