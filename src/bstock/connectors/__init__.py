"""Connectors to the RNB, BDNB and DPE public APIs (SPEC §8).

The connector modules are imported explicitly (``from bstock.connectors import rnb``); they need
the ``[connectors]`` extra, and the BDNB one also the ``[geo]`` extra.
"""

from bstock.connectors._report import ImportReport

__all__ = ["ImportReport"]
