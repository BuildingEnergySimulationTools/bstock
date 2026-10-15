"""Figures of a park with ``sel.plot`` (SPEC §14.5), on a fictitious park with made-up data.

The example builds a small park (three school groups, six buildings with values from several
sources, gas and electricity meters), then writes one HTML file per figure:

1. reports: completeness and sources of the values;
2. values: heated area per site and distribution of the construction years;
3. maps: buildings coloured by usage, footprints coloured by heated area;
4. consumption per energy and per month, and kWh/m² per site.

It needs the ``[plot]`` extra. Run it with ``python examples/plots.py [output_directory]`` and open
the HTML files in a browser (they load plotly.js from a CDN).
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import pandas as pd

from bstock import BuildingPark, Channel, HeatingGenerator, Meter

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


def build_park() -> BuildingPark:
    """Return the fictitious park, with a year of daily gas and electricity readings."""
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
    return park


def main(output: Path) -> BuildingPark:
    """Write the figures of the example in ``output`` and return the park."""
    park = build_park()
    buildings, sites = park.select(), park.select(kind="site")
    year = {"start": "2024-01-01", "end": "2025-01-01"}
    figures = {
        # 1. reports
        "completeness": buildings.plot.completeness(
            fields=["usage", "construction_year", "heated_area", "height", "lat"]
        ),
        "sources": buildings.plot.provenance(["usage", "construction_year", "heated_area"]),
        # 2. values
        "heated_area": buildings.plot.aggregate("heated_area"),
        "years": buildings.plot.distribution("construction_year", bins=8),
        # 3. maps
        "map": buildings.plot.map(color="usage"),
        "footprints": buildings.plot.map(geometry="footprint", color="heated_area"),
        # 4. consumption
        "consumption": sites.plot.consumption(**year),
        "monthly": buildings.plot.consumption(**year, freq="MS"),
        "intensity": sites.plot.intensity(**year),
    }
    for name, figure in figures.items():
        figure.write_html(output / f"{name}.html", include_plotlyjs="cdn")
    print(f"Wrote {len(figures)} figures to {output}")
    return park


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        target.mkdir(parents=True, exist_ok=True)
        main(target)
    else:
        main(Path(tempfile.mkdtemp(prefix="bstock_plots_")))
