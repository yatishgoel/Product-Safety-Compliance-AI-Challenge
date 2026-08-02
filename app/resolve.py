"""Request-time PubChem resolution for ingredients the index could not match.

Also provides the veto used to overrule the LLM judge: if both sides resolve to different
structures, the claimed match is rejected.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.index import Index
from app.models import Evidence
from app.pubchem import flush, resolve

ID_DRAWERS = ("inchikey", "inchikey_parent", "pubchem_cid")


@dataclass(frozen=True)
class Resolution:
    evidence: tuple[Evidence, ...] = ()
    unresolvable: tuple[str, ...] = ()
    clean: tuple[str, ...] = ()


def _keys(record: dict) -> list[tuple[str, str]]:
    pairs = [
        ("inchikey", record.get("inchikey")),
        ("inchikey_parent", record.get("parent_inchikey")),
        ("pubchem_cid", str(record["cid"]) if record.get("cid") else None),
    ]
    return [(drawer, value) for drawer, value in pairs if value]


def resolve_one(ingredient: str, index: Index) -> Evidence | None | bool:
    record = resolve(ingredient)
    if record is None:
        return False
    for drawer, key in _keys(record):
        if drawer not in ID_DRAWERS:
            continue
        found = index.lookup(drawer, key)
        if found:
            entry, tier = found
            return Evidence(ingredient=ingredient, entry=entry, drawer=drawer, tier=tier)
    return None


def contradicts(ingredient: str, entry: str) -> bool:
    a, b = resolve(ingredient), resolve(entry)
    if not a or not b:
        return False
    keys_a = {a.get("inchikey"), a.get("parent_inchikey")} - {None}
    keys_b = {b.get("inchikey"), b.get("parent_inchikey")} - {None}
    return bool(keys_a and keys_b and not keys_a & keys_b)


def resolve_all(residuals: Sequence[str], index: Index) -> Resolution:
    evidence: list[Evidence] = []
    unresolvable: list[str] = []
    clean: list[str] = []
    seen: set[str] = set()

    for ingredient in residuals:
        outcome = resolve_one(ingredient, index)
        if outcome is False:
            unresolvable.append(ingredient)
        elif outcome is None:
            clean.append(ingredient)
        elif outcome.entry not in seen:
            seen.add(outcome.entry)
            evidence.append(outcome)

    flush()
    return Resolution(tuple(evidence), tuple(unresolvable), tuple(clean))
