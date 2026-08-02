"""PubChem client. Resolves a name or formula to CID, InChIKey, parent InChIKey and synonyms.

The parent relation is what makes salt substitution detectable: sodium, potassium and
ammonium lauryl sulfate all reduce to the same parent compound. Results are cached on disk.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.chem import clean, is_formula, to_hill

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"
CACHE = Path(__file__).parent / "data" / "pubchem_cache.json"
_LAST_CALL = 0.0
_CACHE: dict[str, dict | None] | None = None
_DIRTY = False


def _cache() -> dict[str, dict | None]:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    return _CACHE


def flush() -> None:
    global _DIRTY
    if _DIRTY:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_cache(), indent=1, sort_keys=True))
        _DIRTY = False


def _get(path: str) -> dict | None:
    """One rate-limited GET. None means "no answer" - never "the answer is nothing"."""
    global _LAST_CALL
    wait = 0.25 - (time.monotonic() - _LAST_CALL)   # PubChem allows ~5 req/s
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL = time.monotonic()
    try:
        with urllib.request.urlopen(f"{BASE}/{path}", timeout=15) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _inchikey(cid: int) -> str | None:
    d = _get(f"cid/{cid}/property/InChIKey/JSON") or {}
    props = d.get("PropertyTable", {}).get("Properties") or [{}]
    return props[0].get("InChIKey")


def resolve(term: str, refresh: bool = False) -> dict | None:
    global _DIRTY
    key = clean(term)
    if not refresh and key in _cache():
        return _cache()[key]
    found = _lookup(key)
    _cache()[key] = found
    _DIRTY = True
    return found


def _lookup(term: str) -> dict | None:
    """Look up one term. Returns None when PubChem cannot answer - offline, 404, or throttled.

    The caller must treat None as "unknown", not "clean". That distinction is the whole
    reason this returns None rather than an empty dict.
    """
    term = clean(term)
    if not term:
        return None

    if is_formula(term):
        path = f"fastformula/{urllib.parse.quote(to_hill(term), safe='')}/cids/JSON"
    else:
        path = f"name/{urllib.parse.quote(term, safe='')}/cids/JSON"

    d = _get(path)
    cids = (d or {}).get("IdentifierList", {}).get("CID") or []
    if not cids:
        return None

    # First CID only. PubChem returns the canonical compound first, and taking all of them
    # for a formula would pull in every isomer - 193 for C6H6, 2233 for C8H10N4O2 - which is
    # how a formula match turns into a false positive on an unrelated compound.
    cid = cids[0]

    parent = _get(f"cid/{cid}/cids/JSON?cids_type=parent") or {}
    parent_cid = (parent.get("IdentifierList", {}).get("CID") or [cid])[0]

    syn = _get(f"cid/{cid}/synonyms/JSON") or {}
    info = syn.get("InformationList", {}).get("Information") or [{}]

    formula = (_get(f"cid/{cid}/property/MolecularFormula/JSON") or {}) \
        .get("PropertyTable", {}).get("Properties", [{}])[0].get("MolecularFormula")

    return {
        "cid": cid,
        "inchikey": _inchikey(cid),
        "parent_cid": parent_cid,
        "parent_inchikey": _inchikey(parent_cid) if parent_cid != cid else _inchikey(cid),
        "formula": formula,
        "synonyms": info[0].get("Synonym") or [],
    }
