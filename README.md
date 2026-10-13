# bstock

Common data structure for building stocks: sites, buildings, envelopes, systems and meters.

- **Multi-source provenance**: every descriptive value keeps its candidates from each source (BDNB,
  DPE, audit, client files...) and a configurable priority picks the effective one.
- **Nested sites**: a client site can contain BDNB building groups; buildings inherit intensive
  values (year, usage, U-value...) from their site, never extensive ones (areas, dwellings).
- **Project extensions**: subclass `Site` and `Building` to add client attributes; the park creates
  and reloads them with your classes.
- **Metered data** in the [tide](https://github.com/BuildingEnergySimulationTools/tide) column format
  `name__unit__bloc__sub_bloc`, read from CSV, Parquet or your own sources.
- **Persistence**: JSON park file, optional Parquet export making a park directory self-contained,
  JSON Schema.

The design reference is [`SPEC.md`](SPEC.md) (in French).

## Installation

```bash
python -m pip install -e .            # library
python -m pip install -e ".[dev]"     # + pytest, ruff
python -m pip install -e ".[plot]"    # + plotly
```

Requires Python ≥ 3.11.

## Quick start

```python
from bstock import Building, BuildingPark, Channel, CsvSource, Meter, Site, sourced


class MyBuilding(Building):            # client attributes, with provenance too
    asset_code = sourced(str)


park = BuildingPark(name="My park", building_cls=MyBuilding)
school = park.new_site("school-A", name="School A")
group = park.new_site("bdnb-bg-1111-1HRT-8SVH", parent=school)
group.set("year_construction", 1972, source="bdnb")

b = park.new_building("A1B2C3D4E5F6", site=group, asset_code="BAT-0042", source="client")
b.set("year_construction", 1974, source="audit")
b.year_construction                    # 1974: own value, audit
b.candidates("year_construction", inherited=True)
# [('A1B2C3D4E5F6', SourcedValue(value=1974, source='audit', ...)),
#  ('bdnb-bg-1111-1HRT-8SVH', SourcedValue(value=1972, source='bdnb', ...))]

park.add_meter(Meter(
    id="PDL_123", scope=["school-A"], end_use="total",
    channels=[Channel(name="power", unit="W", column="valeur")],
    data_source=CsvSource(path="enedis.csv", index_col="horodate", sep=";", tz="Europe/Paris"),
))
df = park.timeseries(load=True)        # columns: PDL_123_power__W__school-A__total

park.save("out/my_park", include_data=True)
park = BuildingPark.load("out/my_park", building_cls=MyBuilding)
```

A complete, runnable walk-through is in [`examples/bayonne.py`](examples/bayonne.py):

```bash
python examples/bayonne.py
```

## Development

```bash
python -m pytest
python -m ruff check --fix .
python -m ruff format .
```

Conventions are in [`CLAUDE.md`](CLAUDE.md), the implementation plan in [`PLAN.md`](PLAN.md).
