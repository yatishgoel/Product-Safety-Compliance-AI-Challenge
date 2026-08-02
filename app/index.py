"""Compile a forbidden list into a lookup table, once, at startup.

The list is small and changes rarely; the product stream is unbounded. So each entry is
resolved against PubChem once and filed under every key an ingredient could match on:
name, synonym, formula, OCR corruption, structure hash, desalted structure, registry id.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.chem import clean, is_formula, normalize, ocr_variants, to_hill
from app.pubchem import flush, resolve

CERTAIN, STRONG, WEAK = "certain", "strong", "weak"
DEFAULT_LIST = Path(__file__).parent.parent / "forbidden_ingredients.csv"

# Header words that mean the first CSV row is a header, not data.
_HEADERS = {"ingredient", "ingredients", "name", "substance", "chemical", "entry", "cas",
            "cas_number"}
# PubChem synonym lists mix real names with supplier catalogue codes - BHT's 323 include
# "3IM" and "P 21". Very short keys are the collision risk, so they are dropped.
#
# The threshold applies to the *normalised key*, not the raw synonym, because normalising
# shortens things unpredictably: "Dbpc (technical grade)" is 22 characters but keys as
# "dbpc", while "E321" is 4 characters and keys as "e321". Filtering the raw string let the
# first through and blocked the second - exactly backwards, since E-numbers are useful and
# catalogue codes are not.
#
# 4 keeps "e218", "e321" and "dbpc" (all genuine identifiers) and drops "bht", "3im", "p21".
# Dropping "bht" costs nothing: an entry's own name is filed separately as CERTAIN.
MIN_KEY_LEN = 4


def load_entries(source: str | Path | bytes) -> list[str]:
    """Read a forbidden list from CSV, JSON, or newline-delimited text.

    Permissive about format because the list is user-supplied and must not be hardcoded;
    strict about content - quoting and whitespace stripped, blanks dropped, duplicates
    removed case-insensitively while preserving the author's ordering.
    """
    if isinstance(source, bytes):
        raw, suffix = source, ""
    else:
        raw, suffix = Path(source).read_bytes(), Path(source).suffix.lower()
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return []

    if suffix == ".json" or text.startswith(("[", "{")):
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = next((v for v in payload.values() if isinstance(v, list)), [])
        raw = [str(x) for x in payload]
    else:
        rows = list(csv.reader(io.StringIO(text)))
        if rows and rows[0] and rows[0][0].strip().lower() in _HEADERS:
            rows = rows[1:]
        raw = [cell for row in rows for cell in row if cell.strip()]

    seen: set[str] = set()
    out: list[str] = []
    for e in raw:
        v = e.strip().strip("\"'").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


@dataclass
class Index:
    entries: tuple[str, ...]
    drawers: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)
    degraded: tuple[str, ...] = ()
    version: str = ""

    def add(self, drawer: str, key: str, entry: str, tier: str) -> None:
        if key:
            # First writer wins, so the entry's own CERTAIN key is never downgraded by a
            # synonym's STRONG one.
            self.drawers.setdefault(drawer, {}).setdefault(key, (entry, tier))

    def lookup(self, drawer: str, key: str) -> tuple[str, str] | None:
        return self.drawers.get(drawer, {}).get(key)

    def __len__(self) -> int:
        return len(self.entries)


def build(source: str | Path | bytes = DEFAULT_LIST, refresh: bool = False) -> Index:
    """Resolve every entry and file it under every key it could be found under.

    Entries PubChem cannot resolve are recorded in `degraded` rather than skipped silently.
    The offline drawers still get built, so the index is usable but knowingly incomplete -
    a later phase reads that flag and escalates instead of accepting.
    """
    entries = load_entries(source)
    idx = Index(entries=tuple(entries))
    degraded: list[str] = []

    for entry in entries:
        # Offline drawers first: these need no network and always populate.
        idx.add("literal", normalize(entry), entry, CERTAIN)
        if is_formula(clean(entry)):
            idx.add("hill", to_hill(clean(entry)), entry, CERTAIN)
        for variant in ocr_variants(entry):
            idx.add("ocr_variant", variant, entry, CERTAIN)

        r = resolve(entry, refresh=refresh)
        if not r:
            degraded.append(entry)
            continue

        if r.get("inchikey"):
            idx.add("inchikey", r["inchikey"], entry, CERTAIN)
        if r.get("parent_inchikey"):
            idx.add("inchikey_parent", r["parent_inchikey"], entry, STRONG)
        idx.add("pubchem_cid", str(r["cid"]), entry, STRONG)
        if r.get("formula") and not is_formula(clean(entry)):
            idx.add("hill", to_hill(r["formula"]), entry, WEAK)

        for s in r.get("synonyms", []):
            key = normalize(s)
            if len(key) < MIN_KEY_LEN:
                continue
            idx.add("literal", key, entry, STRONG)
            if is_formula(clean(s)):
                idx.add("hill", to_hill(clean(s)), entry, WEAK)

    flush()
    idx.degraded = tuple(degraded)
    digest = hashlib.sha256("\n".join(normalize(e) for e in entries).encode()).hexdigest()[:12]
    idx.version = f"{len(entries)}e-{digest}" + ("+degraded" if degraded else "")
    return idx


@lru_cache(maxsize=8)
def build_cached(source: str | bytes = str(DEFAULT_LIST)) -> Index:
    return build(source)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default=str(DEFAULT_LIST))
    ap.add_argument("--refresh", action="store_true", help="ignore the cache, re-resolve")
    ap.add_argument("--lookup", help="trace one term through the drawers")
    args = ap.parse_args()

    idx = build(args.list, refresh=args.refresh)

    print(f"\n  {len(idx)} entries | version {idx.version}")
    for drawer in ("literal", "hill", "ocr_variant", "inchikey", "inchikey_parent",
                   "pubchem_cid"):
        print(f"    {drawer:16} {len(idx.drawers.get(drawer, {})):>5} keys")
    print(f"  degraded: {', '.join(idx.degraded) if idx.degraded else 'none'}\n")

    if args.lookup:
        term = args.lookup
        print(f"  tracing {term!r}")
        keys = [("literal", normalize(term))]
        if is_formula(clean(term)):
            keys += [("hill", to_hill(clean(term))), ("ocr_variant", normalize(term))]
        else:
            keys += [("ocr_variant", normalize(term))]
        for drawer, key in keys:
            hit = idx.lookup(drawer, key)
            print(f"    {drawer:16} key={key!r:24} -> {hit or 'no hit'}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
