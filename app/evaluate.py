"""The pipeline: extract, match, resolve, judge, decide.

Each stage only sees what the previous one could not handle.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

from app import trace, vision
from app.extract import extract
from app.index import DEFAULT_LIST, Index, build
from app.judge import judge
from app.match import match, unread
from app.resolve import contradicts, resolve_all
from app.models import Blob, Err, Ok, Status, Verdict
from app.policy import decide

ROOT = Path(__file__).parent.parent


def _summarise(verdict: Verdict) -> str:
    """One readable line for the trace list, rather than a bare '[]' on a clean product."""
    if not verdict.reason:
        return f"{verdict.status.value}: nothing forbidden found"
    return f"{verdict.status.value}: {'; '.join(verdict.reason)}"


def evaluate(blob: Blob, index: Index, policy=decide,
             extractor: str = "auto") -> Verdict:
    with trace.stage(f"evaluate {blob.filename}", inputs=blob.filename,
                     file=blob.filename, bytes=len(blob.data),
                     index_version=index.version) as root:
        with trace.stage("1. extract ingredients", inputs=blob.filename) as span:
            only = None
            if extractor == "native":
                only = "pdf" if blob.suffix == ".pdf" else "text"
            elif extractor != "auto":
                only = extractor
            product = extract(blob, only=only)
            if isinstance(product, Ok):
                span.set(engine=product.value.source,
                         ingredients_out=len(product.value.ingredients),
                         unreadable=len(product.value.suspect or ()))
                span.output(product.value.ingredients)
            else:
                span.set(ingredients_out=0)
                span.output(str(product))

        if isinstance(product, Err):
            verdict = Verdict(
                product_name=None,
                status=Status.NEEDS_REVIEW,
                reason=(f"Could not read {blob.filename} ({product}); manual review required",),
                unread=(blob.filename,),
                index_version=index.version,
            )
            root.set(status=verdict.status.value)
            root.output(_summarise(verdict))
            return verdict

        found = product.value
        with trace.stage("2. match offline index", inputs=found.ingredients) as span:
            evidence, residuals = match(found.ingredients, index)
            unknown = list(unread(found.suspect, index))
            span.set(ingredients_in=len(found.ingredients), matched=len(evidence),
                     residuals_out=len(residuals), unreadable=len(unknown))
            span.output([e.render() for e in evidence] or "no offline match")

        if residuals:
            with trace.stage("3. resolve on PubChem", inputs=residuals) as span:
                before = len(residuals)
                resolution = resolve_all(residuals, index)
                evidence += resolution.evidence
                residuals = resolution.clean + resolution.unresolvable
                span.set(residuals_in=before, matched=len(resolution.evidence),
                         residuals_out=len(residuals),
                         unresolvable=len(resolution.unresolvable))
                span.output([e.render() for e in resolution.evidence] or "no structural match")

        if residuals and not evidence:
            with trace.stage("4. judge with LLM", inputs=residuals) as span:
                result = judge(residuals, index.entries)
                claimed = result.value if isinstance(result, Ok) else []
                kept = [e for e in claimed if not contradicts(e.ingredient, e.entry)]
                evidence += tuple(kept)
                span.set(residuals_in=len(residuals), claimed=len(claimed),
                         vetoed=len(claimed) - len(kept), matched=len(kept))
                span.output([e.render() for e in kept] or "no match claimed")

        with trace.stage("5. decide verdict",
                         inputs=[e.render() for e in evidence] or "no evidence") as span:
            verdict = replace(
                policy(found, evidence, index, tuple(dict.fromkeys(unknown))),
                ingredients=found.ingredients,
                extraction_source=found.source,
            )
            span.set(status=verdict.status.value, evidence_in=len(evidence),
                     unreadable_in=len(set(unknown)))
            span.output(_summarise(verdict))

        root.set(status=verdict.status.value, product_name=verdict.product_name,
                 ingredients=len(found.ingredients), evidence=len(evidence),
                 model=vision.MODEL)
        root.output(_summarise(verdict))
        return verdict


def _report(verdict: Verdict) -> None:
    print(f"\n  {verdict.product_name or '(no name)'}  ->  {verdict.status.value}")
    for line in verdict.reason:
        print(f"    {line}")
    print(f"    index {verdict.index_version}\n")


def _check(index: Index) -> int:
    """Score the pipeline against the provided samples. Reachable only from `--check`.

    The ids below are the answer key I read off the sample labels by hand, used to grade the
    output. Nothing here is consulted while deciding: `evaluate()` never sees it, and neither
    does the API. Grading against known answers is not the same as hardcoding them, but the
    distinction matters enough to say out loud.
    """
    truth_accept = {"P-2025-0003", "P-2025-0014", "P-2025-0022",
                    "P-2025-0024", "P-2025-0025", "P-2025-0028"}
    with open(ROOT / "product_index.csv") as handle:
        rows = list(csv.DictReader(handle))

    tally: Counter[str] = Counter()
    exact = wrong = silent = 0
    print(f"\n  {'product':14}{'expected':11}{'verdict':14}reason")
    with trace.session(f"check of {len(rows)} provided products"):
        for row in rows:
            verdict = evaluate(Blob.from_path(ROOT / row["filename"]), index)
            expected = Status.ACCEPTED if row["product_id"] in truth_accept else Status.REJECTED
            tally[verdict.status.value] += 1
            exact += verdict.status is expected
            wrong += verdict.status is not expected and verdict.status is not Status.NEEDS_REVIEW
            silent += expected is Status.REJECTED and verdict.status is Status.ACCEPTED
            flag = (" " if verdict.status is expected
                    else "~" if verdict.status is Status.NEEDS_REVIEW else "!")
            print(f" {flag}{row['product_id']:14}{expected.value:11}{verdict.status.value:14}"
                  f"{(verdict.reason[0] if verdict.reason else '')[:46]}")

    print(f"\n  {' · '.join(f'{k} {v}' for k, v in sorted(tally.items()))}")
    print(f"  exact {exact}/{len(rows)} · wrong {wrong} · silent accepts {silent}\n")
    return 1 if wrong or silent else 0


SUFFIXES = (".txt", ".csv", ".md", ".pdf", ".png", ".jpg", ".jpeg")


def _report_dir(folder: Path, index: Index) -> int:
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in SUFFIXES)
    if not files:
        print(f"  no product files under {folder}")
        return 1
    tally: Counter[str] = Counter()
    print(f"\n  {'file':44}{'verdict':14}reason")
    for path in files:
        verdict = evaluate(Blob.from_path(path), index)
        tally[verdict.status.value] += 1
        reason = verdict.reason[0] if verdict.reason else ""
        print(f"  {path.name[:42]:44}{verdict.status.value:14}{reason[:44]}")
    print(f"\n  {' · '.join(f'{k} {v}' for k, v in sorted(tally.items()))}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="a product file, or a directory to scan")
    parser.add_argument("--list", default=str(DEFAULT_LIST))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    trace.setup()
    index = build(args.list)
    try:
        if args.check or not args.path:
            return _check(index)

        target = Path(args.path)
        if target.is_dir():
            return _report_dir(target, index)

        _report(evaluate(Blob.from_path(target), index))
        return 0
    finally:
        trace.flush()


if __name__ == "__main__":
    sys.exit(main())
