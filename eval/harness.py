"""Shared plumbing: load the manifest, run one case through the pipeline, score the result.

Used by check_corpus.py, score.py and run_experiment.py so all three agree on what a case
means and how it is judged.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.models import Blob
from app.index import Index, build
from app.models import Status, Verdict
from app.evaluate import evaluate

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "eval" / "corpus"
GENERATED = CORPUS / "generated"
LISTS = CORPUS / "lists"
DEFAULT_LIST = ROOT / "forbidden_ingredients.csv"

TIER_ORDER = [
    "t0_regression",
    "t1_synonym",
    "t2_near_miss",
    "t3_ocr",
    "t4_layout",
    "t5_adversarial",
    "t6_degenerate",
    "t7_lists",
]

_EXT = {"txt": ".txt", "pdf": ".pdf", "png": ".png", "pdf_scanned": ".pdf"}


def load_cases() -> list[dict]:
    return yaml.safe_load((CORPUS / "manifest.yaml").read_text())["cases"]


def case_path(case: dict) -> Path:
    if "source" in case:
        return ROOT / case["source"]
    return GENERATED / case["tier"] / f"{case['id']}{_EXT[case['format']]}"


@lru_cache(maxsize=None)
def index_for(list_name: str) -> Index:
    src = DEFAULT_LIST if list_name == "default" else LISTS / list_name
    return build(src)


@dataclass(frozen=True)
class Outcome:
    case_id: str
    tier: str
    title: str
    expected: str
    actual: str
    status_ok: bool
    citation_ok: bool
    missed: tuple[str, ...]      # required entries the verdict failed to name
    verdict: Verdict

    @property
    def passed(self) -> bool:
        return self.status_ok and self.citation_ok


def run_case(case: dict) -> Outcome:
    index = index_for(case.get("forbidden_list", "default"))
    path = case_path(case)

    if not path.exists():
        raise FileNotFoundError(f"{case['id']}: {path} missing - run `make corpus`")

    verdict = evaluate(Blob.from_path(path), index)

    expected = case["expect"]["status"]
    # Case-insensitive: the verdict echoes the forbidden list's own spelling, so a list
    # written as "coumarin" produces a citation of "coumarin". Which *entry* was matched is
    # what matters for an audit; its capitalisation is the list author's business.
    required = {c.lower() for c in (case["expect"].get("must_cite") or [])}
    cited = {c.lower() for c in verdict.entries}
    missed = tuple(sorted(required - cited))

    return Outcome(
        case_id=case["id"],
        tier=case["tier"],
        title=case["title"],
        expected=expected,
        actual=verdict.status.value,
        status_ok=verdict.status.value == expected,
        # Citation only applies to rejections: a system that rejects for the wrong reason has
        # not really solved the case, even though its status matched.
        citation_ok=not missed,
        missed=missed,
        verdict=verdict,
    )


def is_forbidden_case(case: dict) -> bool:
    """Cases where a banned substance is genuinely present - the recall denominator."""
    return case["expect"]["status"] == Status.REJECTED.value
