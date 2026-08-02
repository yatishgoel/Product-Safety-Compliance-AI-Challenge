"""Evidence to a verdict.

Missing a banned substance means a recall; wrongly flagging a clean product costs a reviewer
five minutes. So doubt escalates to Needs Review and never resolves to Accepted.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.index import Index
from app.models import Evidence, Product, Status, Verdict


ACTIONABLE = ("certain", "strong")


def decide(product: Product, evidence: Sequence[Evidence], index: Index,
           unread: Sequence[str] = ()) -> Verdict:
    strong = [e for e in evidence if e.tier in ACTIONABLE]
    weak = [e for e in evidence if e.tier not in ACTIONABLE]

    if strong:
        evidence = strong
        return Verdict(
            product_name=product.name,
            status=Status.REJECTED,
            reason=tuple(e.render() for e in evidence),
            evidence=tuple(evidence),
            unread=tuple(unread),
            index_version=index.version,
        )

    if weak:
        return Verdict(
            product_name=product.name,
            status=Status.NEEDS_REVIEW,
            reason=tuple(f"Possible {e.entry}: {e.ingredient} shares its molecular formula, "
                         "which many compounds do" for e in weak),
            evidence=tuple(weak),
            unread=tuple(unread),
            index_version=index.version,
        )

    if unread:
        listed = ", ".join(unread)
        return Verdict(
            product_name=product.name,
            status=Status.NEEDS_REVIEW,
            reason=(f"Could not read {len(unread)} ingredient(s): {listed}",),
            unread=tuple(unread),
            index_version=index.version,
        )

    if index.entries and len(index.degraded) == len(index.entries):
        return Verdict(
            product_name=product.name,
            status=Status.NEEDS_REVIEW,
            reason=(f"Forbidden list incompletely resolved ({len(index.degraded)} entries); "
                    "cannot clear this product",),
            index_version=index.version,
        )

    return Verdict(product_name=product.name, status=Status.ACCEPTED,
                   index_version=index.version)
