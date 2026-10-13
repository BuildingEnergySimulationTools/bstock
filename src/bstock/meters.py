"""Meters and the tide column convention (SPEC §6.1, §6.3, §6.4)."""

from __future__ import annotations

from typing import Annotated, Any

import pandas as pd
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    SerializeAsAny,
    field_validator,
    model_validator,
)

from bstock._ids import TIDE_SEPARATOR, check_id, new_id
from bstock.enums import EndUse, EnergyType
from bstock.exceptions import DataSourceError, TimeSeriesError
from bstock.provenance import SourcedModel, sourced
from bstock.sources import DataSource, data_source_from_dict, ensure_time_index

__all__ = ["TAG_NAMES", "Channel", "Meter", "tide_column"]

TAG_NAMES = ("bloc", "sub_bloc")
"""Tags of a meter that may be overridden through ``Meter.tags``."""

AnySource = Annotated[SerializeAsAny[DataSource], BeforeValidator(data_source_from_dict)]


def tide_column(name: str, unit: str, bloc: str, sub_bloc: str) -> str:
    """Return the tide column name ``name__unit__bloc__sub_bloc``.

    Raises
    ------
    ValueError
        If a tag is empty or contains the separator ``__``.
    """
    tags = (name, unit, bloc, sub_bloc)
    for tag in tags:
        if not tag or TIDE_SEPARATOR in tag:
            raise ValueError(f"invalid tide tag {tag!r}")
    return TIDE_SEPARATOR.join(tags)


class Channel(BaseModel):
    """One measured quantity of a meter, i.e. one column of its data.

    Parameters
    ----------
    name
        Quantity: ``"energy"``, ``"power"``, ``"index"``, ``"temperature"``...
    unit
        Unit of the values, e.g. ``"kWh"``, ``"W"``, ``"°C"``.
    column
        Column name in the raw source data; defaults to ``name``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    unit: str
    column: str | None = None

    @field_validator("name", "unit")
    @classmethod
    def _check_tag(cls, value: str) -> str:
        if not value or TIDE_SEPARATOR in value:
            raise ValueError(f"{value!r} cannot be empty or contain {TIDE_SEPARATOR!r}")
        return value

    @property
    def source_column(self) -> str:
        """Column read in the raw source data."""
        return self.column or self.name


class Meter(SourcedModel):
    """A meter, sub-meter or sensor concerning one or several entities of the park.

    The data is a DataFrame with a sorted, time-zone aware ``DatetimeIndex`` and one column per
    channel, named after the tide convention ``name__unit__bloc__sub_bloc`` (SPEC §6.4). It is kept
    in memory only: the JSON keeps ``data_source``, from which :meth:`load` reads it.

    Parameters
    ----------
    id
        Unique id in the park (e.g. a delivery point number); generated when omitted.
    scope
        Ids of the measured entities: sites, buildings or systems.
    channels
        Measured quantities, one column each.
    data_source
        Where to read the data.
    original_data_source
        Source the data came from before being saved in the park directory.
    tags
        Overrides of the tide tags ``bloc`` and ``sub_bloc``.
    """

    id: str = Field(default_factory=lambda: new_id("meter"), frozen=True)
    scope: list[str] = Field(default_factory=list)
    channels: list[Channel] = Field(min_length=1)
    data_source: AnySource | None = None
    original_data_source: AnySource | None = None
    tags: dict[str, str] = Field(default_factory=dict)

    name = sourced(str)
    energy_type = sourced(EnergyType)
    usage = sourced(EndUse, description="Measured end use")
    timestep = sourced(str, description="Nominal time step, as a pandas offset ('30min', '1h')")

    _data: pd.DataFrame | None = PrivateAttr(default=None)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return check_id(value)

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, value: list[str]) -> list[str]:
        return [check_id(v) for v in value]

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = set(value) - set(TAG_NAMES)
        if unknown:
            raise ValueError(f"unknown tags {sorted(unknown)}; allowed: {TAG_NAMES}")
        for tag in value.values():
            tide_column("x", "x", tag, "x")  # same validation as any tide tag
        return value

    @model_validator(mode="after")
    def _check_channel_names(self) -> Meter:
        names = [c.name for c in self.channels]
        if len(set(names)) != len(names):
            raise ValueError(f"channel names must be unique, got {names}")
        return self

    def set(self, name: str, value: Any, **provenance: Any) -> Any:
        """Set a sourced attribute (see :meth:`SourcedModel.set`); validates ``timestep``."""
        if name == "timestep" and value is not None:
            try:
                pd.tseries.frequencies.to_offset(value)
            except ValueError as error:
                raise ValueError(f"invalid timestep {value!r}: {error}") from error
        return super().set(name, value, **provenance)

    # --- tide naming --------------------------------------------------------------------------

    @property
    def bloc(self) -> str:
        """Tide ``bloc`` tag: the scope entity if there is only one, else the meter id."""
        return self.tags.get("bloc") or (self.scope[0] if len(self.scope) == 1 else self.id)

    @property
    def sub_bloc(self) -> str:
        """Tide ``sub_bloc`` tag: the measured end use (``"other"`` when unknown)."""
        return self.tags.get("sub_bloc") or str(self.usage or EndUse.OTHER)

    def column_names(self) -> dict[str, str]:
        """Return the tide column name of each channel, by channel name."""
        return {
            c.name: tide_column(f"{self.id}_{c.name}", c.unit, self.bloc, self.sub_bloc)
            for c in self.channels
        }

    # --- data ---------------------------------------------------------------------------------

    @property
    def data(self) -> pd.DataFrame | None:
        """The data in memory, or ``None`` if it has not been loaded or set."""
        return self._data

    @data.setter
    def data(self, frame: pd.DataFrame | None) -> None:
        """Set the data; columns are named by channel name or already in tide format."""
        if frame is None:
            self._data = None
            return
        frame = ensure_time_index(frame)
        names = self.column_names()
        renamed = frame.rename(columns=names)
        expected = list(names.values())
        missing = [c for c in expected if c not in renamed.columns]
        if missing:
            raise TimeSeriesError(
                f"meter {self.id!r}: missing columns {missing} (got {list(frame.columns)})"
            )
        self._data = renamed[expected]

    def load(self) -> pd.DataFrame:
        """Read the data from ``data_source``, keep it in :attr:`data` and return it.

        Each channel reads the column ``channel.column`` (or ``channel.name``); a source with a
        single column feeds a single channel whatever its name.

        Raises
        ------
        DataSourceError
            If there is no data source, or a channel column is missing.
        """
        if self.data_source is None:
            raise DataSourceError(f"meter {self.id!r} has no data source")
        raw = self.data_source.load()
        if len(self.channels) == 1 and raw.shape[1] == 1:
            selected = raw.set_axis([self.channels[0].name], axis=1)
        else:
            missing = [c.source_column for c in self.channels if c.source_column not in raw]
            if missing:
                raise DataSourceError(
                    f"meter {self.id!r}: columns {missing} not found in {list(raw.columns)}"
                )
            selected = raw[[c.source_column for c in self.channels]].set_axis(
                [c.name for c in self.channels], axis=1
            )
        self.data = selected
        assert self._data is not None
        return self._data
