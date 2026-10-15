"""Persistence of a park: JSON, park directories and JSON Schema (SPEC §7).

A park directory holds ``park.json`` and, when saved with ``include_data=True``, one Parquet
file per meter in ``timeseries/``. Relative paths of the readers are relative to the park
directory.
"""

from __future__ import annotations

import json
import warnings
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from bstock.building import Building
from bstock.envelope import Envelope, EnvelopeComponent, Window
from bstock.exceptions import ClassMismatchWarning, UnsupportedSchemaError
from bstock.meters import Meter
from bstock.provenance import SourcedModel
from bstock.readers import DataReader, ParquetReader
from bstock.site import CLASSES_CONTEXT_KEY, Site
from bstock.systems import SYSTEM_CLASSES

if TYPE_CHECKING:
    from bstock.park import BuildingPark

__all__ = [
    "PARK_FILE",
    "SCHEMA_VERSION",
    "TIMESERIES_DIR",
    "from_json",
    "json_schema",
    "load",
    "save",
    "to_json",
]

SCHEMA_VERSION = 2
"""Version of the park file format."""

PARK_FILE = "park.json"
TIMESERIES_DIR = "timeseries"

_METADATA_KEYS = ("bstock_version", "schema_version", "site_cls", "building_cls")


def class_path(cls: type) -> str:
    """Return the import path ``module.QualName`` of ``cls``."""
    return f"{cls.__module__}.{cls.__qualname__}"


def _bstock_version() -> str:
    try:
        return version("bstock")
    except PackageNotFoundError:  # pragma: no cover - package not installed
        return "0.0.0"


# --- JSON -------------------------------------------------------------------------------------


def to_dict(park: BuildingPark) -> dict[str, Any]:
    """Return the JSON-compatible content of ``park.json``."""
    metadata = {
        "bstock_version": _bstock_version(),
        "schema_version": SCHEMA_VERSION,
        "site_cls": class_path(park.site_cls),
        "building_cls": class_path(park.building_cls),
    }
    return {**metadata, **park.model_dump(mode="json", exclude_none=True)}


def to_json(park: BuildingPark, *, indent: int | None = 2) -> str:
    """Serialise ``park`` to JSON; metered data is not included (see :func:`save`)."""
    return json.dumps(to_dict(park), indent=indent, ensure_ascii=False)


def from_json(
    text: str | bytes,
    *,
    site_cls: type[Site] = Site,
    building_cls: type[Building] = Building,
) -> BuildingPark:
    """Load a park from JSON text, instantiating ``site_cls`` and ``building_cls`` everywhere.

    Attributes unknown to the classes are kept with an
    :class:`~bstock.exceptions.UnknownAttributeWarning`, and a :class:`ClassMismatchWarning` is
    emitted when the file was saved with other classes.

    Raises
    ------
    UnsupportedSchemaError
        If the file format version is not supported.
    """
    from bstock.park import BuildingPark  # circular import

    data = json.loads(text)
    metadata = {key: data.pop(key, None) for key in _METADATA_KEYS}
    schema_version = metadata["schema_version"]
    if schema_version is not None and schema_version != SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"park file format {schema_version} is not supported (expected {SCHEMA_VERSION})"
        )
    for key, cls in (("site_cls", site_cls), ("building_cls", building_cls)):
        saved = metadata[key]
        if saved is not None and saved != class_path(cls):
            warnings.warn(
                f"the park was saved with {key}={saved}, loaded with {class_path(cls)}; "
                f"import that class and pass {key}= to load it with its project attributes",
                ClassMismatchWarning,
                stacklevel=3,
            )
    context = {CLASSES_CONTEXT_KEY: (site_cls, building_cls)}
    data = {**data, "site_cls": site_cls, "building_cls": building_cls}
    return BuildingPark.model_validate(data, context=context)


# --- park directories -------------------------------------------------------------------------


def save(park: BuildingPark, directory: str | Path, *, include_data: bool = False) -> Path:
    """Save ``park`` in ``directory`` and return the path of ``park.json``.

    The park in memory is modified so that it describes what was saved: see Notes.

    Parameters
    ----------
    park
        The park to save.
    directory
        Target directory, created if needed.
    include_data
        Also write the data in memory of each meter to ``timeseries/<meter_id>.parquet``. The
        meter ``reader`` then points to that file and its former reader is kept in
        ``original_reader``: the saved park no longer depends on the original files.

    Notes
    -----
    Relative paths of the other readers are made absolute, since relative paths in a park
    file are relative to the park directory. With ``include_data``, the ``reader`` of the
    meters is replaced in memory too: saving again does not rewrite unchanged data from the
    original files, and the park keeps working if the working directory changes.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for meter in park.meters:
        for reader in (meter.reader, meter.original_reader):
            _make_path_absolute(reader)
        if include_data and meter.data is not None:
            relative = Path(TIMESERIES_DIR) / f"{meter.id}.parquet"
            (directory / TIMESERIES_DIR).mkdir(exist_ok=True)
            meter.data.to_parquet(directory / relative)
            if meter.original_reader is None:
                meter.original_reader = meter.reader
            new_reader = ParquetReader(path=relative.as_posix())
            new_reader._base_dir = directory
            meter.reader = new_reader
    path = directory / PARK_FILE
    path.write_text(to_json(park), encoding="utf-8")
    return path


def load(
    directory: str | Path,
    *,
    site_cls: type[Site] = Site,
    building_cls: type[Building] = Building,
) -> BuildingPark:
    """Load a park saved by :func:`save`; readers resolve relative paths in ``directory``.

    ``directory`` is the park directory or its ``park.json`` file, as returned by :func:`save`.
    Metered data is not read: call ``meter.load_data()`` or ``park.timeseries(load_data=True)``.
    """
    directory = Path(directory)
    if directory.is_file():
        directory = directory.parent
    park = from_json(
        (directory / PARK_FILE).read_text(encoding="utf-8"),
        site_cls=site_cls,
        building_cls=building_cls,
    )
    for meter in park.meters:
        for reader in (meter.reader, meter.original_reader):
            if reader is not None:
                reader._base_dir = directory
    return park


def _make_path_absolute(reader: DataReader | None) -> None:
    path = getattr(reader, "path", None)
    if reader is None or not isinstance(path, str) or Path(path).is_absolute():
        return
    if reader._base_dir is None:  # relative to the working directory
        reader.path = str(Path(path).resolve())


# --- JSON Schema ------------------------------------------------------------------------------


def json_schema(
    *, site_cls: type[Site] = Site, building_cls: type[Building] = Building
) -> dict[str, Any]:
    """Return the JSON Schema of ``park.json``.

    Sourced attributes are stored in the ``values`` object of each entity, so the schema lists
    them separately in ``x-bstock-sourced-attributes``: for each class, the attribute type (as a
    JSON Schema), unit, inheritance and extensive flags and description.
    """
    from bstock.park import BuildingPark  # circular import

    schema = BuildingPark.model_json_schema()
    classes: list[type[SourcedModel]] = [
        site_cls,
        building_cls,
        Envelope,
        EnvelopeComponent,
        Window,
        *SYSTEM_CLASSES.values(),
        Meter,
    ]
    schema["x-bstock-sourced-attributes"] = {
        cls.__name__: {
            name: {
                "schema": TypeAdapter(field.type_).json_schema(),
                "unit": field.unit,
                "inherit": field.inherit,
                "extensive": field.extensive,
                "description": field.description,
            }
            for name, field in cls.sourced_fields().items()
        }
        for cls in classes
    }
    schema["x-bstock-schema-version"] = SCHEMA_VERSION
    return schema
