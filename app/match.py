"""Ingredients to evidence, by offline lookup against the index.

Anything that matches nothing is returned as a residual for the next stage of the cascade.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from app.chem import clean, is_formula, normalize, to_hill
from app.index import Index
from app.models import Evidence

DRAWERS = ("literal", "hill", "ocr_variant")


def candidate_keys(ingredient: str) -> Iterator[tuple[str, str]]:
    cleaned = clean(ingredient)
    key = normalize(ingredient)
    if not key:
        return
    yield "literal", key
    yield "ocr_variant", key
    if is_formula(cleaned):
        yield "hill", to_hill(cleaned)


def best_hit(ingredient: str, index: Index) -> Evidence | None:
    for drawer, key in candidate_keys(ingredient):
        if drawer not in DRAWERS:
            continue
        found = index.lookup(drawer, key)
        if found:
            entry, tier = found
            return Evidence(ingredient=ingredient, entry=entry, drawer=drawer, tier=tier)
    return None


def match(ingredients: Sequence[str], index: Index) -> tuple[tuple[Evidence, ...], tuple[str, ...]]:
    seen: set[str] = set()
    hits: list[Evidence] = []
    residuals: list[str] = []
    for ingredient in ingredients:
        evidence = best_hit(ingredient, index)
        if evidence is None:
            residuals.append(ingredient)
        elif evidence.entry not in seen:
            seen.add(evidence.entry)
            hits.append(evidence)
    return tuple(hits), tuple(residuals)


def unread(suspect: Sequence[str], index: Index) -> tuple[str, ...]:
    return tuple(token for token in suspect if not best_hit(token, index))
