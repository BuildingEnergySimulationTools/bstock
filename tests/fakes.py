"""Fake connector clients serving the recorded API responses of ``tests/data``.

Each fake has the methods of the real client used by the connector functions, records its calls
in ``calls`` and takes the ``recorded`` loader fixture.
"""

from bstock.connectors.dpe import DATASET_KEY

# --- RNB --------------------------------------------------------------------------------------

GABARRE = "6X3K8HHY9FFC"  # 4 rue de la Gabarre, Bayonne
NEIGHBOUR = "GTV1GBN4PVR4"
DEMOLISHED = "B1D95Z9H5X53"
MERGED = "2HE8HF5AVVRS"
SPLIT = "3PK8K5FN1BBV"
DEACTIVATED = "5TFX3VFYXTCZ"
INACTIVE_FILES = {
    MERGED: "merged",
    SPLIT: "split",
    DEACTIVATED: "deactivated",
}


class FakeRnbClient:
    """Serve the recorded responses of tests/data/rnb and record the calls."""

    def __init__(self, recorded, closest=None, address=None, aliases=(), addresses=None):
        self.recorded = recorded
        self.aliases = set(aliases)
        self.addresses = addresses or {}  # address -> recorded search response
        self.closest_response = closest
        self.address_response = address
        self.calls = []

    def building(self, rnb_id):
        self.calls.append(("building", rnb_id))
        if rnb_id in self.aliases:  # any other id: the Gabarre building under that id
            return {**self.recorded(f"rnb/building_{GABARRE}.json"), "rnb_id": rnb_id}
        if rnb_id == GABARRE:
            return self.recorded(f"rnb/building_{GABARRE}.json")
        if rnb_id == DEMOLISHED:
            return self.recorded(f"rnb/building_demolished_{DEMOLISHED}.json")
        if rnb_id == NEIGHBOUR:
            return next(
                r
                for r in self.recorded("rnb/closest_bayonne.json")["results"]
                if r["rnb_id"] == rnb_id
            )
        if rnb_id in INACTIVE_FILES:
            return self.recorded(f"rnb/building_inactive_{INACTIVE_FILES[rnb_id]}_{rnb_id}.json")
        try:
            return self.recorded(f"rnb/building_{rnb_id}.json")
        except FileNotFoundError:
            return None

    def history(self, rnb_id):
        self.calls.append(("history", rnb_id))
        return self.recorded(f"rnb/history_{INACTIVE_FILES[rnb_id]}_{rnb_id}.json")

    def closest(self, lat, lon, radius):
        self.calls.append(("closest", lat, lon, radius))
        if self.closest_response is not None:
            return self.closest_response
        return self.recorded("rnb/closest_bayonne.json")["results"]

    def search_address(self, address):
        self.calls.append(("address", address))
        if address in self.addresses:
            return self.recorded(self.addresses[address])
        return self.address_response or self.recorded("rnb/address_search_ok.json")

    def plot_buildings(self, plot_id):
        self.calls.append(("plot", plot_id))
        try:
            return self.recorded(f"rnb/plot_{plot_id}.json")["results"]
        except FileNotFoundError:
            return []


# --- cadastre ---------------------------------------------------------------------------------

GABARRE_POINT = (43.48829, -1.45472)  # lat, lon
GABARRE_PLOT = "64102000AO0095"  # holds Gabarre (cover 0.98) and a sliver of NEIGHBOUR
NEXT_PLOT = "64102000AO0148"  # holds HJ3QCGD13V13 (cover 1.0) and slivers of the two others
NEXT_PLOT_BUILDING = "HJ3QCGD13V13"


class FakeCadastreClient:
    """Serve the recorded plots around the Gabarre building and record the calls."""

    def __init__(self, recorded, plots=None):
        self.recorded = recorded
        self.plots_response = plots
        self.calls = []

    def plots(self, lat, lon, radius=0.0):
        self.calls.append(("plots", lat, lon, radius))
        if self.plots_response is not None:
            return self.plots_response
        name = "point" if radius == 0 else "radius20"
        return self.recorded(f"cadastre/parcelle_{name}_gabarre.json")["features"]


# --- BDNB -------------------------------------------------------------------------------------

SINGLE_GROUP, SINGLE_RNB = "bdnb-bg-6SCZ-8433-AMV8", "5CWS74SZZ7EW"
MULTI_GROUP, MULTI_RNB = "bdnb-bg-F317-Z6BT-TLT8", "BWB9SNCJHWVT"
OTHER_RNB = "A1B2C3D4E5F6"  # given to another construction of the multi group in some tests
OTHER_CONSTRUCTION = "bdnb-bc-2FBM-2CXY-L2SE"


