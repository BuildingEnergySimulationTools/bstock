"""End-to-end example of the bstock connectors (SPEC §8, §9), on real Bayonne buildings.

It shows the v2 workflow:

1. RNB: create the buildings of a client site from their RNB ids (point, footprint, address);
2. BDNB: enrich them; a BDNB group of several constructions becomes a sub-site;
3. DPE: add the DPE values (description, envelope, generators);
4. derived values and provenance in a DataFrame;
5. exports: GeoPackage (``[geo]`` extra) and a park directory.

It calls the public APIs and needs the ``[connectors]`` and ``[geo]`` extras. Run it with
``python examples/connectors.py [output_directory]``. The tests run it with fake clients serving
recorded responses.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from bstock import BuildingPark, MissingDependencyError
from bstock.connectors import bdnb, dpe, rnb

RNB_IDS = [
    "5CWS74SZZ7EW",  # residential building, alone in its BDNB group
    "BWB9SNCJHWVT",  # one of the 7 constructions of a BDNB group
    "2E1X4DY5AY9T",  # residential building with a building DPE
]


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
    park = BuildingPark(name="Bayonne, connecteurs")
    park.new_site("quartier-saint-esprit", name="Quartier Saint-Esprit")

    # 1. RNB: one request per building
    report = rnb.add_buildings(park, RNB_IDS, parent="quartier-saint-esprit", client=rnb_client)
    print(f"RNB: {len(report.created)} buildings created")

    # 2. BDNB: construction values on the buildings, group values on the building or on a sub-site
    report = bdnb.enrich(park, client=bdnb_client)
    print(f"BDNB: {len(report.enriched)} entities enriched, group sites created: {report.created}")

    # 3. DPE: the most recent building-scale DPE of each building
    report = dpe.enrich(park, client=dpe_client)
    print(report.to_dataframe()[["enriched", "not_found", "message"]].to_string())

    # 4. a computed value next to the BDNB one; the source priority picks the effective value
    for building in park.iter_buildings():
        if building.footprint is not None:
            building.compute_footprint_area()
    columns = ["parent_id", "usage", "construction_year", "construction_year_source"]
    columns += ["construction_year_provider", "footprint_area", "footprint_area_source"]
    print(park.to_dataframe(provenance=True)[columns].to_string())

    # 5. exports
    try:
        park.to_geopackage(output / "bayonne.gpkg")
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
