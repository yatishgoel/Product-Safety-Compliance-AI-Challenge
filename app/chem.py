"""Formula and name normalisation. Pure string work, no chemistry lookups.

`to_hill` canonicalises by atom count, so C2H5OH and C2H6O both become C2H6O. `is_formula`
validates every token against the periodic table, which is what classifies BHT as a name.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from itertools import product

ELEMENTS = frozenset("""
    H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni
    Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe
    Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au
    Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf
    Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og""".split())

_SHAPE = re.compile(r"^(?:[A-Z][a-z]?\d*)+$")
_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)")
_PUNCT = re.compile(r"[\s,\-_/()\[\].;:·•*]+")
_NAME_SHAPE = re.compile(r"[A-Za-z][A-Za-z\s\-',.()]*")
_DECORATION = re.compile(r"\s+\((?=[^()]*[\d%])[^()]*\)|\s*\[[^\[\]]*\]")
# Footnote glyphs only. Never trailing digits: formulas end in digits, and stripping them
# collapses H2O2 onto H2O - water would be flagged as a banned oxidiser.
_FOOTNOTE = re.compile(r"[*†‡]+$")

# Codepoints that render identically to Latin. A label with Cyrillic С (U+0421) and Н
# (U+041D) looks exactly like "C6H6" but is a different byte sequence.
_CONFUSABLE = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M",
    "О": "O", "Р": "P", "Т": "T", "Х": "X", "а": "a", "е": "e", "о": "o",
    "р": "p", "с": "c", "х": "x", "‐": "-", "‑": "-", "–": "-", "—": "-",
    "\u00a0": " ", "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "",
})


def clean(s: str) -> str:
    """Undo label decoration and lookalike codepoints, before anything else."""
    s = unicodedata.normalize("NFKC", s.translate(_CONFUSABLE))
    return _FOOTNOTE.sub("", _DECORATION.sub("", s).strip()).strip()


def is_formula(s: str) -> bool:
    """True when every token is a real element symbol.

    C6H6 / NaCl / H2O2 -> True.  BHT / EDTA / Coumarin -> False, because T, E and o are not
    element symbols. Validating against the periodic table is what avoids maintaining a list
    of abbreviations to exclude.
    """
    s = s.strip()
    return bool(s and _SHAPE.match(s)
                and all(el in ELEMENTS for el, _ in _TOKEN.findall(s) if el))


def is_formula_like(s: str) -> bool:
    s = clean(s)
    return is_formula(s) or is_formula(s.replace("€", "C"))


MAX_ATOMS_PER_ELEMENT = 100


def is_plausible(formula: str) -> bool:
    s = clean(formula)
    if not is_formula(s):
        return False
    counts: Counter[str] = Counter()
    for el, n in _TOKEN.findall(s):
        if el:
            counts[el] += int(n) if n else 1
    if max(counts.values(), default=0) > MAX_ATOMS_PER_ELEMENT:
        return False
    return counts.get("H", 0) <= 2 * counts.get("C", 0) + 2 + counts.get("N", 0)


def looks_like_name(s: str) -> bool:
    return bool(_NAME_SHAPE.fullmatch(clean(s).strip()))


def to_hill(formula: str) -> str:
    """Hill notation: carbon first, hydrogen second, rest alphabetical.

    Counts atoms rather than reordering text, so equivalent spellings converge -
    C2H5OH and C2H6O both give C2H6O; CH3COOH and C2H4O2 both give C2H4O2.
    """
    counts: Counter[str] = Counter()
    for el, n in _TOKEN.findall(formula.strip()):
        if el:
            counts[el] += int(n) if n else 1
    order = [e for e in ("C", "H") if e in counts] + sorted(set(counts) - {"C", "H"})
    return "".join(f"{e}{counts[e] if counts[e] > 1 else ''}" for e in order)


def normalize(s: str) -> str:
    """Lookup key for a name. '1,4-Dioxane' -> '14dioxane'."""
    return _PUNCT.sub("", clean(s).lower())


def ocr_variants(s: str, limit: int = 16) -> set[str]:
    """Ways OCR is known to mangle this formula.

    Only corruptions actually observed in Tesseract output on the sample images:
    O read as zero (H2O2 -> H202, C8H10N4O2 -> C8H10N402) and a leading C read as a euro
    sign (C12H24O12 -> EUR12H24O12). Speculative confusions are excluded - an unmeasured
    variant adds false-positive surface for no evidence of benefit.
    """
    s = clean(s)
    if not is_formula(s):
        return set()

    positions = [i for i, ch in enumerate(s) if ch == "O"]
    out: set[str] = set()
    for mask in product("O0", repeat=min(len(positions), 4)):
        chars = list(s)
        for i, ch in zip(positions, mask):
            chars[i] = ch
        variant = "".join(chars)
        out.add(variant)
        if variant[0] == "C":
            out.add("€" + variant[1:])
        if len(out) >= limit:
            break
    return {normalize(v) for v in out} - {normalize(s)}
