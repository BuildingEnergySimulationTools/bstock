# ruff: noqa: B018  (a bare expression at the end of a cell is displayed by the console)
"""Interactive version of ``connectors.py``, to run cell by cell (``# %%``) (PyCharm, VS Code).

It calls the real RNB, BDNB and DPE APIs (``[connectors]`` extra; ``[geo]`` for the GeoPackage).
Each cell ends with an expression shown by the console, and every object (``park``, the reports,
the raw API responses...) stays in the namespace for exploration. The files are written to a
persistent temporary directory (``OUTPUT``). Also works with
``python -i examples/connectors_interactive.py``.
"""

# %% imports and output directory
import tempfile
from pathlib import Path

import pandas as pd

from bstock import Building, BuildingPark, MissingDependencyError, Site
from bstock.connectors import bdnb, dpe, rnb

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_colwidth", 60)

OUTPUT = Path(tempfile.gettempdir()) / "bstock_examples" / "connectors"
OUTPUT.mkdir(parents=True, exist_ok=True)
OUTPUT


# %% helpers to look at the park


def candidates(entity: Site | Building, name: str) -> pd.DataFrame:
    """Return every candidate of ``name`` along the inheritance chain, nearest first."""
    rows = [
        {"entity": entity_id, **candidate.model_dump()}
        for entity_id, candidate in entity.inherited_candidates(name)
    ]
    return pd.DataFrame(rows)


# %% 1. the park and its two sites, known by their address only
OWNER_SOURCE = "nobatek"
SITES = {
    "nobatek-anglet": ("Nobatek Anglet", "67 rue de Mirambeau 64600 Anglet"),
    "nobatek-bordeaux": ("Nobatek Bordeaux", "9 rue Jean-Paul Alaux, 33100 Bordeaux"),
}

park = BuildingPark(name="Nobatek")
for site_id, (name, address) in SITES.items():
    park.new_site(site_id, name=name, address=address, source=OWNER_SOURCE)

park.to_dataframe("site", provenance=True)


# %% 2. RNB: search the buildings at each site address (geocoded with the BAN)
rnb_client = rnb.RnbClient()
rnb_responses = {site.id: rnb_client.search_address(site.address) for site in park.iter_sites()}

pd.DataFrame(
    [
        {"site": site_id, **{k: v for k, v in result.items() if k != "shape"}}
        for site_id, response in rnb_responses.items()
        for result in response.get("results") or []
    ]
)


# %% add the RNB buildings to their site (point, footprint, address)
rnb_reports = {}
for site in list(park.iter_sites()):
    rnb_ids = [building["rnb_id"] for building in rnb_responses[site.id].get("results") or []]
    rnb_reports[site.id] = rnb.add_buildings(park, rnb_ids, parent=site, client=rnb_client)

park.tree(fields=["address"])


# %% 3. BDNB: construction values on the buildings, group values on a sub-site
bdnb_report = bdnb.enrich(park)
print(f"group sites created: {bdnb_report.created}")
print(park.tree(systems=True, fields=["construction_year"]))
bdnb_report.to_dataframe()


# %% 4. DPE: the most recent building-scale DPE of each building, if any
dpe_report = dpe.enrich(park)
dpe_report.to_dataframe()


# %% 5. a computed footprint area next to the BDNB one: the source priority picks the effective one
for building in park.iter_buildings():
    if building.footprint is not None:
        building.compute_footprint_area()

columns = ["parent_id", "address", "usage", "construction_year", "construction_year_provider"]
columns += ["height", "footprint_area", "footprint_area_source"]
park.to_dataframe(provenance=True)[columns]


# %% buildings the sources left without construction year, and where they are
undated = park.select(construction_year__isnull=True)
undated.tree()


# %% what the sources filled, and where the values come from
report_fields = ["construction_year", "usage", "height", "footprint_area", "heated_area"]
buildings = park.select()
print(buildings.completeness(fields=report_fields))
buildings.provenance(report_fields)


# %% where the sources disagree (own candidates only; floats within 5 %)
buildings.conflicts()


# %% all candidates of one value, e.g. the footprint area of the first building
first = next(park.iter_buildings())
print(first)
candidates(first, "footprint_area")


# %% every attribute of that building: effective value, unit, source, provider
first.describe()


# %% 6. exports: GeoPackage (``[geo]`` extra) and park directory
try:
    print(park.to_geopackage(OUTPUT / "nobatek.gpkg"))
except MissingDependencyError as error:
    print(f"GeoPackage skipped: {error}")
park.save(OUTPUT / "park")
sorted(p.relative_to(OUTPUT).as_posix() for p in OUTPUT.rglob("*") if p.is_file())
