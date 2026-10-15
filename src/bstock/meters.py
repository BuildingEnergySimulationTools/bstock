"""Meters and the tide column convention (SPEC §6.1, §6.3, §6.4)."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

import pandas as pd
from pydantic import (
    AfterValidator,
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
from bstock.exceptions import DataSourceError, InvalidTagError, TimeSeriesError
from bstock.provenance import SourcedModel, sourced
from bstock.sources import DataSource, _data_source_from_dict, _ensure_time_index

__all__ = ["Channel", "Meter", "tide_column"]


def _check_timestep(value: str) -> str:
    try:
        pd.tseries.frequencies.to_offset(value)
    except ValueError as error:
        raise ValueError(f"invalid timestep {value!r}: {error}") from error
    return value


Timestep = Annotated[str, AfterValidator(_check_timestep)]
"""A pandas offset alias such as ``"30min"`` or ``"1h"``."""

AnySource = Annotated[SerializeAsAny[DataSource], BeforeValidator(_data_source_from_dict)]


def tide_column(name: str, unit: str, bloc: str, sub_bloc: str) -> str:
    """Return the tide column name ``name__unit__bloc__sub_bloc``.

    Raises
    ------
    InvalidTagError
        If a tag is empty or contains the separator ``__``.
    """
    tags = (name, unit, bloc, sub_bloc)
    for tag in tags:
        if not tag or TIDE_SEPARATOR in tag:
            raise InvalidTagError(f"invalid tide tag {tag!r}")
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
    in memory only: the JSON keeps ``data_source``, from which :meth:`load_data` reads it.

    Parameters
    ----------
    id
        Unique id in the park (e.g. a delivery point number); generated when omitted.
    scope
        Ids of the measured entities: sites, buildings or systems. In a park, they must exist;
        this is checked when the meter is added and on assignment.
    parent_meter
        Id of the meter this one is a sub-meter of; ``None`` for a head meter. Consumption totals
        only add up head meters, sub-meters split them by end use (SPEC §6.5). In a park, it must
        be another meter of the park and must not create a cycle; this is checked when the meter
        is added and on assignment.
    channels
        Measured quantities, one column each.
    data_source
        Where to read the data.
    original_data_source
        Source the data came from before being saved in the park directory.
    bloc, sub_bloc
        Tide tags of the columns; ``None`` (default) derives them, see :meth:`tide_tags`.
    """

    id: str = Field(default_factory=lambda: new_id("meter"), frozen=True)
    scope: list[str] = Field(default_factory=list)
    parent_meter: str | None = None
    channels: list[Channel] = Field(min_length=1)
    data_source: AnySource | None = None
    original_data_source: AnySource | None = None
    bloc: str | None = None
    sub_bloc: str | None = None

    name = sourced(str)
    energy_type = sourced(EnergyType)
    end_use = sourced(EndUse, description="Measured end use")
    timestep = sourced(
        Timestep, description="Nominal time step, as a pandas offset ('30min', '1h')"
    )

    _data: pd.DataFrame | None = PrivateAttr(default=None)

    _repr_fields: ClassVar[tuple[str, ...]] = ("name", "energy_type", "end_use")

    def _repr_items(self) -> list[tuple[str, str]]:
        items = [*super()._repr_items(), ("scope", repr(self.scope))]
        return items if self._data is None else [*items, ("data", "loaded")]

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return check_id(value)

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, value: list[str]) -> list[str]:
        return [check_id(v) for v in value]

    @field_validator("parent_meter")
    @classmethod
    def _check_parent_meter(cls, value: str | None) -> str | None:
        return None if value is None else check_id(value)

    @field_validator("bloc", "sub_bloc")
    @classmethod
    def _check_tag(cls, value: str | None) -> str | None:
        if value is not None and (not value or TIDE_SEPARATOR in value):
            raise ValueError(f"{value!r} cannot be empty or contain {TIDE_SEPARATOR!r}")
        return value

    @model_validator(mode="after")
    def _check_channel_names(self) -> Meter:
        names = [c.name for c in self.channels]
        if len(set(names)) != len(names):
            raise ValueError(f"channel names must be unique, got {names}")
        return self

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "scope" and self._container is not None:
            self._container._check_scope(self.id, value)
        if name == "parent_meter" and self._container is not None:
            self._container._check_parent_meter(self.id, value)
        super().__setattr__(name, value)

    # --- tide naming --------------------------------------------------------------------------

    def tide_tags(self) -> tuple[str, str]:
        """Return the effective tide tags ``(bloc, sub_bloc)`` of the columns.

        Without an explicit value, ``bloc`` is the scope entity if there is only one, else the
        meter id, and ``sub_bloc`` is the measured end use (``"other"`` when unknown).
        """
        bloc = self.bloc or (self.scope[0] if len(self.scope) == 1 else self.id)
        sub_bloc = self.sub_bloc or str(self.end_use or EndUse.OTHER)
        return bloc, sub_bloc

    def tide_columns(self) -> dict[str, str]:
        """Return the tide column name of each channel, by channel name.

        These are the columns of :attr:`data`; ``Channel.column`` is the column read in the raw
        source data.
        """
        bloc, sub_bloc = self.tide_tags()
        return {
            c.name: tide_column(f"{self.id}_{c.name}", c.unit, bloc, sub_bloc)
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
        frame = _ensure_time_index(frame)
        names = self.tide_columns()
        renamed = frame.rename(columns=names)
        expected = list(names.values())
        missing = [c for c in expected if c not in renamed.columns]
        if missing:
            raise TimeSeriesError(
                f"meter {self.id!r}: missing columns {missing} (got {list(frame.columns)})"
            )
        self._data = renamed[expected]

    def load_data(self) -> pd.DataFrame:
        """Read the data from ``data_source``, keep it in :attr:`data` and return it.

        Columns already in tide format (as saved by the park) are taken as is. Otherwise each
        channel reads the column ``channel.column`` (or ``channel.name``), and a source with a
        single column feeds a single channel whatever its name.

        Raises
        ------
        DataSourceError
            If there is no data source, or a channel column is missing.
        """
        if self.data_source is None:
            raise DataSourceError(f"meter {self.id!r} has no data source")
        raw = self.data_source.load()
        tide_names = list(self.tide_columns().values())
        if all(name in raw.columns for name in tide_names):
            selected = raw[tide_names]
        elif len(self.channels) == 1 and raw.shape[1] == 1:
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
