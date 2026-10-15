"""End-to-end example of bstock (SPEC §10), with made-up data for a fictitious Bayonne park.

It shows the typical project workflow:

1. project subclasses adding client attributes;
2. a project reader loading the client inventory;
3. provenance: several sources for one value, inheritance from a BDNB group site;
4. shared systems, envelope computations and a meter read from a CSV file;
5. a tide-ready time series, then a self-contained park directory reloaded with the project classes.

Run it with ``python examples/bayonne.py [output_directory]``.
"""

from __future__ import annotations

import math
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from bstock import (
    Building,
    BuildingPark,
    Channel,
    CsvReader,
    EnvelopeComponent,
    HeatingGenerator,
    Meter,
    Site,
    Window,
    sourced,
)

# --- 1. project model (would live in the project package, e.g. projet_bayonne/model.py) ------


class BayonneSite(Site):
    """Site with the Bayonne management sector."""

    secteur = sourced(str, description="Management sector")


class BayonneBuilding(Building):
    """Building with the Bayonne asset code and usable area."""

    code_patrimoine = sourced(str)
    surface_utile_client = sourced(float, unit="m²", ge=0, extensive=True)


def new_park() -> BuildingPark:
    """Return an empty Bayonne park."""
    return BuildingPark(name="Ville de Bayonne", site_cls=BayonneSite, building_cls=BayonneBuilding)


# --- 2. project reader (would live in projet_bayonne/readers.py) ------------------------------

CLIENT_SOURCE = "bayonne_inventory_2024"


def read_inventory(path: Path, park: BuildingPark) -> None:
    """Load the client inventory: one row per building, grouped by client site."""
    for row in pd.read_csv(path).itertuples():
        site = park.get(row.code_site) or park.new_site(
            row.code_site, name=row.nom_site, secteur=row.secteur, source=CLIENT_SOURCE
        )
        park.new_building(
            rnb_id=row.rnb,
            parent=site,
            source=CLIENT_SOURCE,
            code_patrimoine=row.code,
            surface_utile_client=row.surface_utile,
        )


def write_input_files(directory: Path) -> tuple[Path, Path]:
    """Write the made-up client inventory and electricity load curve."""
    inventory = directory / "inventory.csv"
    inventory.write_text(
        "code_site,nom_site,secteur,rnb,code,surface_utile\n"
        "site-ecole-A,Groupe scolaire A,Nord,A1B2C3D4E5F6,BAT-0042,1180\n"
        "site-ecole-A,Groupe scolaire A,Nord,B1B2C3D4E5F6,BAT-0043,640\n",
        encoding="utf-8",
    )
    load_curve = directory / "enedis_PDL_123.csv"
    index = pd.date_range("2024-01-15", periods=48, freq="30min")  # local time, no offset
    power = [20_000 + 15_000 * (8 <= t.hour < 18) for t in index]
    pd.DataFrame({"horodate": index, "valeur": power}).to_csv(load_curve, sep=";", index=False)
    return inventory, load_curve


# --- 3 to 5. workflow -------------------------------------------------------------------------


