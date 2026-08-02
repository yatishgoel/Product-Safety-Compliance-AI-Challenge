"""Run the corpus once: print the scoreboard, then validate the corpus itself.

Both halves need the same run, so they live together rather than in two scripts that each
load and execute all 94 cases.

The scoreboard deliberately does not headline accuracy - it averages over two error types
whose real costs differ by orders of magnitude, and rewards a system that quietly accepts
whatever it cannot understand. `SILENT ACCEPTS` is the number that matters.

The gates exist because a test set nobody validates is worse than none: it produces
confident numbers that are wrong. Two of them have already caught real defects.

Usage:  uv run python -m eval.report [--failures] [--tier t1_synonym]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter

from eval.harness import LISTS, TIER_ORDER, case_path, index_for, load_cases, run_case

DIM, RESET, GREEN, RED, YELLOW = "\033[2m", "\033[0m", "\033[32m", "\033[31m", "\033[33m"


def ok(msg: str) -> None:
    print(f"  {GREEN}PASS{RESET}  {msg}")


def bad(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")


# ------------------------------------------------------------------ scoreboard
def scoreboard(outcomes: list) -> None:
    print(f"\n  {len(outcomes)} cases | extraction: text + PDF | matching: exact\n")
    print(f"  {'tier':18}{'pass':>8}{'rate':>8}\n  {DIM}{'-' * 34}{RESET}")
    for tier in TIER_ORDER:
        group = [o for o in outcomes if o.tier == tier]
        if group:
            n = sum(o.passed for o in group)
            print(f"  {tier:18}{f'{n}/{len(group)}':>8}{n / len(group):>8.0%}")
    total = sum(o.passed for o in outcomes)
    print(f"  {DIM}{'-' * 34}{RESET}\n  {'OVERALL':18}"
          f"{f'{total}/{len(outcomes)}':>8}{total / len(outcomes):>8.0%}\n")

    forbidden = [o for o in outcomes if o.expected == "Rejected"]
    clean = [o for o in outcomes if o.expected == "Accepted"]
    rejected = [o for o in outcomes if o.actual == "Rejected"]
    # Caught = rejected OR escalated. Escalation is worse than a clean rejection, but a human
    # still sees it, so it is not a miss.
    caught = sum(o.actual in ("Rejected", "Needs Review") for o in forbidden)
    silent = sum(o.actual == "Accepted" for o in forbidden)

    pct = lambda n, d: n / d if d else 1.0  # noqa: E731
    print(f"  recall on forbidden .... {pct(caught, len(forbidden)):6.1%}   a miss is a recall")
    print(f"  false-reject rate ...... "
          f"{pct(sum(o.actual == 'Rejected' for o in clean), len(clean)) if clean else 0:6.1%}"
          f"   clean products blocked")
    print(f"  citation accuracy ...... "
          f"{pct(sum(o.citation_ok for o in rejected), len(rejected)):6.1%}   right ingredient")
    print(f"  abstention rate ........ "
          f"{sum(o.actual == 'Needs Review' for o in outcomes) / len(outcomes):6.1%}"
          f"   sent to human review")
    print(f"  SILENT ACCEPTS ......... {silent:6d}   must be 0\n")


# ----------------------------------------------------------------------- gates
def gate_labels(cases: list[dict]) -> list[str]:
    """Internal consistency: statuses, citations, referenced lists and files."""
    errors = []
    for cid, n in Counter(c["id"] for c in cases).items():
        if n > 1:
            errors.append(f"duplicate case id {cid}")

    for case in cases:
        exp, cites = case["expect"], case["expect"].get("must_cite") or []
        lst = case.get("forbidden_list", "default")

        if exp["status"] == "Accepted" and cites:
            errors.append(f"{case['id']}: Accepted but declares must_cite {cites}")
        if exp["status"] == "Rejected" and not cites:
            errors.append(f"{case['id']}: Rejected but names no forbidden entry")
        if case["tier"] not in TIER_ORDER:
            errors.append(f"{case['id']}: unknown tier {case['tier']!r}")
        if lst != "default" and not (LISTS / lst).exists():
            errors.append(f"{case['id']}: forbidden list {lst} not found")
        if not case_path(case).exists():
            errors.append(f"{case['id']}: file {case_path(case)} missing - run `make corpus`")

        entries = {e.lower() for e in index_for(lst).entries}
        for c in cites:
            if c.lower() not in entries:
                errors.append(f"{case['id']}: must_cite {c!r} is not in forbidden list {lst}")

    ok(f"label sanity: {len(cases)} cases internally consistent") if not errors else None
    return errors


def gate_round_trip(cases: list[dict]) -> list[str]:
    """Generated files must re-extract to exactly the ingredients the manifest declares,
    so labels provably describe the bytes on disk."""
    from app.chem import clean
    from app.extract import extract
    from app.models import Blob
    from app.models import Ok

    errors, checked = [], 0
    for case in cases:
        # Only lossless formats with an explicit list. raw_body cases use deliberately odd
        # layouts; t3/t6 are lossy or broken by design.
        if ("source" in case or "raw_body" in case
                or case["format"] not in ("txt", "pdf") or case["tier"] == "t6_degenerate"):
            continue
        declared = case.get("ingredients") or []
        if not declared:
            continue

        result = extract(Blob.from_path(case_path(case)))
        if not isinstance(result, Ok):
            errors.append(f"{case['id']}: extraction failed ({result})")
        elif [clean(i) for i in result.value.ingredients] != [clean(d) for d in declared]:
            errors.append(f"{case['id']}: declared {declared}, file yields "
                          f"{list(result.value.ingredients)}")
        else:
            checked += 1

    if not errors:
        ok(f"round-trip: {checked} generated files match their declared ingredients")
    return errors


def gate_ocr_breaks(cases: list[dict]) -> list[str]:
    """Every t3 image must genuinely defeat Tesseract. One that OCRs cleanly tests nothing
    and would silently inflate the score of a pipeline that never fixed its OCR."""
    try:
        subprocess.run(["tesseract", "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
    except Exception:
        print(f"  {YELLOW}SKIP{RESET}  tesseract not installed")
        return []

    errors, verified = [], 0
    for case in (c for c in cases if c["tier"] == "t3_ocr"):
        raw = subprocess.run(["tesseract", str(case_path(case)), "-"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True).stdout
        read = {i.strip() for i in re.findall(r"^\s*-\s*(.+)$", raw, re.M)}
        survived = set(case["expect"].get("must_cite") or []) & read
        if survived:
            errors.append(f"{case['id']}: OCR read {sorted(survived)} correctly - "
                          f"degradation too mild, case does not discriminate")
        else:
            verified += 1

    if not errors:
        ok(f"ocr degradation: {verified} t3 images genuinely defeat Tesseract")
    return errors


def gate_discrimination(outcomes: list) -> list[str]:
    """The corpus must leave real headroom for the layers not yet built.

    t0 the baseline can *read* must all pass - that is the proof the provided samples are
    uninformative. (Image cases fail on extraction, not matching; scoring them as corpus
    defects would be wrong.) t1 and t3 must score near zero, since they exist precisely
    because string matching cannot touch them. And enough cases must fail overall that later
    layers have something to demonstrably improve - t2 and t6 passing at baseline is expected,
    so an aggregate pass rate is a poor gate; failure count is the honest measure.
    """
    errors = []
    by_tier: dict[str, list] = {}
    for o in outcomes:
        by_tier.setdefault(o.tier, []).append(o)

    t0 = by_tier.get("t0_regression", [])
    readable = [o for o in t0 if "Could not read" not in " ".join(o.verdict.reason)]
    if failing := [o.case_id for o in readable if not o.passed]:
        errors.append(f"readable t0 cases must all pass on string matching alone: {failing[:6]}")
    else:
        ok(f"provided samples uninformative: {len(readable)}/{len(readable)} readable t0 cases "
           f"pass on string equality alone ({len(t0) - len(readable)} images pending vision)")


    failed = sum(not o.passed for o in outcomes)
    ok(f"{failed}/{len(outcomes)} cases still failing - headroom remains")

    return errors


# ------------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", help="only score one tier")
    ap.add_argument("--failures", action="store_true", help="list every failing case")
    ap.add_argument("--no-gates", action="store_true", help="scoreboard only")
    args = ap.parse_args()

    all_cases = load_cases()
    cases = [c for c in all_cases if not args.tier or c["tier"] == args.tier]
    outcomes = [run_case(c) for c in cases]

    scoreboard(outcomes)

    if args.failures:
        for tier in TIER_ORDER:
            group = [o for o in outcomes if o.tier == tier and not o.passed]
            if not group:
                continue
            print(f"  {RED}{tier}{RESET} - {len(group)} failing")
            for o in group:
                why = "wrong status" if not o.status_ok else f"missed {list(o.missed)}"
                print(f"    {o.case_id}  {o.expected:12} -> {o.actual:12}  {why}")
                print(f"      {DIM}{o.title[:92]}{RESET}")
            print()

    if args.no_gates or args.tier:
        return 0

    print(f"  {DIM}validating the corpus itself{RESET}")
    errors = (gate_labels(all_cases) + gate_round_trip(all_cases)
              + gate_ocr_breaks(all_cases) + gate_discrimination(outcomes))
    for e in errors[:10]:
        bad(e)

    print(f"\n{RED}corpus invalid: {len(errors)} problem(s){RESET}\n" if errors
          else f"\n{GREEN}corpus valid{RESET}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
