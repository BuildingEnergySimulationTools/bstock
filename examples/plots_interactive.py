# ruff: noqa: B018  (a bare expression at the end of a cell is displayed by the console)
"""Interactive version of ``plots.py``, to run cell by cell (``# %%``) (PyCharm, VS Code).

Each cell builds one figure and shows it (``fig.show()`` opens it in the browser, or in the
plot pane of the IDE); every object stays in the namespace for exploration. Needs the ``[plot]``
extra. Also works with ``python -i examples/plots_interactive.py``.
"""

# %% imports
import math

import pandas as pd

from bstock import BuildingPark, Channel, HeatingGenerator, Meter

pd.set_option("display.width", 200)


# %% a fictitious park: three school groups, six buildings, values from several sources
SITES = {
    "gs-nord": ("Groupe scolaire Nord", 43.4945, -1.4740),
    "gs-centre": ("Groupe scolaire Centre", 43.4890, -1.4770),
    "gs-sud": ("Groupe scolaire Sud", 43.4830, -1.4820),
}
BUILDINGS = [  # id, site, usage, year, heated area (m²), source of the values
    ("nord-ecole", "gs-nord", "tertiary", 1962, 1450.0, "dpe"),
    ("nord-logement", "gs-nord", "residential_collective", 1962, 180.0, "bdnb"),
    ("centre-ecole", "gs-centre", "tertiary", 1931, 2100.0, "audit"),
    ("centre-gymnase", "gs-centre", "tertiary", 1978, None, "bdnb"),
    ("sud-ecole", "gs-sud", "tertiary", 2004, 1650.0, "dpe"),
    ("sud-cantine", "gs-sud", "tertiary", 2004, 420.0, "dpe"),
]

park = BuildingPark(name="Ville fictive")
for site_id, (name, lat, lon) in SITES.items():
    park.new_site(site_id, name=name, lat=lat, lon=lon, source="client")
for i, (building_id, site_id, usage, year, area, source) in enumerate(BUILDINGS):
    _, lat, lon = SITES[site_id]
    lat, lon = lat + 0.0006 * (i % 2), lon + 0.0008 * (i % 2)
    size = 0.0003 + 0.0001 * (i % 3)
    ring = [[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size]]
    park.new_building(
        id=building_id,
        parent=site_id,
        usage=usage,
        construction_year=year,
        heated_area=area,
        footprint={"type": "Polygon", "coordinates": [[*ring, ring[0]]]},
        source=source,
    )

park.tree(fields=["usage", "heated_area"])


# %% a year of daily gas and electricity readings per school group
days = pd.date_range("2024-01-01", "2024-12-31", freq="D", tz="Europe/Paris")
winter = [max(0.0, math.cos(2 * math.pi * (day.dayofyear - 15) / 366)) for day in days]
for n, site_id in enumerate(SITES, start=1):
    park.add_system(HeatingGenerator(id=f"boiler-{site_id}", nominal_power=150.0 * n), site_id)
    for energy, daily in (
        ("natural_gas", [n * (30 + 400 * w) for w in winter]),
        ("electricity", [n * (60 + 20 * w) for w in winter]),
    ):
        meter = park.add_meter(
            Meter(
                id=f"{energy}-{site_id}",
                scope=[site_id],
                energy_type=energy,
                end_use="total",
                timestep="1D",
                channels=[Channel(name="energy", unit="kWh")],
            )
        )
        meter.data = pd.DataFrame({"energy": daily}, index=days)

buildings, sites = park.select(), park.select(entity="site")
year = {"start": "2024-01-01", "end": "2025-01-01"}
park.tree(systems=True, meters=True)


# %% 1. reports: how complete the values are, and where they come from
completeness = buildings.plot.completeness(
    fields=["usage", "construction_year", "heated_area", "height", "lat"]
)
completeness.show()


# %%
sources = buildings.plot.provenance(["usage", "construction_year", "heated_area"])
sources.show()


# %% 2. values: heated area per site (no double count), distribution of the construction years
heated_area = buildings.plot.aggregate("heated_area")
heated_area.show()


# %%
years = buildings.plot.distribution("construction_year", bins=8)
years.show()


# %% 3. maps: buildings coloured by usage (location inherited from the sites), footprints by area
building_map = buildings.plot.map(color="usage")
building_map.show()


# %%
footprints = buildings.plot.map(geometry="footprint", color="heated_area")
footprints.show()


# %% 4. consumption per energy, per month, and kWh/m² per site
consumption = sites.plot.consumption(**year)
consumption.show()


# %%
monthly = buildings.plot.consumption(**year, freq="MS")
monthly.show()


# %%
intensity = sites.plot.intensity(**year)
intensity.show()


# %% figures are plain plotly figures: customise them before showing or saving
intensity.update_layout(width=700, height=400)
intensity.layout.title.text
