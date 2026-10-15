"""End-to-end example of the bstock connectors (SPEC §8, §9): the Nobatek park.

Nobatek owns two sites, known by their address only. The example:

1. creates the park and its two sites, with their address;
2. RNB: finds the buildings at each site address and adds them to the site (point, footprint,
   address);
3. BDNB: enriches them; a BDNB group of several constructions becomes a sub-site of the site;
4. DPE: adds the values of the DPE linked to the buildings, if any;
5. computes the footprint areas and shows the effective values with their provenance;
6. exports a GeoPackage (``[geo]`` extra) and saves the park directory.

It calls the public APIs and needs the ``[connectors]`` and ``[geo]`` extras. Run it with
``python examples/connectors.py [output_directory]``. The tests run it with fake clients serving
responses recorded on 2026-10-14.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from bstock import BuildingPark, MissingDependencyError
from bstock.connectors import bdnb, dpe, rnb

OWNER_SOURCE = "nobatek"
SITES = {
    "nobatek-anglet": ("Nobatek Anglet", "67 rue de Mirambeau 64600 Anglet"),
    "nobatek-bordeaux": ("Nobatek Bordeaux", "9 rue Jean-Paul Alaux, 33100 Bordeaux"),
}


def main(
    output: Path,
    *,
    rnb_client: Any | None = None,
    bdnb_client: Any | None = None,
    dpe_client: Any | None = None,
) -> BuildingPark:
    """Run the example in ``output`` and return the park.

    The clients default to the real APIs; tests pass fake ones.
    """
    rnb_client = rnb_client or rnb.RnbClient()

    # 1. the park and its sites, as given by the owner
    park = BuildingPark(name="Nobatek")
    for site_id, (name, address) in SITES.items():
        park.new_site(site_id, name=name, address=address, source=OWNER_SOURCE)

    # 2. RNB: the buildings at each site address (the RNB geocodes the address with the BAN)
    for site in list(park.iter_sites()):
        report = rnb.add_buildings_at_address(park, site.address, parent=site, client=rnb_client)
        print(f"{site.name}: {len(report.created)} RNB building(s) at {site.address!r}")

    # 3. BDNB: construction values on the buildings, group values on the building or a sub-site
    report = bdnb.enrich(park, client=bdnb_client)
    print(f"BDNB: group sites created: {report.created}")
    print(report.to_dataframe()[["enriched", "not_found", "message"]].to_string())
    print(park.tree(systems=True, fields=["construction_year"]))

    # 4. DPE: the most recent building-scale DPE of each building, when the open data has one
    report = dpe.enrich(park, client=dpe_client)
    print(f"DPE: {len(report.enriched)} entities enriched, {len(report.not_found)} without DPE")

    # 5. a computed footprint area next to the BDNB one; the source priority picks the effective one
    for building in park.iter_buildings():
        if building.footprint is not None:
            building.compute_footprint_area()
    columns = ["parent_id", "address", "usage", "construction_year", "construction_year_provider"]
    columns += ["height", "footprint_area", "footprint_area_source"]
    with pd.option_context("display.width", 200):
        print(park.to_dataframe(provenance=True)[columns].to_string())

    # buildings the sources left without construction year, and where they are
    print(park.select(construction_year__isnull=True).tree())

    # what the sources filled, where the values come from, and where the sources disagree
    report_fields = ["construction_year", "usage", "height", "footprint_area", "heated_area"]
    buildings = park.select()
    print(buildings.completeness(fields=report_fields).to_string())
    print(buildings.provenance(report_fields).to_string())
    conflicts = buildings.conflicts()
    print(conflicts.to_string() if len(conflicts) else "No conflict between the sources")

    # 6. exports
    try:
        park.to_geopackage(output / "nobatek.gpkg")
    except MissingDependencyError as error:
        print(f"GeoPackage skipped: {error}")
    park.save(output / "park")
    print(f"Saved {len(list(park.iter_buildings()))} buildings to {output / 'park'}")
    return park


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        target.mkdir(parents=True, exist_ok=True)
        main(target)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp))