class FakeBdnbClient:
    """Serve the recorded rows of tests/data/bdnb and record the calls."""

    def __init__(self, recorded, rnb_of=None):
        single = recorded(f"bdnb/batiment_construction_single_{SINGLE_GROUP}.json")
        multi = recorded(f"bdnb/batiment_construction_multi_{MULTI_GROUP}.json")
        self.constructions = [
            {**row, "rnb_id": (rnb_of or {}).get(row["batiment_construction_id"], row["rnb_id"])}
            for row in [*single, *multi, *recorded("bdnb/batiment_construction_nobatek.json")]
        ]
        self.group_rows = [
            *recorded(f"bdnb/batiment_groupe_complet_single_{SINGLE_GROUP}.json"),
            *recorded(f"bdnb/batiment_groupe_complet_multi_{MULTI_GROUP}.json"),
            *recorded("bdnb/batiment_groupe_complet_nobatek.json"),
        ]
        self.dpe_rows = [
            *recorded(f"bdnb/batiment_groupe_dpe_representatif_logement_{MULTI_GROUP}.json"),
            *recorded("bdnb/batiment_groupe_dpe_representatif_logement_nobatek.json"),
        ]
        self.calls = []

    def constructions_by_rnb(self, rnb_ids):
        rnb_ids = set(rnb_ids)
        self.calls.append(("constructions", sorted(rnb_ids)))
        return [r for r in self.constructions if r["rnb_id"] in rnb_ids]

    def groups(self, group_ids):
        group_ids = set(group_ids)
        self.calls.append(("groups", sorted(group_ids)))
        return [r for r in self.group_rows if r["batiment_groupe_id"] in group_ids]

    def representative_dpe(self, group_ids):
        group_ids = set(group_ids)
        return [r for r in self.dpe_rows if r["batiment_groupe_id"] in group_ids]

    def count_constructions(self, group_id):
        self.calls.append(("count", group_id))
        return sum(r["batiment_groupe_id"] == group_id for r in self.constructions)


# --- DPE --------------------------------------------------------------------------------------

FILES = {
    "dpe03existant": [
        "lines_dpe03existant_bayonne_immeuble.json",
        "lines_dpe03existant_bayonne_maison.json",
        "lines_dpe03existant_bayonne_appartement.json",
        "lines_dpe03existant_bayonne_appartement_from_immeuble.json",
    ],
    "dpe02neuf": ["lines_dpe02neuf_bayonne_immeuble.json", "lines_dpe02neuf_bayonne_maison.json"],
    "dpe01tertiaire": [
        "lines_dpe01tertiaire_bayonne.json",
        "lines_dpe01tertiaire_bayonne_facture.json",
    ],
}
IMMEUBLE, IMMEUBLE_RNB = "2664E2519948T", "2E1X4DY5AY9T"  # replaces 2664E2519887K
REPLACED = "2664E2519887K"
MAISON_RNB = "1SZPEQZ91AE6"
APARTMENT_RNB = "2PH4SW4PVQQ9"
FROM_BUILDING_RNB, BUILDING_DPE = "PEH1TASYDBYE", "2664E0578892T"
TERTIARY_RNB = "WWZHWSD72AAJ"


def dpe_lines(recorded):
    """Every recorded line, tagged with its dataset, plus two crafted ones."""
    found = [
        {**line, DATASET_KEY: dataset}
        for dataset, files in FILES.items()
        for name in files
        for line in recorded(f"dpe/{name}")["results"]
    ]
    immeuble = next(line for line in found if line["numero_dpe"] == IMMEUBLE)
    # the replaced DPE, more recent on purpose: it must still be set aside
    found.append(
        {
            **immeuble,
            "numero_dpe": REPLACED,
            "numero_dpe_remplace": None,
            "date_etablissement_dpe": "2026-10-10",
        }
    )
    # the building DPE the apartment DPE of FROM_BUILDING_RNB were generated from
    other = next(line for line in found if line["numero_dpe"] == "2664E2513521O")
    found.append(
        {
            **other,
            "numero_dpe": BUILDING_DPE,
            "id_rnb": None,
            "date_etablissement_dpe": "2026-02-27",
        }
    )
    return found


class FakeDpeClient:
    def __init__(self, lines):
        self.all_lines = lines
        self.calls = []

    def dpe_by_rnb(self, rnb_ids):
        rnb_ids = set(rnb_ids)
        self.calls.append(("rnb", sorted(rnb_ids)))
        return [line for line in self.all_lines if line.get("id_rnb") in rnb_ids]

    def dpe_by_numbers(self, numbers):
        numbers = set(numbers)
        self.calls.append(("numbers", sorted(numbers)))
        return [line for line in self.all_lines if line["numero_dpe"] in numbers]
