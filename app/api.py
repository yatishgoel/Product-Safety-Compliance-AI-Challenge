"""REST API over the evaluation pipeline.

Needs Review returns 202 rather than 200, so a caller cannot mistake an escalation for a
clean pass.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import trace, vision
from app.evaluate import SUFFIXES, evaluate
from app.extract import EXTRACTORS
from app.index import Index, build_cached
from app.models import Blob, Status, Verdict

MAX_BYTES = 20 * 1024 * 1024
ROOT = Path(__file__).parent.parent.resolve()
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    trace.setup()
    build_cached()
    yield
    trace.flush()


api = FastAPI(
    title="Product Safety Compliance",
    version="1.0",
    summary="Decide whether a product may ship, against a configurable forbidden list.",
    lifespan=lifespan,
)
trace.instrument_fastapi(api)


def as_json(verdict: Verdict) -> dict:
    return {
        "product_name": verdict.product_name,
        "status": verdict.status.value,
        "reason": list(verdict.reason),
        "evidence": [
            {
                "ingredient": e.ingredient,
                "forbidden_entry": e.entry,
                "matched_by": e.drawer,
                "confidence": e.tier,
            }
            for e in verdict.evidence
        ],
        "unread": list(verdict.unread),
        "forbidden_list_version": verdict.index_version,
        "ingredients": list(verdict.ingredients),
        "extraction_source": verdict.extraction_source,
    }


async def _read(upload: UploadFile) -> bytes:
    data = await upload.read()
    if not data:
        raise HTTPException(400, f"{upload.filename or 'file'} is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"{upload.filename} exceeds {MAX_BYTES // 1024 // 1024} MB")
    return data


def _from_path(raw: str) -> Blob:
    target = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if ROOT not in target.parents:
        raise HTTPException(400, "path must be inside the project directory")
    if target.suffix.lower() not in SUFFIXES:
        raise HTTPException(400, f"unsupported file type: {target.suffix or 'none'}")
    if not target.is_file():
        raise HTTPException(404, f"no such file: {raw}")
    return Blob(target.name, target.read_bytes())


async def _index(forbidden_list: UploadFile | None):
    if forbidden_list is None:
        return build_cached()
    return build_cached(await _read(forbidden_list))


def policy_json(index: Index) -> dict:
    """Return policy entries with enough coverage metadata for the review workspace."""
    drawer_counts = {
        entry: {
            "name_keys": 0,
            "formula_keys": 0,
            "structure_keys": 0,
        }
        for entry in index.entries
    }
    for drawer, values in index.drawers.items():
        bucket = (
            "name_keys"
            if drawer in {"literal", "ocr_variant"}
            else "formula_keys"
            if drawer == "hill"
            else "structure_keys"
        )
        for entry, _tier in values.values():
            if entry in drawer_counts:
                drawer_counts[entry][bucket] += 1

    total_keys = sum(len(values) for values in index.drawers.values())
    degraded = set(index.degraded)
    return {
        "version": index.version,
        "summary": {
            "entries": len(index),
            "resolved": len(index) - len(index.degraded),
            "unresolved": len(index.degraded),
            "match_keys": total_keys,
        },
        "entries": [
            {
                "name": entry,
                "status": "unresolved" if entry in degraded else "resolved",
                **drawer_counts[entry],
            }
            for entry in index.entries
        ],
    }


@api.get("/health")
async def health() -> dict:
    index = build_cached()
    return {
        "status": "ok",
        "forbidden_entries": len(index),
        "forbidden_list_version": index.version,
        "unresolved_entries": list(index.degraded),
    }


@api.get("/policy")
async def policy() -> dict:
    """Describe the default forbidden-ingredient policy used by the evaluator."""
    return policy_json(build_cached())


@api.post("/inspect_policy")
async def inspect_policy(forbidden_list: UploadFile = File(...)) -> dict:
    """Compile an uploaded policy and return its authoritative parsed entries."""
    return policy_json(build_cached(await _read(forbidden_list)))


CATALOG: tuple[dict, ...] = (
    {
        "id": "auto",
        "name": "Automatic",
        "description": "Picks the cheapest engine that can read this file.",
        "supports": ["text", "pdf", "image"],
        "hint": "",
    },
    {
        "id": "native",
        "name": "Built-in parser",
        "description": "Reads text files and PDF text layers directly. No OCR.",
        "supports": ["text", "pdf"],
        "hint": "",
    },
    {
        "id": "tesseract",
        "name": "Tesseract OCR",
        "description": "Local OCR for photographed or scanned labels.",
        "supports": ["image"],
        "hint": "Install it with: brew install tesseract",
    },
    {
        "id": "gemini",
        "name": "Gemini Vision",
        "description": f"Reads the label with {vision.MODEL}. Best on low-quality photos.",
        "supports": ["image", "pdf"],
        "hint": "Set GEMINI_API_KEY, then restart the server.",
    },
)


def _available(entry_id: str) -> bool:
    extractor = EXTRACTORS.get(entry_id)
    return True if extractor is None else extractor.available()


@api.get("/extractors")
async def extractors() -> dict:
    """List extraction choices and their runtime availability for the UI.

    `hint` says how to turn an engine on. Without it an unavailable engine reads as broken
    rather than as one setup step away, which is the difference between a dead control and
    an actionable one.
    """
    return {
        "extractors": [
            {**entry, "available": (ready := _available(entry["id"])),
             "hint": "" if ready else entry["hint"]}
            for entry in CATALOG
        ]
    }


def _checked(extractor: str) -> str:
    """Reject an unknown engine here, so a typo cannot silently fall back to Automatic."""
    if extractor not in {"auto", "native", *EXTRACTORS}:
        raise HTTPException(400, f"unknown extractor: {extractor}")
    return extractor


@api.post("/evaluate_product")
async def evaluate_product(
    file: UploadFile | None = File(None),
    path: str | None = Form(None),
    forbidden_list: UploadFile | None = File(None),
    extractor: str = Form("auto"),
) -> JSONResponse:
    if file is None and not path:
        raise HTTPException(400, "provide either an uploaded file or a path")
    _checked(extractor)
    index = await _index(forbidden_list)
    blob = _from_path(path) if file is None else Blob(file.filename or "upload", await _read(file))
    verdict = evaluate(blob, index, extractor=extractor)
    status = 200 if verdict.status is not Status.NEEDS_REVIEW else 202
    return JSONResponse(as_json(verdict), status_code=status)


@api.post("/evaluate_products")
async def evaluate_products(
    files: list[UploadFile] = File(...),
    forbidden_list: UploadFile | None = File(None),
    extractor: str = Form("auto"),
) -> dict:
    _checked(extractor)
    index = await _index(forbidden_list)
    results = []
    with trace.session(f"batch of {len(files)}"):
        for upload in files:
            blob = Blob(upload.filename or "upload", await _read(upload))
            verdict = evaluate(blob, index, extractor=extractor)
            results.append({"file": blob.filename, **as_json(verdict)})
    counts: dict[str, int] = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"summary": counts, "results": results}


if STATIC.is_dir():
    api.mount("/static", StaticFiles(directory=STATIC), name="static")

    @api.get("/", include_in_schema=False)
    async def home() -> FileResponse:
        return FileResponse(STATIC / "index.html")
