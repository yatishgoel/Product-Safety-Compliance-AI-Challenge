"""Independently verify the chemistry asserted in manifest.yaml against PubChem.

The gap this closes: every `must_cite` in the corpus is a claim that some ingredient in the
case IS the cited forbidden entry - "Benzene is C6H6", "E218 is Methylparaben". Those claims
were typed by hand. Nothing in the repo re-checked them, so a wrong one would sit there
looking authoritative, and every gate would still pass.

This asks a third party. For each claim, resolve both sides to PubChem CIDs and check they
overlap. Three outcomes:

    confirmed    both sides resolve and share a CID - the claim is externally corroborated
    REFUTED      both sides resolve and share nothing - the label is probably wrong
    unverified   PubChem cannot resolve one side, so it cannot rule either way

`unverified` is expected and fine. PubChem returns 404 for "Butylated Hydroxytoluene" despite
storing 323 synonyms for the compound - which is exactly why an offline structural resolver
earns its place in the solution. Those claims fall back to manual assertion, and the report
says so out loud rather than implying everything was checked.

Network + rate-limited, so this is not part of `make report`. It writes a cache that the
offline gate reads on every run.

Usage:  uv run python -m eval.verify_labels [--refresh]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from app.chem import clean, is_formula, to_hill
from eval.harness import CORPUS, index_for, load_cases

CACHE = CORPUS / "label_verification.json"
BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"


def cids(term: str) -> list[int] | None:
    """PubChem CIDs for a name or formula. None means 'could not resolve'."""
    term = clean(term)
    endpoint = "fastformula" if is_formula(term) else "name"
    value = to_hill(term) if is_formula(term) else term
    url = f"{BASE}/{endpoint}/{urllib.parse.quote(value, safe='')}/cids/JSON"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return None  # 404 (genuinely unknown) or transient - either way, no answer
    got = data.get("IdentifierList", {}).get("CID") or []
    return got[:50] or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignore the existing cache")
    args = ap.parse_args()

    resolved: dict[str, list[int] | None] = {}
    if CACHE.exists() and not args.refresh:
        resolved = {k: v for k, v in json.loads(CACHE.read_text())["resolved"].items()}

    def lookup(term: str) -> list[int] | None:
        if term not in resolved:
            resolved[term] = cids(term)
            time.sleep(0.25)  # PubChem allows ~5 req/s
        return resolved[term]

    results = []
    for case in load_cases():
        required = case["expect"].get("must_cite") or []
        ingredients = case.get("ingredients") or []
        if not required or not ingredients:
            continue  # t0 (real files) and raw_body cases have no declared ingredient list

        entries = {e.lower(): e for e in index_for(case.get("forbidden_list", "default")).entries}
        for cited in required:
            entry = entries.get(cited.lower(), cited)
            entry_cids = lookup(entry)

            verdict, matched = "unverified", None
            if entry_cids:
                for ing in ingredients:
                    ing_cids = lookup(ing)
                    if ing_cids and set(ing_cids) & set(entry_cids):
                        verdict, matched = "confirmed", ing
                        break
                else:
                    # Every ingredient resolved and none overlapped -> the claim is refuted.
                    # If any failed to resolve, we simply do not know.
                    if all(lookup(i) for i in ingredients):
                        verdict = "REFUTED"
            results.append({"case": case["id"], "entry": entry,
                            "verdict": verdict, "matched_ingredient": matched})

    CACHE.write_text(json.dumps(
        {"resolved": resolved, "claims": results}, indent=1, sort_keys=True))

    counts = {v: sum(r["verdict"] == v for r in results)
              for v in ("confirmed", "REFUTED", "unverified")}
    print(f"\n  {len(results)} synonym claims checked against PubChem")
    for k, n in counts.items():
        print(f"    {k:12} {n}")

    if counts["REFUTED"]:
        print("\n  REFUTED - these labels are probably wrong:")
        for r in results:
            if r["verdict"] == "REFUTED":
                print(f"    {r['case']}: no ingredient resolves to {r['entry']!r}")

    unver = [r for r in results if r["verdict"] == "unverified"]
    if unver:
        print("\n  unverified - PubChem could not resolve one side; these rest on manual"
              " assertion:")
        for r in unver:
            print(f"    {r['case']}: {r['entry']}")

    print(f"\n  cache written to {CACHE.relative_to(CACHE.parents[2])}\n")
    return 1 if counts["REFUTED"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
