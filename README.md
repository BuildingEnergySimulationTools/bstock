# bstock

Common data structure for building stocks (sites, buildings, systems, meters), with multi-source
provenance of every descriptive value and metered time series in the
[tide](https://github.com/BuildingEnergySimulationTools/tide) column format.

> Status: under development (v0.1). The design reference is [`SPEC.md`](SPEC.md) and the
> implementation plan is [`PLAN.md`](PLAN.md).

## Installation

```bash
python -m pip install -e ".[dev]"     # development
python -m pip install -e ".[plot]"    # with plotly for graphs
```

Requires Python ≥ 3.11.

## Development

```bash
python -m pytest
python -m ruff check --fix .
python -m ruff format .
```
