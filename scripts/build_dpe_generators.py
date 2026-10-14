"""Build ``src/bstock/connectors/data/dpe_generators.csv`` (SPEC annex A.5).

The DPE open data gives generators as labels ("Chaudière gaz à condensation après 2015"). This
script reads the enum tables of the Observatoire DPE (Licence Ouverte 2.0), sheets
``type_generateur_ch``, ``type_generateur_ecs`` and ``type_generateur_fr``, and translates each
label into a bstock ``GeneratorTechnology`` and, when the generator implies it, an ``EnergyType``.

Run once, then review the output line by line::

    python scripts/build_dpe_generators.py                # downloads enum_tables.xlsx
    python scripts/build_dpe_generators.py --xlsx enum_tables.xlsx

Only the standard library and bstock are needed: the .xlsx file is read as a zip of XML files.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import re
import tempfile
import urllib.request
import zipfile
from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree

from bstock.connectors._mappings import DPE_ENERGIES
from bstock.enums import EnergyType, GeneratorTechnology

SOURCE_URL = "https://gitlab.com/observatoire-dpe/observatoire-dpe/-/raw/master/modele_donnee/enum_tables.xlsx"
OUTPUT = Path(__file__).parents[1] / "src" / "bstock" / "connectors" / "data" / "dpe_generators.csv"
SHEETS = {"type_generateur_ch": "ch", "type_generateur_ecs": "ecs", "type_generateur_fr": "fr"}

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_T = GeneratorTechnology
_E = EnergyType


# --- xlsx reading -----------------------------------------------------------------------------


def read_xlsx(path: Path, sheets: Iterable[str]) -> dict[str, list[dict[str, str]]]:
    """Return the rows of ``sheets`` as dicts keyed by the header row; empty rows are skipped."""
    with zipfile.ZipFile(path) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relations = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target", "") for r in relations}
        sheet_paths = {
            s.get("name"): "xl/" + targets[s.get(f"{{{_NS['r']}}}id")].removeprefix("/xl/")
            for s in workbook.iterfind("m:sheets/m:sheet", _NS)
        }
        shared = [
            "".join(t.text or "" for t in item.iter(f"{{{_NS['m']}}}t"))
            for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        ]
        return {name: _sheet_rows(archive.read(sheet_paths[name]), shared) for name in sheets}


def _sheet_rows(xml: bytes, shared: list[str]) -> list[dict[str, str]]:
    grid: list[dict[str, str]] = []
    for row in ElementTree.fromstring(xml).iter(f"{{{_NS['m']}}}row"):
        cells: dict[str, str] = {}
        for cell in row.iterfind("m:c", _NS):
            column = re.match(r"[A-Z]+", cell.get("r", "")).group()  # type: ignore[union-attr]
            value = cell.find("m:v", _NS)
            if cell.get("t") == "s" and value is not None:
                cells[column] = shared[int(value.text or 0)]
            elif cell.get("t") == "inlineStr":
                cells[column] = "".join(t.text or "" for t in cell.iter(f"{{{_NS['m']}}}t"))
            elif value is not None and value.text is not None:
                cells[column] = value.text
        grid.append(cells)
    header, *body = grid
    return [{header[c]: v for c, v in r.items() if c in header} for r in body if r.get("B")]


# --- translation ------------------------------------------------------------------------------

_HYBRID_HEAT_PUMP = "pompe à chaleur hybride : partie pompe à chaleur"
_HYBRID_BOILER = "pompe à chaleur hybride : partie chaudière"

# (substring of the lower-cased label, technology), first match wins
_TECHNOLOGY_RULES: list[tuple[str, GeneratorTechnology]] = [
    ("pac air/air", _T.HEAT_PUMP_AIR_AIR),
    ("pac air/eau", _T.HEAT_PUMP_AIR_WATER),
    ("pac eau glycolée/eau", _T.HEAT_PUMP_GROUND),
    ("pac eau/eau", _T.HEAT_PUMP_WATER_WATER),
    ("pac géothermique", _T.HEAT_PUMP_GROUND),
    ("pac double service", _T.HEAT_PUMP),
    ("cet sur air", _T.THERMODYNAMIC_WATER_HEATER),
    ("autre système thermodynamique électrique", _T.HEAT_PUMP),
    ("autre système thermodynamique gaz", _T.HEAT_PUMP_GAS),
    ("autre système à combustion", _T.OTHER),
    ("autre système de refroidissement", _T.OTHER),
    ("système collectif par défaut", _T.BOILER),
    ("chaudière électrique", _T.ELECTRIC_BOILER),
    ("air chaud", _T.WARM_AIR_GENERATOR),  # some are "à condensation"
    ("accumulateur", _T.WATER_HEATER),  # gas storage water heaters, some "à condensation"
    ("à condensation", _T.BOILER_CONDENSING),
    ("basse température", _T.BOILER_LOW_TEMPERATURE),
    ("chaudière gaz classique", _T.BOILER_STANDARD),
    ("chaudière gaz standard", _T.BOILER_STANDARD),
    ("chaudière fioul classique", _T.BOILER_STANDARD),
    ("chaudière fioul standard", _T.BOILER_STANDARD),
    ("chaudière gpl/propane/butane classique", _T.BOILER_STANDARD),
    ("chaudière gpl/propane/butane standard", _T.BOILER_STANDARD),
    ("chaudière", _T.BOILER),  # wood and coal boilers: type not given
    ("production instantanée", _T.INSTANT_WATER_HEATER),
    ("chauffe-eau électrique instantané", _T.INSTANT_WATER_HEATER),
    ("ballon électrique", _T.ELECTRIC_WATER_TANK),
    ("radiateur électrique à accumulation", _T.ELECTRIC_STORAGE_HEATER),
    ("effet joule", _T.ELECTRIC_RESISTANCE),
    ("électrique", _T.ELECTRIC_RESISTANCE),  # convectors, panels, radiant floors
    ("bi-jonction", _T.ELECTRIC_RESISTANCE),
    ("poêle", _T.STOVE_INSERT),
    ("insert", _T.STOVE_INSERT),
    ("foyer fermé", _T.STOVE_INSERT),
    ("cuisinière", _T.STOVE_INSERT),
    ("radiateur à gaz", _T.GAS_RADIATOR),
    ("réseau de chaleur", _T.DISTRICT_SUBSTATION),
    ("réseau de froid", _T.DISTRICT_SUBSTATION),
]


def technology(label: str) -> GeneratorTechnology:
    """Return the technology of a DPE generator label.

    Raises
    ------
    ValueError
        If no rule matches: the rules must be completed.
    """
    text = label.lower()
    if text.startswith(_HYBRID_HEAT_PUMP):
        return _T.HEAT_PUMP_HYBRID
    if text.startswith(_HYBRID_BOILER):
        rest = label[len(_HYBRID_BOILER) :].strip()
        return _T.BOILER if "(SUPPRIME)" in rest else technology(rest)
    if "multi bâtiment modélisée comme un réseau de chaleur" in text:
        return _T.HEAT_PUMP if text.startswith("pompe") else _T.BOILER
    for fragment, result in _TECHNOLOGY_RULES:
        if fragment in text:
            return result
    raise ValueError(f"no technology rule for {label!r}")


_WOODS = {_E.WOOD_LOGS, _E.WOOD_PELLETS, _E.WOOD_CHIPS}


def energy_type(
    label: str, energy_ids: set[str], energies: dict[str, EnergyType]
) -> EnergyType | None:
    """Return the energy implied by a generator, from its allowed DPE energies, or ``None``.

    ``autre combustible fossile`` (a tolerance of the DPE method) and the district heating of the
    multi-building generators are ignored next to other energies; several wood forms give
    ``WOOD``. Cooling generators have no energy list: heat pumps use electricity.
    """
    if not energy_ids:
        text = label.lower()
        if text.startswith("pac") or "thermodynamique électrique" in text:
            return _E.ELECTRICITY
        return _E.DISTRICT_COOLING if "réseau de froid" in text else None
    found = {energies[i] for i in energy_ids}
    for ignored in (_E.OTHER_FOSSIL, _E.DISTRICT_HEATING):
        if len(found) > 1:
            found.discard(ignored)
    if len(found) > 1 and found <= _WOODS:
        found = {_E.WOOD}
    return found.pop() if len(found) == 1 else None


def build_rows(tables: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    """Return the CSV rows, one per distinct label, in sheet order.

    A label present in several sheets (a heat pump for heating and cooling...) gets one row; its
    allowed energies are the union of those of each sheet.
    """
    energies = {r["id"]: DPE_ENERGIES[r["lib"]] for r in tables["type_energie"]}
    labels: dict[str, dict[str, list[str]]] = {}
    for sheet, short in SHEETS.items():
        for record in tables[sheet]:
            entry = labels.setdefault(
                record["lib"], {"sheets": [], "energy_ids": [], "categories": []}
            )
            entry["sheets"].append(short)
            entry["energy_ids"] += [
                i for i in record.get("enum_type_energie_id", "").split("|") if i
            ]
            category = record.get("categorie_open_data", "").strip()
            if category and category not in entry["categories"]:
                entry["categories"].append(category)
    rows = []
    for label, entry in labels.items():
        energy = energy_type(label, set(entry["energy_ids"]), energies)
        rows.append(
            {
                "label": label,
                "technology": str(technology(label)),
                "energy_type": "" if energy is None else str(energy),
                "sheets": "|".join(entry["sheets"]),
                "categorie_open_data": "|".join(entry["categories"]),
            }
        )
    return rows


def write_csv(rows: list[dict[str, str]], path: Path, version: str) -> None:
    """Write the table with a header citing its source."""
    today = datetime.date.today().isoformat()
    header = [
        "# DPE generator labels -> bstock GeneratorTechnology and EnergyType (SPEC annex A.5).",
        "# Generated by scripts/build_dpe_generators.py, then reviewed line by line.",
        f"# Source: Observatoire DPE, modele_donnee/enum_tables.xlsx (version {version}),",
        f"# {SOURCE_URL}",
        f"# downloaded on {today}. Licence Ouverte 2.0 (Etalab).",
        "# energy_type is empty when the generator does not imply one.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        file.write("\n".join(header) + "\n")
        writer = csv.DictWriter(file, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    """Parse the command line, build the table and write it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--xlsx", type=Path, help="local enum_tables.xlsx (downloaded otherwise)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        xlsx = args.xlsx
        if xlsx is None:
            xlsx = Path(directory) / "enum_tables.xlsx"
            urllib.request.urlretrieve(SOURCE_URL, xlsx)
        tables = read_xlsx(xlsx, [*SHEETS, "type_energie", "version"])
    version = tables["version"][-1]["id"]
    rows = build_rows(tables)
    write_csv(rows, args.output, version)
    print(f"{len(rows)} labels written to {args.output}")


if __name__ == "__main__":
    main()
