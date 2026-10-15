"""Readers of metered data (SPEC §6.2).

A :class:`DataReader` describes where the data of a meter lives and reads it as a DataFrame with a
time-zone aware, sorted :class:`pandas.DatetimeIndex`. Projects add their own readers by
subclassing :class:`DataReader` with a new ``type`` and implementing :meth:`DataReader.read_raw`;
the subclass is registered as soon as it is imported, so parks saved with it can be loaded back.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, PrivateAttr

from bstock.exceptions import DataReadError, InvalidArgumentError, TimeSeriesError

__all__ = [
    "CsvReader",
    "DataReader",
    "ParquetReader",
    "UnregisteredReader",
]


class DataReader(BaseModel):
    """Base class of data readers.

    Parameters
    ----------
    type
        Discriminator, unique per subclass (``"csv"``, ``"parquet"``...).
    tz
        Time zone used to localise a naive index (e.g. ``"Europe/Paris"``). A naive index without
        ``tz`` is rejected.
    tz_ambiguous, tz_nonexistent
        How to handle the daylight saving time ambiguities when localising, as in
        :meth:`pandas.DatetimeIndex.tz_localize`.
    """

    model_config = ConfigDict(extra="forbid")

    registry: ClassVar[dict[str, type[DataReader]]] = {}

    type: str
    tz: str | None = None
    tz_ambiguous: Literal["raise", "infer", "NaT"] = "raise"
    tz_nonexistent: Literal["raise", "shift_forward", "shift_backward", "NaT"] = "raise"

    _base_dir: Path | None = PrivateAttr(default=None)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Register the subclass under its ``type`` default value.

        Raises
        ------
        InvalidArgumentError
            If the subclass overrides :meth:`read` instead of implementing :meth:`read_raw`.
        """
        super().__pydantic_init_subclass__(**kwargs)
        if "read" in vars(cls):
            raise InvalidArgumentError(
                f"{cls.__name__}: implement read_raw(), not read(), which checks the index"
            )
        type_name = cls.model_fields["type"].default
        if isinstance(type_name, str) and type_name:
            DataReader.registry[type_name] = cls

    def __eq__(self, other: object) -> bool:
        # The base directory is a runtime detail: two readers describing the same data are equal.
        if not isinstance(other, BaseModel):
            return NotImplemented
        return (
            type(self) is type(other)
            and self.__dict__ == other.__dict__
            and (self.__pydantic_extra__ == other.__pydantic_extra__)
        )

    __hash__ = None  # type: ignore[assignment]

    def read_raw(self) -> pd.DataFrame:
        """Read the data as stored; implemented by subclasses, called by :meth:`read`.

        Users call :meth:`read` (or ``meter.load_data()``), which also checks and localises the
        index.
        """
        raise NotImplementedError

    def read(self) -> pd.DataFrame:
        """Return the data with a sorted, time-zone aware ``DatetimeIndex``.

        Raises
        ------
        DataReadError
            If the data cannot be read.
        TimeSeriesError
            If the index is not a datetime index, or is naive while ``tz`` is not set.
        """
        try:
            frame = self.read_raw()
        except (OSError, ValueError, KeyError) as error:
            raise DataReadError(f"cannot read {self!r}: {error}") from error
        return _ensure_time_index(
            frame, tz=self.tz, ambiguous=self.tz_ambiguous, nonexistent=self.tz_nonexistent
        )

    def resolve_path(self, path: str | Path) -> Path:
        """Resolve a relative ``path`` against the base directory set by the park, if any."""
        path = Path(path)
        if not path.is_absolute() and self._base_dir is not None:
            return self._base_dir / path
        return path


class CsvReader(DataReader):
    """CSV file whose index column holds timestamps.

    Timestamps with mixed UTC offsets (typical around daylight saving time changes) are converted
    to UTC.
    """

    type: Literal["csv"] = "csv"
    path: str
    index_col: str | int = 0
    sep: str = ","
    decimal: str = "."
    encoding: str = "utf-8"
    date_format: str | None = None
    dayfirst: bool = False

    def read_raw(self) -> pd.DataFrame:
        """Read the CSV file and parse its index as datetimes."""
        frame = pd.read_csv(
            self.resolve_path(self.path),
            sep=self.sep,
            decimal=self.decimal,
            encoding=self.encoding,
            index_col=self.index_col,
        )
        frame.index = _parse_datetimes(frame.index, self.date_format, self.dayfirst)
        return frame


class ParquetReader(DataReader):
    """Parquet file, with a datetime index or a datetime column given by ``index_col``."""

    type: Literal["parquet"] = "parquet"
    path: str
    index_col: str | None = None

    def read_raw(self) -> pd.DataFrame:
        """Read the Parquet file."""
        frame = pd.read_parquet(self.resolve_path(self.path))
        if self.index_col is not None:
            frame = frame.set_index(self.index_col)
        return frame


class UnregisteredReader(DataReader):
    """Placeholder for a reader whose ``type`` is not registered; it keeps the description as is.

    It typically appears when a park saved with a project reader is loaded without importing the
    project code.
    """

    model_config = ConfigDict(extra="allow")

    def read_raw(self) -> pd.DataFrame:
        """Raise: the reader class is not available."""
        raise DataReadError(
            f"no data reader registered for type {self.type!r}; import the module defining it"
        )


def _reader_from_dict(value: Any) -> Any:
    """Build the reader registered for ``value["type"]``, or an :class:`UnregisteredReader`."""
    if isinstance(value, dict):
        cls = DataReader.registry.get(value.get("type", ""), UnregisteredReader)
        return cls.model_validate(value)
    return value


def _ensure_time_index(
    frame: pd.DataFrame,
    *,
    tz: str | None = None,
    ambiguous: str = "raise",
    nonexistent: str = "raise",
) -> pd.DataFrame:
    """Return ``frame`` with a sorted, time-zone aware ``DatetimeIndex``.

    A naive index is localised to ``tz``; it is rejected when ``tz`` is None.

    Raises
    ------
    TimeSeriesError
        If the index is not a ``DatetimeIndex`` or is naive without ``tz``.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TimeSeriesError(f"expected a pandas DataFrame, got {type(frame).__name__}")
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex):
        raise TimeSeriesError(f"the index must be a DatetimeIndex, got {type(index).__name__}")
    if index.tz is None:
        if tz is None:
            raise TimeSeriesError("the index has no time zone; set one or give tz= to the reader")
        try:
            index = index.tz_localize(tz, ambiguous=ambiguous, nonexistent=nonexistent)
        except (ValueError, TypeError) as error:  # pandas raises ValueError subclasses here
            raise TimeSeriesError(f"cannot localise the index to {tz}: {error}") from error
        frame = frame.set_axis(index)
    if not frame.index.is_monotonic_increasing:
        frame = frame.sort_index()
    return frame


def _parse_datetimes(values: pd.Index, date_format: str | None, dayfirst: bool) -> pd.DatetimeIndex:
    try:
        return pd.DatetimeIndex(pd.to_datetime(values, format=date_format, dayfirst=dayfirst))
    except ValueError as error:
        if "Mixed timezones" not in str(error):
            raise
        return pd.DatetimeIndex(
            pd.to_datetime(values, format=date_format, dayfirst=dayfirst, utc=True)
        )
