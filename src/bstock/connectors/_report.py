"""Report returned by the high-level connector functions (SPEC §8.2)."""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

__all__ = ["ImportReport"]

Status = Literal["created", "enriched", "not_found", "ambiguous"]
STATUSES: tuple[Status, ...] = ("created", "enriched", "not_found", "ambiguous")


class ImportReport(BaseModel):
    """What a connector did, per id.

    Expected cases (a building absent from the source, an apartment DPE ignored...) are recorded
    here rather than emitted as warnings.

    Parameters
    ----------
    source
        Source of the connector, e.g. ``"rnb"``.
    created
        Ids of the entities created.
    enriched
        Ids of the entities that received values.
    not_found
        Ids (entity or external) absent from the source.
    ambiguous
        Ids for which the source gave several candidates and nothing was done.
    messages
        Messages by id, in order.
    """

    source: str
    created: list[str] = Field(default_factory=list)
    enriched: list[str] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
    ambiguous: list[str] = Field(default_factory=list)
    messages: dict[str, list[str]] = Field(default_factory=dict)

    def add(self, status: Status, entity_id: str, message: str | None = None) -> None:
        """Record ``entity_id`` under ``status`` (once), with an optional message."""
        ids: list[str] = getattr(self, status)
        if entity_id not in ids:
            ids.append(entity_id)
        if message is not None:
            self.note(entity_id, message)

    def note(self, entity_id: str, message: str) -> None:
        """Add a message about ``entity_id``."""
        self.messages.setdefault(entity_id, []).append(message)

    def to_dataframe(self) -> pd.DataFrame:
        """Return one row per id, in order of first appearance.

        Returns
        -------
        pandas.DataFrame
            Indexed by ``id``, with one boolean column per status (``created``, ``enriched``,
            ``not_found``, ``ambiguous``) and ``message`` (messages joined by ``"; "``, or
            ``None``).
        """
        ids = list(
            dict.fromkeys([*(i for s in STATUSES for i in getattr(self, s)), *self.messages])
        )
        rows = [
            {
                **{status: entity_id in getattr(self, status) for status in STATUSES},
                "message": "; ".join(self.messages[entity_id])
                if entity_id in self.messages
                else None,
            }
            for entity_id in ids
        ]
        return pd.DataFrame(
            rows, columns=[*STATUSES, "message"], index=pd.Index(ids, name="id", dtype=object)
        ).astype(dict.fromkeys(STATUSES, bool))
