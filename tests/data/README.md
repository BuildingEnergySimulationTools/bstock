# Recorded API responses

Real responses collected on 2026-10-13 (SPEC annex A.6), used by the offline connector tests. Examples
are buildings in Bayonne (INSEE 64102). Do not edit the responses by hand: re-record them and update the
date above when an API contract changes.

| Directory | Source | Licence |
|---|---|---|
| `rnb/` | RNB API, `https://rnb-api.beta.gouv.fr/api/alpha/` | Licence Ouverte 2.0 |
| `bdnb/` | BDNB open API, `https://api.bdnb.io/v1/bdnb/donnees/` (vintage 2026-02.a) | Licence Ouverte 2.0 |
| `dpe/` | ADEME data-fair, datasets `dpe03existant`, `dpe02neuf`, `dpe01tertiaire` | Licence Ouverte 2.0 |

Files that are not raw responses:
- `bdnb/enum_values.json`: distinct values of the coded columns (`modalite_enum` of
  `metadonnees/colonne`), per table;
- `dpe/schema_*.json`: dataset schemas (`GET /datasets/<id>`);
- `dpe/values_agg_generators_and_scales.json`: national distinct values with counts (`values_agg`) of
  the generator, energy, scale and period columns, merged from one call per field.

## How tests use them

- Unit tests never reach the network. The `recorded` fixture (`tests/conftest.py`) loads a file of this
  directory, e.g. `recorded("rnb/building_6X3K8HHY9FFC.json")`.
- HTTP behaviour (retries, pagination, errors) is tested with the `fake_session` fixture, a fake
  `requests` session serving queued responses. High-level connector functions are tested with a fake
  client passed as `client=`.
- Tests calling the real APIs carry `@pytest.mark.network`; skip them with `pytest -m "not network"`.
