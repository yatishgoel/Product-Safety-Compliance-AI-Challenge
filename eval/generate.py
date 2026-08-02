"""Render eval/corpus/manifest.yaml into real product files.

The manifest is the source of truth; these binaries are disposable and gitignored. Files are
generated to match the byte signature of the provided samples, measured from them directly:

    PDF   ReportLab, US Letter 612x792pt, Helvetica / Helvetica-Bold, unembedded Type 1
    PNG   1000x1400 RGB, 8-bit, non-interlaced, no pHYs chunk (i.e. no DPI metadata)
    TXT   ASCII, LF line endings, "Product ID / Product Name / SKU / Description" skeleton

Tier-3 cases are rendered *correctly* and then degraded, so ground truth stays exact while
extraction becomes genuinely hard. That is the only honest way to test OCR robustness: if
you hand-write the corruption into the manifest you are testing your own typo, not the
pipeline.

Usage:  uv run python eval/generate.py [--tier t3_ocr] [--clean]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import random
import shutil
import sys
from pathlib import Path

from eval.harness import case_path, load_cases
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as pdfcanvas

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "eval" / "corpus"
OUT = CORPUS / "generated"

PNG_SIZE = (1000, 1400)
DESCRIPTION = "A {name} formulated with selected active compounds for everyday use."

_FONT_CANDIDATES = {
    "regular": [
        "/System/Library/Fonts/Supplemental/DejaVuSans.ttf",
        "/Library/Fonts/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
    "bold": [
        "/System/Library/Fonts/Supplemental/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ],
    "Arial Narrow": [
        "/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ],
}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES.get(kind, _FONT_CANDIDATES["regular"]):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def sku(case_id: str) -> str:
    """Deterministic 8-char SKU, so regenerating the corpus produces identical files."""
    h = hashlib.sha1(case_id.encode()).hexdigest().upper()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(alphabet[int(h[i : i + 2], 16) % 36] for i in range(0, 16, 2))


def body_text(case: dict) -> str:
    """The document content, in the same skeleton as the provided samples."""
    if "raw_body" in case:
        return case["raw_body"]

    name = case.get("product_name", "Test Product")
    lines = [
        f"Product ID: {case['id']}",
        f"Product Name: {name}",
        f"SKU: {sku(case['id'])}",
        "",
        "Description:",
        DESCRIPTION.format(name=name.lower()),
        "",
        "Ingredients / Chemical Formulas:",
    ]
    # `or []` rather than a default: YAML renders an empty sequence as None, and hand-edited
    # manifests will do this too.
    lines += [f"- {i}" for i in (case.get("ingredients") or [])]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------- text
def render_txt(case: dict, path: Path) -> None:
    path.write_text(body_text(case), encoding="utf-8", newline="\n")


# ------------------------------------------------------------------------ pdf
def render_pdf(case: dict, path: Path) -> None:
    render = case.get("render") or {}
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=letter)
    width, height = letter

    text = body_text(case)
    head, _, rest = text.partition("Ingredients")
    ingredient_lines = ["Ingredients" + rest] if rest else []

    y = height - 72
    c.setFont("Helvetica-Bold", 14)
    for line in head.splitlines():
        if not line.strip():
            y -= 10
            continue
        c.setFont("Helvetica-Bold" if line.startswith("Product ID") else "Helvetica",
                  14 if line.startswith("Product ID") else 10)
        c.drawString(72, y, line)
        y -= 16

    if render.get("page_break_before_ingredients"):
        c.showPage()
        y = height - 72

    lines = "\n".join(ingredient_lines).splitlines()
    if render.get("columns", 1) == 2:
        half = (len(lines) + 1) // 2
        for col, chunk in enumerate((lines[:half], lines[half:])):
            yy = y
            for line in chunk:
                c.setFont("Helvetica", 10)
                c.drawString(72 + col * (width - 144) / 2, yy, line)
                yy -= 16
    else:
        for line in lines:
            c.setFont("Helvetica", 10)
            c.drawString(72, y, line)
            y -= 16

    c.showPage()
    c.save()
    data = buf.getvalue()

    if render.get("truncate"):
        # A file that starts like a PDF and stops mid-stream: the classic partial upload.
        data = data[: int(len(data) * 0.55)]

    path.write_bytes(data)


# ------------------------------------------------------------------------ png
def _draw_page(case: dict, font_kind: str) -> Image.Image:
    img = Image.new("RGB", PNG_SIZE, "white")
    d = ImageDraw.Draw(img)
    text = body_text(case)
    lines = text.splitlines()

    y = 40
    for i, line in enumerate(lines):
        if not line.strip():
            y += 18
            continue
        if i == 0:
            f = _font("bold", 34)
        else:
            f = _font(font_kind, 26)
        d.text((38, y), line, fill=(0, 0, 0), font=f)
        y += 38 if i == 0 else 28
    return img


def render_png(case: dict, path: Path) -> None:
    render = case.get("render") or {}

    if render.get("blank"):
        Image.new("RGB", PNG_SIZE, "white").save(path, "PNG")
        return

    img = _draw_page(case, render.get("font", "regular"))

    # Low capture resolution: downsample then restore size, exactly as a phone photo of a
    # label at a distance would. This is what turns the O in H2O2 into a 0.
    if (dpi := render.get("dpi")):
        scale = max(0.25, min(1.0, dpi / 144))
        small = img.resize((int(PNG_SIZE[0] * scale), int(PNG_SIZE[1] * scale)), Image.LANCZOS)
        img = small.resize(PNG_SIZE, Image.LANCZOS)

    if (deg := render.get("rotate")):
        img = img.rotate(deg, resample=Image.BICUBIC, fillcolor=(255, 255, 255))

    if (q := render.get("jpeg_quality")):
        tmp = io.BytesIO()
        img.save(tmp, "JPEG", quality=q)
        tmp.seek(0)
        img = Image.open(tmp).convert("RGB")

    if (amount := render.get("noise")):
        rng = random.Random(case["id"])  # deterministic per case
        px = img.load()
        w, h = img.size
        for _ in range(int(w * h * amount)):
            x, yy = rng.randrange(w), rng.randrange(h)
            v = rng.choice((0, 255))
            px[x, yy] = (v, v, v)

    # No `dpi=` argument: the provided PNGs carry no pHYs chunk, and neither do ours.
    img.save(path, "PNG")


def render_pdf_scanned(case: dict, path: Path) -> None:
    """An image-only PDF: no text layer at all, so the text extractor must decline."""
    render = case.get("render") or {}
    img = _draw_page(case, render.get("font", "regular"))
    if (dpi := render.get("dpi")):
        scale = max(0.25, min(1.0, dpi / 144))
        small = img.resize((int(PNG_SIZE[0] * scale), int(PNG_SIZE[1] * scale)), Image.LANCZOS)
        img = small.resize(PNG_SIZE, Image.LANCZOS)

    tmp = OUT / f".{case['id']}.scan.png"
    img.save(tmp, "PNG")
    c = pdfcanvas.Canvas(str(path), pagesize=letter)
    c.drawImage(str(tmp), 0, 0, width=letter[0], height=letter[1])
    c.showPage()
    c.save()
    tmp.unlink(missing_ok=True)


RENDERERS = {
    "txt": (render_txt, ".txt"),
    "pdf": (render_pdf, ".pdf"),
    "png": (render_png, ".png"),
    "pdf_scanned": (render_pdf_scanned, ".pdf"),
}


def generate(cases: list[dict], only_tier: str | None = None) -> int:
    made = 0
    for case in cases:
        if only_tier and case["tier"] != only_tier:
            continue
        if "source" in case:  # provided sample, nothing to render
            continue
        path = case_path(case)
        path.parent.mkdir(parents=True, exist_ok=True)
        renderer, _ = RENDERERS[case["format"]]
        renderer(case, path)
        made += 1
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", help="only regenerate one tier")
    ap.add_argument("--clean", action="store_true", help="delete generated files first")
    args = ap.parse_args()

    if args.clean and OUT.exists():
        shutil.rmtree(OUT)

    cases = load_cases()
    OUT.mkdir(parents=True, exist_ok=True)
    made = generate(cases, args.tier)

    missing = [c["id"] for c in cases if not case_path(c).exists()]
    print(f"generated {made} files ({len(cases)} cases total)")
    if missing:
        print(f"MISSING: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
