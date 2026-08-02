"""Verdict rules. The safety property is that doubt never resolves to Accepted."""

import pytest

from app.index import Index
from app.models import Evidence, Product, Status
from app.policy import decide

PRODUCT = Product(name="Test", ingredients=("Aqua",), source="text")


def index(entries=("Coumarin",), degraded=()) -> Index:
    return Index(entries=tuple(entries), drawers={}, degraded=tuple(degraded), version="v1")


def evidence(tier: str, entry: str = "Coumarin") -> Evidence:
    return Evidence(ingredient="x", entry=entry, drawer="literal", tier=tier)


class TestVerdicts:
    @pytest.mark.parametrize("tier", ["certain", "strong"])
    def test_solid_evidence_rejects(self, tier):
        verdict = decide(PRODUCT, [evidence(tier)], index())
        assert verdict.status is Status.REJECTED
        assert verdict.entries == {"Coumarin"}

    def test_weak_evidence_escalates(self):
        """A shared molecular formula matches many compounds, so it is not enough."""
        assert decide(PRODUCT, [evidence("weak")], index()).status is Status.NEEDS_REVIEW

    def test_clean_check_accepts(self):
        assert decide(PRODUCT, [], index()).status is Status.ACCEPTED


class TestFailsClosed:
    def test_unreadable_ingredient_escalates(self):
        verdict = decide(PRODUCT, [], index(), unread=("H202",))
        assert verdict.status is Status.NEEDS_REVIEW
        assert "H202" in verdict.unread

    def test_solid_evidence_outranks_unreadable(self):
        verdict = decide(PRODUCT, [evidence("certain")], index(), unread=("H202",))
        assert verdict.status is Status.REJECTED

    def test_fully_unresolved_list_escalates(self):
        both = ("Coumarin", "BHT")
        assert decide(PRODUCT, [], index(both, degraded=both)).status is Status.NEEDS_REVIEW

    def test_one_unresolvable_entry_does_not_block_everything(self):
        """Regression: an empty or partly unresolved list used to escalate every product."""
        assert decide(PRODUCT, [], index(("Coumarin", "BHT"), degraded=("BHT",))).status \
            is Status.ACCEPTED
        assert decide(PRODUCT, [], index((), ())).status is Status.ACCEPTED


class TestReasons:
    def test_names_the_forbidden_entry_not_the_matched_text(self):
        hit = Evidence(ingredient="H202", entry="H2O2", drawer="ocr_variant", tier="certain")
        reason = decide(PRODUCT, [hit], index(("H2O2",))).reason[0]
        assert "H2O2" in reason and reason.startswith("Contains H2O2")

    def test_carries_the_index_version(self):
        assert decide(PRODUCT, [], index()).index_version == "v1"
