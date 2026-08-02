"""File bytes to a product.

Extractors are tried cheapest first, with Tesseract as the fallback that is always
available. OCR mangles chemical formulas, so output from a lossy engine is checked for
chemically impossible tokens and escalated to the next engine if any are found.

EasyOCR was removed: it read C8H10N4O2 as C8HION4O2, a real iodine compound, so the
plausibility check could not see the damage and the ingredient vanished silently.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from app import vision
from app.chem import is_formula_like, is_plausible, looks_like_name
from app.models import Blob, Err, Failure, Ok, Product, Result
from app.parse import parse

MIN_PAGE_TEXT = 20
FALLBACK = "tesseract"
FREE, PAID = 0, 2
_CLEAN_NAME = re.compile(r"[A-Za-z][A-Za-z\s\-',.()]*")


class Extractor(Protocol):
    name: str
    cost: int
    lossy: bool
    auto: bool

    def handles(self, blob: Blob) -> bool: ...

    def available(self) -> bool: ...

    def product(self, blob: Blob) -> Result[Product]: ...


EXTRACTORS: dict[str, Extractor] = {}


def register(cls):
    EXTRACTORS[cls.name] = cls()
    return cls


class TextExtractor:
    name: str
    cost = FREE
    lossy = False
    auto = True

    def handles(self, blob: Blob) -> bool: ...

    def available(self) -> bool:
        return True

    def read(self, blob: Blob) -> Result[str]: ...

    def product(self, blob: Blob) -> Result[Product]:
        text = self.read(blob)
        if isinstance(text, Err):
            return text
        parsed = parse(text.value, self.name)
        if not parsed.ingredients:
            return Err(Failure.NO_CONTENT, f"no ingredients found in {blob.filename}")
        return Ok(parsed)


class OcrExtractor(TextExtractor):
    lossy = True
    suffixes = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"})

    def handles(self, blob: Blob) -> bool:
        return blob.suffix in self.suffixes or blob.mime.startswith("image/")


@register
class PlainText(TextExtractor):
    name = "text"

    def handles(self, blob: Blob) -> bool:
        return blob.suffix in {".txt", ".csv", ".md"} or blob.mime.startswith("text/")

    def read(self, blob: Blob) -> Result[str]:
        return Ok(blob.data.decode("utf-8", errors="replace"))


@register
class PdfText(TextExtractor):
    name = "pdf"

    def handles(self, blob: Blob) -> bool:
        return blob.suffix == ".pdf" or blob.mime == "application/pdf"

    def read(self, blob: Blob) -> Result[str]:
        from pypdf import PdfReader

        try:
            pages = PdfReader(io.BytesIO(blob.data)).pages
        except Exception as error:
            return Err(Failure.UNREADABLE, f"{blob.filename}: {error}")

        rendered = []
        for number, page in enumerate(pages):
            text = (page.extract_text() or "").strip()
            rendered.append(text if len(text) >= MIN_PAGE_TEXT else _ocr_pdf_page(blob, number))
        return Ok("\n".join(rendered))


@register
class Tesseract(OcrExtractor):
    name = FALLBACK
    cost = FREE
    config = ""

    def available(self) -> bool:
        import shutil

        return shutil.which("tesseract") is not None

    def read(self, blob: Blob) -> Result[str]:
        import pytesseract
        from PIL import Image, ImageOps

        try:
            with Image.open(io.BytesIO(blob.data)) as image:
                prepared = ImageOps.exif_transpose(image).convert("RGB")
                return Ok(pytesseract.image_to_string(prepared, config=self.config))
        except pytesseract.TesseractNotFoundError:
            return Err(Failure.UNSUPPORTED, "tesseract not installed (brew install tesseract)")
        except Exception as error:
            return Err(Failure.UNREADABLE, f"{blob.filename}: {error}")


@register
class GeminiVision:
    name = "gemini"
    cost = PAID
    lossy = False
    auto = True

    def handles(self, blob: Blob) -> bool:
        return self.available()

    def available(self) -> bool:
        import importlib.util

        return vision.configured() and importlib.util.find_spec("google.genai") is not None

    def product(self, blob: Blob) -> Result[Product]:
        payload = vision.read(blob)
        if isinstance(payload, Err):
            return payload
        return Ok(Product(payload.value["name"], tuple(payload.value["ingredients"]), self.name))


def _ocr_pdf_page(blob: Blob, number: int) -> str:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(blob.data)
    try:
        buffer = io.BytesIO()
        document[number].render(scale=200 / 72).to_pil().save(buffer, format="PNG")
    finally:
        document.close()
    page = EXTRACTORS[FALLBACK].read(Blob(f"{blob.filename}#{number}.png", buffer.getvalue()))
    return page.value if isinstance(page, Ok) else ""


def chain(blob: Blob, prefer: str | None = None, only: str | None = None) -> list[Extractor]:
    if only:
        picked = EXTRACTORS[only]
        return [picked] if picked.handles(blob) else []
    prefer = prefer or os.getenv("OCR_ENGINE") or FALLBACK
    usable = [e for e in EXTRACTORS.values() if e.handles(blob) and (e.auto or e.name == prefer)]
    usable.sort(key=lambda e: (e.name != prefer, e.name == FALLBACK, e.cost, e.name))
    return usable


def suspect_tokens(ingredients: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        i for i in ingredients
        if (is_plausible(i) is False and is_formula_like(i)) or
           (not is_formula_like(i) and not looks_like_name(i))
    )


def extract(blob: Blob, prefer: str | None = None, only: str | None = None) -> Result[Product]:
    if not blob.data.strip():
        return Err(Failure.UNREADABLE, f"{blob.filename} is empty")

    failure: Err | None = None
    degraded: Product | None = None

    for extractor in chain(blob, prefer, only):
        outcome = extractor.product(blob)
        if isinstance(outcome, Err):
            # Keep the first failure, not the last. The chain is ordered by how well each
            # engine suits the file, so the first one to fail explains the file best. The
            # last is whichever long shot ran out of ideas, and naming it misleads: a plain
            # text file would report "vision returned no ingredients".
            failure = failure or outcome
            continue
        suspect = suspect_tokens(outcome.value.ingredients) if extractor.lossy else ()
        if not suspect:
            return outcome
        if degraded is None or len(suspect) < len(degraded.suspect):
            degraded = replace(outcome.value, suspect=suspect)

    if degraded is not None:
        return Ok(degraded)
    return failure or Err(Failure.UNSUPPORTED, f"no extractor handles {blob.suffix or blob.mime}")


def _check(prefer: str | None, only: str | None = None) -> int:
    import csv

    root = Path(__file__).parent.parent
    with open(root / "product_index.csv") as handle:
        rows = list(csv.DictReader(handle))

    failures: list[str] = []
    results = []
    print(f"\n  {'product':14}{'via':11}{'n':>4}   ingredients")
    for row in rows:
        outcome = extract(Blob.from_path(root / row["filename"]), prefer, only)
        results.append((row["product_id"], Path(row["filename"]).suffix.lstrip("."), outcome))
        if isinstance(outcome, Err):
            print(f"  {row['product_id']:14}{'--':11}{'--':>4}   {outcome}")
            failures.append(f"{row['product_id']}: {outcome}")
            continue
        found = outcome.value.ingredients
        preview = ", ".join(found[:4]) + ("..." if len(found) > 4 else "")
        print(f"  {row['product_id']:14}{outcome.value.source:11}{len(found):>4}   {preview[:56]}")
        if len(found) < 5:
            failures.append(f"{row['product_id']}: only {len(found)} ingredients")

    for product_id, kind, outcome in results:
        if kind != "png" or isinstance(outcome, Err):
            continue
        for item in outcome.value.ingredients:
            if not is_formula_like(item) and not _CLEAN_NAME.fullmatch(item):
                failures.append(f"{product_id}: garbled name {item!r}")

    served = Counter(o.value.source for _, _, o in results if isinstance(o, Ok))
    unresolved = [f"{p}: {', '.join(o.value.suspect)}"
                  for p, _, o in results if isinstance(o, Ok) and o.value.suspect]

    registry = ", ".join(
        f"{n}{'*' if n == FALLBACK else ''}{'' if e.available() else ' (UNAVAILABLE)'}"
        for n, e in sorted(EXTRACTORS.items(), key=lambda kv: (kv[1].cost, kv[0]))
    )
    print(f"\n  engines (by cost)    {registry}   * = fallback")
    print(f"  all extracted        {sum(isinstance(o, Ok) for _, _, o in results)}/{len(results)}")
    print(f"  served by            {dict(served)}")
    print(f"  formulas unresolved  {len(unresolved)} products")
    for item in unresolved:
        print(f"      {item}")
    if failures:
        print("\n  FAILURES")
        for item in failures:
            print(f"    {item}")
        return 1
    print("\n  ok\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?")
    parser.add_argument("--engine", choices=sorted(EXTRACTORS))
    parser.add_argument("--only", choices=sorted(EXTRACTORS))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check or not args.path:
        return _check(args.engine, args.only)

    outcome = extract(Blob.from_path(args.path), args.engine, args.only)
    if isinstance(outcome, Err):
        print(f"  {outcome}")
        return 1
    product = outcome.value
    print(f"\n  {product.name or '(no name)'}  via {product.source}")
    for item in product.ingredients:
        print(f"    {item}")
    if product.suspect:
        print(f"  unreadable: {', '.join(product.suspect)}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
