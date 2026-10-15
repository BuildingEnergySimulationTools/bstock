# bstock

Common data structure for building stocks: sites, buildings, envelopes, systems and meters.

- **Multi-source provenance**: every descriptive value keeps its candidates from each source (BDNB,
  DPE, audit, client files...) and a configurable priority picks the effective one.
- **Nested sites**: a client site can contain BDNB building groups; buildings inherit intensive
  values (year, usage, U-value...) from their site, never extensive ones (areas, dwellings).
- **Project extensions**: subclass `Site` and `Building` to add client fields; the park creates
  and reloads them with your classes.
- **Metered data** in the [tide](https://github.com/BuildingEnergySimulationTools/tide) column format
  `name__unit__bloc__sub_bloc`, read from CSV, Parquet or your own readers.
- **Persistence**: JSON park file, optional Parquet export making a park directory self-contained,
  JSON Schema.
- **Connectors** to the French public APIs: RNB (building register), BDNB (building database) and DPE
  (energy performance certificates, ADEME), with RNB match suggestions for buildings without RNB id.
- **Exports**: DataFrame with provenance, GeoDataFrame, GeoPackage and GeoParquet; footprints as
  GeoJSON with a dependency-free area computation.
- **Selections**: `park.select(usage="tertiary", construction_year__lt=1975, within="school-A")`
  filters buildings or sites on their effective values, with operators, structure criteria and set
  operations.
- **Analysis**: sums of extensive quantities (areas, dwellings, system powers) per site without
  double counting a site value and its buildings', completeness and provenance reports, conflicts
  between sources.
- **Measured consumption**: final energy per site or building, energy and end use over a period or
  per calendar month or year, sub-meters without double count, kWh/m²; nothing is integrated or
  extrapolated, a coverage shows the missing data.
- **Figures** (plotly): completeness, sources, sums per site, distributions, maps of points or
  footprints on OpenStreetMap, consumption and kWh/m².
- **Readable in a console or a notebook**: a park or a site displays as a tree (foldable HTML in
  Jupyter), other entities on one line, and `describe()` lists the effective values with their
  source.

The design reference is [`SPEC.md`](SPEC.md) (in French).

## Installation

```bash
python -m pip install -e .            # library
python -m pip install -e ".[dev]"     # + pytest, ruff
python -m pip install -e ".[plot]"    # + plotly: figures
python -m pip install -e ".[connectors]"  # + requests: RNB, BDNB and DPE connectors
python -m pip install -e ".[geo]"     # + shapely, geopandas, pyproj: BDNB geometries, geo exports
```

Requires Python ≥ 3.11.

## Quick start

```python
from bstock import Building, BuildingPark, Channel, CsvReader, Meter, Site, sourced


class MyBuilding(Building):            # client fields, with provenance too
    asset_code = sourced(str)


park = BuildingPark(name="My park", building_cls=MyBuilding)
school = park.new_site("school-A", name="School A")
group = park.new_site("bdnb-bg-1111-1HRT-8SVH", parent=school)
group.set("construction_year", 1972, source="bdnb")

b = park.new_building(rnb_id="A1B2C3D4E5F6", parent=group, asset_code="BAT-0042", source="client")
b.set("construction_year", 1974, source="audit")
b.construction_year                    # 1974: own value, audit
b.construction_year = None             # removes the "manual" candidate only: still 1974
b.inherited_candidates("construction_year")
# [('A1B2C3D4E5F6', SourcedValue(value=1974, source='audit', ...)),
#  ('bdnb-bg-1111-1HRT-8SVH', SourcedValue(value=1972, source='bdnb', ...))]

park.add_meter(Meter(
    id="PDL_123", scope=["school-A"], end_use="total",
    channels=[Channel(name="power", unit="W", column="valeur")],
    reader=CsvReader(path="enedis.csv", index_col="horodate", sep=";", tz="Europe/Paris"),
))
df = park.timeseries(load_data=True)        # columns: PDL_123_power__W__school-A__total

park.save("out/my_park", include_data=True)
park = BuildingPark.load("out/my_park", building_cls=MyBuilding)
```

## Looking at a park

```python
park                                   # tree of the sites and buildings, cut to 30 lines
# BuildingPark 'My park': 2 sites, 1 building, 1 meter
# └── [site] school-A  School A  (1 building)
#     └── [site] bdnb-bg-1111-1HRT-8SVH  (1 building)
#         └── [building] A1B2C3D4E5F6

park.tree(systems=True, meters=True, fields=["construction_year"])   # full tree, with options
park["A1B2C3D4E5F6"]                   # MyBuilding(id='A1B2C3D4E5F6', parent='bdnb-bg-1111-1HRT-8SVH')
park["A1B2C3D4E5F6"].describe()        # value, unit, source, provider, inherited? per field
```

In Jupyter, the park, its sites and `tree()` display as a foldable HTML tree.

## Selecting buildings

```python
sel = park.select(usage="tertiary", construction_year__lt=1975)    # effective values, inherited too
sel = sel.filter(heated_area__isnull=False)                         # narrow it down
park.select(within="school-A") - park.select(has_rnb_id=True)     # set operations: | & -
park.select(lambda b: (b.height or 0) > 10, has_system="heating")  # any predicate, structure criteria
park.select(entity="site", parent=None)                              # top-level sites

len(sel), list(sel), sel.ids
sel.to_dataframe(provenance=True)      # the rows of the selected buildings
sel.tree(fields=["construction_year"]) # the selected buildings and their sites
```

Operators: `__in`, `__lt`, `__le`, `__gt`, `__ge`, `__isnull`, `__contains`. Structure criteria:
`within`, `parent`, `has_rnb_id`, `has_meter`, `has_system`. A selection is a snapshot of ids: select
again after changing the park.

## Analysing a selection

```python
sel = park.select()
sel.aggregate(["heated_area"])         # per site: sum of the buildings, site value, delta, coverage,
                                       # total (prefer="buildings" or "site"), then a "__park__" row
sel.aggregate(by="usage")              # extensive fields summed per usage
sel.completeness()                     # per field: filled, inherited, total, rate
sel.completeness(per="entity")         # per building: what is missing
sel.provenance(normalize=True)         # share of each source in the effective values
sel.conflicts(rtol=0.05)               # own candidates that disagree (floats within 5 %)
```

A site may have its own value (a DPE of the whole block) and its buildings theirs: the aggregation
shows both and never adds them up. Missing values are never estimated, the `coverage` shows them.
Project fields declared `sourced(..., extensive=True)` are aggregated too.

## Measured consumption

```python
gas = park.add_meter(Meter(id="GAZ_A", scope=["school-A"], energy_type="natural_gas",
                           end_use="total", channels=[Channel(name="energy", unit="kWh")]))
park.add_meter(Meter(id="GAZ_A_ECS", scope=["school-A"], parent_meter="GAZ_A", end_use="dhw",
                     energy_type="natural_gas", channels=[Channel(name="energy", unit="kWh")]))

sel = park.select()
year = {"start": "2024-01-01", "end": "2025-01-01"}
sel.consumption(**year)                       # kWh per entity, energy and end use, with coverage
sel.consumption(**year, freq="MS")            # per calendar month (Europe/Paris by default)
sel.consumption(**year, breakdown=True)       # head meters split by the end use of their sub-meters
sel.consumption(**year, split="heated_area")  # site meters shared between buildings (estimated)
sel.intensity(**year)                         # + area (m²) and intensity (kWh/m²)
```

Only head meters are added up (`parent_meter=None`). An `energy` channel is summed, an `index`
channel gives its last reading minus the first; power is not integrated: convert it with tide first.
A meter appears at the level of its scope: a site meter on the site row.

## Figures

With the `[plot]` extra, `sel.plot` builds plotly figures; each method returns a
`plotly.graph_objects.Figure` without showing it (`fig.show()`, `fig.write_html(...)`):

```python
sel = park.select()
sel.plot.completeness()                              # filled share per field, own / inherited
sel.plot.provenance(["usage", "heated_area"])        # share of each source
sel.plot.aggregate("heated_area")                    # total per site, park total in the subtitle
sel.plot.distribution("construction_year", bins=10)  # histogram, or counts for categories
sel.plot.map(color="usage")                          # points on OpenStreetMap, no API key
sel.plot.map(geometry="footprint", color="heated_area")
park.select(entity="site").plot.consumption(start="2024-01-01", end="2025-01-01")
sel.plot.intensity(start="2024-01-01", end="2025-01-01", freq="MS")
```

Entities without a value are not drawn; the subtitle tells how many are.

## Examples

Each example comes as a script and as an interactive version, to run cell by cell (`# %%`) in
PyCharm or VS Code, or with `python -i`; both are kept in step and tested.

- [`examples/bayonne.py`](examples/bayonne.py) /
  [`bayonne_interactive.py`](examples/bayonne_interactive.py): project classes, provenance,
  inheritance, envelope, shared systems, a meter, save and reload.
- [`examples/connectors.py`](examples/connectors.py) /
  [`connectors_interactive.py`](examples/connectors_interactive.py): RNB, BDNB and DPE on two real
  sites, then exports (they call the real APIs).
- [`examples/plots.py`](examples/plots.py) / [`plots_interactive.py`](examples/plots_interactive.py):
  every figure on a fictitious park (`[plot]` extra).

```bash
python examples/bayonne.py
```

## Connectors and exports

```python
from bstock import BuildingPark
from bstock.connectors import bdnb, dpe, rnb

park = BuildingPark(name="Bayonne")
park.new_site("district", name="Saint-Esprit")
rnb.add_buildings(park, ["5CWS74SZZ7EW", "BWB9SNCJHWVT"], parent="district")  # point, footprint, address
bdnb.enrich(park)        # year, usage, dwellings, height, generators; BDNB groups become sub-sites
report = dpe.enrich(park)  # most recent building DPE: areas, envelope, generators
report.to_dataframe()      # what was created, enriched, not found, and why

rnb.suggest_matches(park, method="address")   # RNB candidates for buildings without rnb_id

park.to_dataframe(provenance=True)        # effective values with their source and provider
park.to_geopackage("bayonne.gpkg")        # "sites" and "buildings" layers
```

Each value keeps its source (`"rnb"`, `"bdnb"`, `"dpe"`), date and reference; running a connector
again updates its values without duplicating anything. The BDNB open offer serves 10 rows per call:
pass `bdnb.BdnbClient(api_key=...)` as `client=` for larger parks.

## Development

```bash
python -m pytest                      # offline tests (recorded API responses)
python -m pytest -m network           # tests calling the real APIs
python -m ruff check --fix .
python -m ruff format .
```

Conventions are in [`CLAUDE.md`](CLAUDE.md), the implementation plan in [`PLAN.md`](PLAN.md).
