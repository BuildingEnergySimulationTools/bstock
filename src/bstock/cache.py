"""Incremental Parquet cache of the time series fetched from an API (SPEC §6.6).

A project downloads the data of a meter once, keeps it in a Parquet file and reads it with a
:class:`bstock.ParquetReader`: loading the park never calls the API. :func:`update_cache` only
fetches what the file lacks over the requested window, merges it and records the cached period
in the file metadata.

Example::

    def fetch(start, end):  # data of [start, end) from the API, time-zone aware index
        ...

    update_cache("cache/PDL_123.parquet", fetch, start="2022-01-01", end="2026-10-19",
                 timestep="1D", chunk="365D", tz="Europe/Paris")
    meter.reader = ParquetReader(path="cache/PDL_123.parquet")
"""

from __future__ import annotations

import itertools
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from bstock.exceptions import InvalidArgumentError
from bstock.readers import _ensure_time_index

__all__ = ["CacheUpdate", "read_period", "update_cache"]

logger = logging.getLogger(__name__)

PERIOD_KEY = b"bstock.period"
"""Key of the cached period ``[start, end)`` in the Parquet schema metadata (two UTC ISO 8601
timestamps, JSON)."""

TimeLike = str | date | datetime | pd.Timestamp
Fetch = Callable[[pd.Timestamp, pd.Timestamp], pd.DataFrame]
"""Function returning the data of ``[start, end)``, with a time-zone aware ``DatetimeIndex``."""
Period = tuple[pd.Timestamp, pd.Timestamp]


@dataclass(frozen=True)
class CacheUpdate:
    """What :func:`update_cache` did on one file.

    Attributes
    ----------
    path
        Cache file.
    fetched
        Windows ``(start, end)`` passed to the fetch function, in order.
    rows
        Number of rows received from ``fetch``.
    period
        Cached period ``[start, end)`` after the update, in the ``tz`` of the update; ``None``
        while nothing was received.
    """

    path: Path
    fetched: list[Period] = field(default_factory=list)
    rows: int = 0
    period: Period | None = None


def read_period(path: str | Path, tz: str = "UTC") -> Period | None:
    """Return the cached period ``[start, end)`` of a cache file; ``None`` if there is none.

    The period is the one queried successfully, not the span of the data: a day without data
    inside it is a real gap, not a day to fetch again.

    Parameters
    ----------
    path
        Cache file written by :func:`update_cache`.
    tz
        Time zone of the returned timestamps.
    """
    path = Path(path)
    if not path.exists():
        return None
    metadata = pq.read_schema(path).metadata or {}
    if PERIOD_KEY not in metadata:
        return None
    first, last = json.loads(metadata[PERIOD_KEY])
    return pd.Timestamp(first).tz_convert(tz), pd.Timestamp(last).tz_convert(tz)


