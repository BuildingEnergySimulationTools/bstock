# ruff: noqa: B018  (a bare expression at the end of a cell is displayed by the console)
"""Interactive version of ``bayonne.py``, to run cell by cell (``# %%``) (PyCharm, VS Code).

Each cell ends with an expression shown by the console, and every object (``park``, ``a``,
``frame``...) stays in the namespace for exploration. The files are written to a persistent
temporary directory (``OUTPUT``) so they can be opened after the run. Also works with
``python -i examples/bayonne_interactive.py``.
"""

# %% imports and output directory
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from bstock import (
    Building,
    BuildingPark,
    Channel,
    CsvSource,
    EnvelopeComponent,
    HeatingGenerator,
    Meter,
    Site,
    Window,
    sourced,
)

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)

OUTPUT = Path(tempfile.gettempdir()) / "bstock_examples" / "bayonne"
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


# %% 1. project model (would live in the project package, e.g. projet_bayonne/model.py)


class BayonneSite(Site):
    """Site with the Bayonne management sector."""

    secteur = sourced(str, description="Management sector")


class BayonneBuilding(Building):
    """Building with the Bayonne asset code and usable area."""

    code_patrimoine = sourced(str)
    surface_utile_client = sourced(float, unit="m²", ge=0, extensive=True)


# %% made-up input files: client inventory and electricity load curve
inventory = OUTPUT / "inventory.csv"
inventory.write_text(
    "code_site,nom_site,secteur,rnb,code,surface_utile\n"
    "site-ecole-A,Groupe scolaire A,Nord,A1B2C3D4E5F6,BAT-0042,1180\n"
    "site-ecole-A,Groupe scolaire A,Nord,B1B2C3D4E5F6,BAT-0043,640\n",
    encoding="utf-8",
)
load_curve = OUTPUT / "enedis_PDL_123.csv"
index = pd.date_range("2024-01-15", periods=48, freq="30min")  # local time, no offset
power = [20_000 + 15_000 * (8 <= t.hour < 18) for t in index]
pd.DataFrame({"horodate": index, "valeur": power}).to_csv(load_curve, sep=";", index=False)

pd.read_csv(inventory)


# %% 2. project reader: load the client inventory, one row per building
CLIENT_SOURCE = "bayonne_inventory_2024"

park = BuildingPark(name="Ville de Bayonne", site_cls=BayonneSite, building_cls=BayonneBuilding)
for row in pd.read_csv(inventory).itertuples():
    site = park.get(row.code_site) or park.new_site(
        row.code_site, name=row.nom_site, secteur=row.secteur, source=CLIENT_SOURCE
    )
    park.new_building(
        row.rnb,
        parent=site,
        source=CLIENT_SOURCE,
        code_patrimoine=row.code,
        surface_utile_client=row.surface_utile,
    )

print(park.tree(fields=["secteur", "code_patrimoine"]))
park.to_dataframe(provenance=True)


# %% 3. provenance: the two buildings belong to one BDNB group, its values go on a sub-site
group = park.new_site("bdnb-bg-1111-1HRT-8SVH", parent="site-ecole-A")
for rnb in ("A1B2C3D4E5F6", "B1B2C3D4E5F6"):
    park.move(rnb, group)
group.set("construction_year", 1972, source="bdnb", ref=group.id)
group.set("usage", "tertiary", source="bdnb")
group.envelope.set("u_bat", 1.4, source="dpe", ref="2611E0031228S")

park.tree(fields=["construction_year", "usage"])


# %% an audit gives another construction year for A: which one wins?
a = park["A1B2C3D4E5F6"]
a.set("construction_year", 1974, source="audit", date=date(2025, 3, 12), comment="plans")
a.set("heated_area", 1250.0, source="audit")

print(f"A built in {a.construction_year} (source: {a.effective('construction_year').source})")
print(f"A usage {a.usage} inherited from {a.provider_id('usage')}")
candidates(a, "construction_year")


# %% every attribute of A: effective value, unit, source, provider (inherited or not)
a.describe()


# %% effective values of every building, with source and provider
columns = ["parent_id", "usage", "construction_year", "construction_year_source"]
columns += ["construction_year_provider", "heated_area", "code_patrimoine"]
park.to_dataframe(provenance=True)[columns]


# %% select on effective values, inheritance included: tertiary buildings without heated area
missing = park.select(usage="tertiary", heated_area__isnull=True)
print(missing.ids)
missing.tree(fields=["usage", "heated_area"])


# %% 4. detailed envelope from an audit: the computed U_bat beats the DPE value
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
candidates(a.envelope, "u_bat")


# %% a boiler room shared by the whole school group
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
print(a.serving_systems("heating"))
park.tree(systems=True)


# %% areas per site (client usable area, heated area) and boiler power, without double count
totals = park.select().aggregate(["surface_utile_client", "heated_area"])
columns = [("surface_utile_client", "total"), ("heated_area", "total")]
columns += [("heated_area", "coverage"), ("heating_power", "value")]
totals[columns]


# %% 5. the site electricity meter, read from the CSV load curve
park.add_meter(
    Meter(
        id="PDL_123",
        scope=["site-ecole-A"],
        end_use="total",
        energy_type="electricity",
        timestep="30min",
        channels=[Channel(name="power", unit="W", column="valeur")],
        data_source=CsvSource(
            path=str(load_curve), index_col="horodate", sep=";", tz="Europe/Paris"
        ),
    )
)

print(park.tree(systems=True, meters=True))
frame = park.timeseries(load_data=True)  # columns follow the tide convention
frame.head(24)


# %% save a self-contained park directory, then reload it with the project classes
park.save(OUTPUT / "park", include_data=True)
reloaded = BuildingPark.load(OUTPUT / "park", site_cls=BayonneSite, building_cls=BayonneBuilding)

print(f"Saved to {OUTPUT / 'park'}")
print(f"reloaded == park: {reloaded == park}")
print(f"same time series: {reloaded.timeseries(load_data=True).equals(frame)}")
sorted(p.relative_to(OUTPUT).as_posix() for p in (OUTPUT / "park").rglob("*") if p.is_file())