def main(output: Path) -> BuildingPark:
    """Run the example in ``output`` and return the reloaded park."""
    inventory, load_curve = write_input_files(output)

    park = new_park()
    read_inventory(inventory, park)

    # The two RNB buildings belong to one BDNB group: its values go on a sub-site.
    group = park.new_site("bdnb-bg-1111-1HRT-8SVH", parent="site-ecole-A")
    for rnb in ("A1B2C3D4E5F6", "B1B2C3D4E5F6"):
        park.move(rnb, group)
    group.set("construction_year", 1972, source="bdnb", ref=group.id)
    group.set("usage", "tertiary", source="bdnb")
    group.envelope.set("u_bat", 1.4, source="dpe", ref="2611E0031228S")
    print(park.tree(fields=["construction_year", "usage"]))

    a = park["A1B2C3D4E5F6"]
    a.set("construction_year", 1974, source="audit", date=date(2025, 3, 12), comment="plans")
    a.set("heated_area", 1250.0, source="audit")
    print(f"A built in {a.construction_year} ({a.effective('construction_year').source}),")
    for entity_id, candidate in a.inherited_candidates("construction_year"):
        print(f"  {entity_id}: {candidate.value} from {candidate.source}")
    print(f"A usage {a.usage} inherited from {a.provider_id('usage')}")
    print(a.describe().loc[["construction_year", "usage", "heated_area"]].to_string())

    # Select on effective values, inheritance included: tertiary buildings without heated area.
    missing = park.select(usage="tertiary", heated_area__isnull=True)
    print(missing.tree(fields=["usage", "heated_area"]))

    # Detailed envelope from an audit, then the computed U_bat beats the DPE value.
    for component in (
        EnvelopeComponent(id="walls", kind="wall", area=900.0, u_value=1.1),
        EnvelopeComponent(id="roof", kind="roof", area=420.0, u_value=0.5),
        Window(id="windows", area=180.0, u_value=2.8, shgc=0.55, glazing="double"),
    ):
        a.envelope.add_component(component)
    a.envelope.compute_heat_loss_coefficient()
    a.envelope.compute_loss_area()
    a.envelope.compute_u_bat()
    print(f"A U_bat = {a.envelope.u_bat:.2f} W/(m2.K) ({a.envelope.effective('u_bat').source})")

    # A boiler room shared by the whole school group, and the site electricity meter.
    park.add_system(
        HeatingGenerator(
            id="heating-chaufferie-A",
            technology="boiler_condensing",
            energy_type="natural_gas",
            nominal_power=450.0,
            seasonal_performance_ratio=0.95,
            source="audit",
        ),
        owner="site-ecole-A",
    )
    print(f"A heating systems: {[s.id for s in a.find_serving_systems('heating')]}")

    # Areas per site: client usable area and heated area, with the boiler power, no double count.
    totals = park.select().aggregate(["surface_utile_client", "heated_area"])
    columns = [("surface_utile_client", "total"), ("heated_area", "total")]
    columns += [("heated_area", "coverage"), ("heating_power", "value")]
    print(totals[columns].to_string())
    park.add_meter(
        Meter(
            id="PDL_123",
            scope=["site-ecole-A"],
            end_use="total",
            energy_type="electricity",
            timestep="30min",
            channels=[Channel(name="power", unit="W", column="valeur")],
            reader=CsvReader(
                path=str(load_curve), index_col="horodate", sep=";", tz="Europe/Paris"
            ),
        )
    )

    # Gas: a site meter and a sub-meter on the boiler room for hot water, daily readings in kWh.
    days = pd.date_range("2024-01-01", "2024-12-31", freq="D", tz="Europe/Paris")
    winter = [max(0.0, math.cos(2 * math.pi * (day.dayofyear - 15) / 366)) for day in days]
    for meter_id, scope, end_use, parent, daily in (
        ("GAZ_A", "site-ecole-A", "total", None, [40 + 260 * w for w in winter]),
        ("GAZ_A_ECS", "heating-chaufferie-A", "dhw", "GAZ_A", [40.0] * len(days)),
    ):
        meter = park.add_meter(
            Meter(
                id=meter_id,
                scope=[scope],
                end_use=end_use,
                parent_meter=parent,
                energy_type="natural_gas",
                timestep="1D",
                channels=[Channel(name="energy", unit="kWh")],
            )
        )
        meter.data = pd.DataFrame({"energy": daily}, index=days)

    print(park.tree(systems=True, meters=True))
    frame = park.timeseries(load_data=True)
    print(f"Time series ready for tide: {list(frame.columns)}, {len(frame)} rows")

    # 2024 consumption, the hot water sub-meter splitting the gas; the electricity meter measures
    # power, which bstock does not integrate (tide's job): it is ignored with a NoDataWarning.
    year = {"start": "2024-01-01", "end": "2025-01-01"}
    print(park.select().consumption(**year, breakdown=True).to_string())
    columns = ["entity", "energy_type", "energy", "coverage", "area", "intensity"]
    print(park.select().intensity(**year)[columns].to_string())

    park.save(output / "park", include_data=True)
    load_curve.unlink()  # the saved park no longer needs the original file
    reloaded = BuildingPark.load(
        output / "park", site_cls=BayonneSite, building_cls=BayonneBuilding
    )
    assert reloaded == park
    assert reloaded.timeseries(load_data=True).equals(frame)
    print(f"Reloaded {sum(1 for _ in reloaded.iter_buildings())} buildings from {output / 'park'}")
    return reloaded


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        target.mkdir(parents=True, exist_ok=True)
        main(target)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp))
