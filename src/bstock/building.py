"""Building: one building in the RNB sense (SPEC §3.6)."""

from __future__ import annotations

from typing import Any

from pydantic import field_validator, model_validator

from bstock._ids import check_external_id, new_id
from bstock.description import DescribedEntity
from bstock.enums import SystemKind
from bstock.systems import System

__all__ = ["Building"]


class Building(DescribedEntity):
    """A building, linked to at most one RNB id.

    Parameters
    ----------
    id
        Stable internal id. Defaults to ``rnb_id`` when known at creation, else a generated id. It
        never changes, even if ``rnb_id`` is corrected later.
    rnb_id
        RNB identifier (12 characters), unique in the park.
    bdnb_batiment_groupe_id
        BDNB group id, when the building is alone in its group (otherwise the group is a site).
    bdnb_batiment_construction_id
        BDNB construction id, the one the RNB refers to.
    """

    rnb_id: str | None = None
    bdnb_batiment_groupe_id: str | None = None
    bdnb_batiment_construction_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("id"):
            data = {**data, "id": data.get("rnb_id") or new_id("bldg")}
        return data

    @field_validator("rnb_id")
    @classmethod
    def _check_rnb_id(cls, value: str | None) -> str | None:
        return check_external_id("rnb", value)

    @field_validator("bdnb_batiment_groupe_id")
    @classmethod
    def _check_bdnb_group_id(cls, value: str | None) -> str | None:
        return check_external_id("bdnb_batiment_groupe", value)

    @field_validator("bdnb_batiment_construction_id")
    @classmethod
    def _check_bdnb_construction_id(cls, value: str | None) -> str | None:
        return check_external_id("bdnb_batiment_construction", value)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "rnb_id" and value != self.rnb_id:
            park = self.park
            if park is not None:
                park._change_rnb(self, value)
        super().__setattr__(name, value)

    def systems(
        self, kind: SystemKind | str | None = None, *, include_shared: bool = True
    ) -> list[System]:
        """Return the systems serving this building.

        Parameters
        ----------
        kind
            Restrict to one kind (``"heating"``, ``"cooling"``, ``"dhw"``, ``"ventilation"``,
            ``"pv"``).
        include_shared
            Also return the systems of the ancestor sites that serve this building: their
            ``serves`` is ``None`` or contains the building id.
        """
        found = list(self.iter_systems(kind))
        if include_shared:
            for site in self._ancestor_sites():
                found.extend(
                    s for s in site.iter_systems(kind) if s.serves is None or self.id in s.serves
                )
        return found