def update_cache(
    path: str | Path,
    fetch: Fetch,
    *,
    start: TimeLike,
    end: TimeLike,
    timestep: str | timedelta,
    chunk: str | timedelta | None = None,
    tz: str = "UTC",
) -> CacheUpdate:
    """Fetch the data missing from a Parquet cache file over ``[start, end)`` and merge it.

    Only what the cached period lacks is fetched, so that it stays one contiguous period: from
    ``start`` to its start, and from its end to ``end`` (a window that does not touch the cached
    period is extended up to it). The cached period ends one ``timestep`` after the last sample
    received, so that recent data not yet published is asked again at the next update; a window
    that returns nothing is not recorded (it is asked again), except before the cached period,
    where the whole window is recorded once fetched. Received rows replace the cached rows with
    the same timestamp; rows returned by ``fetch`` outside the window it was asked are ignored.

    If ``fetch`` raises, the data received so far is saved and the exception propagates: the
    caller decides whether to go on with other files (e.g. one error per meter in a report).

    Parameters
    ----------
    path
        Parquet file, created if needed (with its directory).
    fetch
        Function ``fetch(start, end)`` returning the data of ``[start, end)`` as a DataFrame with
        a time-zone aware ``DatetimeIndex`` (an empty DataFrame when there is none).
    start, end
        Window to cover; naive values are read in ``tz``.
    timestep
        Time step of the data (``"1D"``, ``"30min"``...): the last sample covers it.
    chunk
        Longest window passed to ``fetch`` (e.g. ``"365D"`` for an API limited to one year);
        ``None``: no limit.
    tz
        Time zone of naive ``start`` and ``end``, of the windows passed to ``fetch`` (chunk
        boundaries follow its calendar) and of the returned period.

    Returns
    -------
    CacheUpdate
        Windows fetched, rows received and cached period.

    Raises
    ------
    InvalidArgumentError
        If ``start >= end``, or ``timestep`` or ``chunk`` is not a valid duration.
    TimeSeriesError
        If ``fetch`` returns data without a time-zone aware ``DatetimeIndex``.
    """
    path = Path(path)
    first, last = _timestamp(start, tz), _timestamp(end, tz)
    if first >= last:
        raise InvalidArgumentError(f"start ({first}) must be before end ({last})")
    step = _offset(timestep, "timestep")
    size = None if chunk is None else _offset(chunk, "chunk")
    first, last = first.tz_convert(tz), last.tz_convert(tz)
    period = read_period(path, tz)
    frame = pd.read_parquet(path) if path.exists() else None
    fetched: list[Period] = []
    rows = 0
    changed = False
    try:
        for window_start, window_end, leading in _missing(period, first, last):
            for chunk_start, chunk_end in _chunks(window_start, window_end, size):
                new = _fetched(fetch, chunk_start, chunk_end)
                fetched.append((chunk_start, chunk_end))
                rows += len(new)
                if new.empty:
                    continue
                frame = new if frame is None or frame.empty else new.combine_first(frame)
                changed = True
                if not leading:
                    data_end = min(window_end, new.index.max() + step)
                    begin = window_start if period is None else period[0]
                    period = (begin, data_end if period is None else max(period[1], data_end))
            if leading:
                assert period is not None
                period = (window_start, period[1])
                changed = True
    finally:
        if changed and frame is not None:
            _write(path, frame, period)
    logger.info("%s: %d windows fetched, %d rows", path.name, len(fetched), rows)
    return CacheUpdate(path=path, fetched=fetched, rows=rows, period=period)


def _timestamp(value: TimeLike, tz: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(tz) if stamp.tzinfo is None else stamp


def _offset(value: str | timedelta, name: str) -> pd.DateOffset:
    try:
        return pd.tseries.frequencies.to_offset(value)
    except ValueError as error:
        raise InvalidArgumentError(f"invalid {name} {value!r}: {error}") from error


def _missing(
    period: Period | None, first: pd.Timestamp, last: pd.Timestamp
) -> list[tuple[pd.Timestamp, pd.Timestamp, bool]]:
    """Return the windows to fetch ``(start, end, leading)``; leading: before the cached period.

    Windows always touch the cached period, which thus stays contiguous.
    """
    if period is None:
        return [(first, last, False)]
    windows = []
    if first < period[0]:
        windows.append((first, period[0], True))
    if period[1] < last:
        windows.append((period[1], last, False))
    return windows


def _chunks(
    first: pd.Timestamp, last: pd.Timestamp, size: pd.DateOffset | None
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if size is None:
        return [(first, last)]
    bounds = [first]
    while bounds[-1] + size < last:
        bounds.append(bounds[-1] + size)
    bounds.append(last)
    return list(itertools.pairwise(bounds))


def _fetched(fetch: Fetch, first: pd.Timestamp, last: pd.Timestamp) -> pd.DataFrame:
    """Call ``fetch`` and keep the rows of ``[first, last)``."""
    logger.debug("fetching %s -> %s", first, last)
    new = fetch(first, last)
    if new.empty:
        return new
    new = _ensure_time_index(new)
    return new[(new.index >= first) & (new.index < last)]


def _write(path: Path, frame: pd.DataFrame, period: Period | None) -> None:
    """Write ``frame`` and its cached period atomically (a temporary file renamed)."""
    table = pa.Table.from_pandas(frame.sort_index())
    metadata = dict(table.schema.metadata or {})
    if period is not None:
        utc = [t.tz_convert("UTC").isoformat() for t in period]
        metadata[PERIOD_KEY] = json.dumps(utc).encode()
    table = table.replace_schema_metadata(metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    pq.write_table(table, temporary)
    os.replace(temporary, path)
