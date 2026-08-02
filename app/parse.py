"""Free text to an ingredient list.

Finds the ingredient block across header variants and splits it. Blocks introduced by
"free from" or "formulated without" are excluded: a label that advertises what it omits
must not be rejected for it.
"""

from __future__ import annotations

import re

from app.chem import clean
from app.models import Product

HEADER = (
    r"(?:full\s+)?(?:"
    r"ingredients?\s*/\s*chemical\s+formulas?"
    r"|ingredients?\s+list"
    r"|inci(?:\s+names?)?"
    r"|ingredients?"
    r"|composition"
    r"|contains"
    r")"
)

_HEADER_LINE = re.compile(rf"^\s*{HEADER}\s*[:\-]?\s*(?P<rest>.*)$", re.IGNORECASE)
_PRODUCT_NAME = re.compile(r"^\s*product\s+name\s*[:\-]\s*(?P<value>.+?)\s*$", re.IGNORECASE)
_ABSENCE_CLAIM = re.compile(
    r"free\s+(?:from|of)|formulated\s+without|does\s+not\s+contain|contains\s+no|without\s*:",
    re.IGNORECASE,
)
_SECTION_END = re.compile(
    r"^\s*(?:directions?|warnings?|caution|storage|usage|how\s+to\s+use|net\s+w|"
    r"made\s+in|manufactured|distributed|batch|expiry|best\s+before)\b",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[-*•‣▪◦]+\s*|\d+[.)]\s+)")
_METADATA = re.compile(r"^\s*(?:product\s+id|product\s+name|sku|description|batch|lot)\s*[:\-]",
                       re.IGNORECASE)

MAX_WORDS = 7
MAX_LENGTH = 120


def product_name(text: str) -> str | None:
    for line in text.splitlines():
        found = _PRODUCT_NAME.match(line)
        if found:
            return found.group("value")
    return None


def split_ingredients(line: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    buffer: list[str] = []
    for position, character in enumerate(line):
        if character in "([":
            depth += 1
        elif character in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and character in ",;":
            if line[position + 1 : position + 2].isdigit():
                buffer.append(character)
                continue
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(character)
    parts.append("".join(buffer))
    return [p.strip(" .\t") for p in parts if p.strip(" .\t")]


def looks_like_ingredient(value: str) -> bool:
    return bool(value) and len(value) <= MAX_LENGTH and len(value.split()) <= MAX_WORDS \
        and not _METADATA.match(value)


def parse(text: str, source: str = "") -> Product:
    found: list[str] = []
    inside_block = False

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            if found:
                inside_block = False
            continue

        if _ABSENCE_CLAIM.search(stripped):
            inside_block = False
            continue

        header = _HEADER_LINE.match(stripped)
        if header:
            inside_block = True
            rest = header.group("rest").strip()
            if rest:
                found.extend(split_ingredients(rest))
            continue

        if _SECTION_END.match(stripped):
            inside_block = False
            continue

        bullet = _BULLET.match(line)
        if not inside_block:
            if bullet and looks_like_ingredient(_BULLET.sub("", line).strip()):
                found.append(_BULLET.sub("", line).strip())
            continue

        if bullet:
            found.append(_BULLET.sub("", line).strip())
        elif "," in stripped:
            found.extend(split_ingredients(stripped))
        elif looks_like_ingredient(stripped):
            found.append(stripped)

    seen: set[str] = set()
    ingredients: list[str] = []
    for item in found:
        value = clean(item).strip(" .\t") or item.strip(" .\t")
        if value and value.lower() not in seen and looks_like_ingredient(value):
            seen.add(value.lower())
            ingredients.append(value)

    return Product(name=product_name(text), ingredients=tuple(ingredients), source=source)
