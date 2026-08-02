"""Gemini vision extractor.

Inert without GEMINI_API_KEY. The prompt insists on verbatim surface forms so the matching
layer sees what was printed, not the model's tidied version of it.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel, Field, ValidationError

from app.models import Blob, Err, Failure, Ok, Result

MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
SEED = int(os.getenv("GEMINI_SEED", "7"))


class Label(BaseModel):
    """The shape the model must return. Doubles as the schema and the validator.

    Gemini constrains decoding to this, so malformed JSON cannot come back, but a schema only
    guarantees shape. Parsing through the same model on the way in is what turns a surprise
    into an Err rather than something that reads as an empty ingredient list.
    """

    product_name: str | None = Field(
        default=None, description="Product name exactly as printed, or null if absent."
    )
    ingredients: list[str] = Field(
        default_factory=list,
        description="Every ingredient, verbatim. Empty if this is not a product label.",
    )

PROMPT = """You are reading a product label.

Extract the product name and every ingredient listed on it.

Preserve each ingredient's surface form VERBATIM. Do not rewrite 'C6H6' to
'Benzene' or 'Benzene' to 'C6H6'. Do not correct, expand or normalise anything.

Never substitute a word you cannot read with a different plausible word. If
characters are illegible, output what you can see and use '?' for the rest,
e.g. 'B?nz?ne'. A visible gap is far better than a confident guess.

Ingredients the label says it does NOT contain - under headings like
'free from', 'formulated without', 'contains no' - are not ingredients.
Exclude them.

If this is not a product label, or it carries no ingredient list, return an
empty ingredients array. Random characters, keyboard mashing and filler text
are not ingredients. Returning nothing is the correct answer for such input,
and is far better than treating meaningless words as ingredient names.

Return only JSON: {"product_name": string|null, "ingredients": [string]}"""


@lru_cache(maxsize=1)
def _client():
    from google import genai

    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def read(blob: Blob) -> Result[dict]:
    if not configured():
        return Err(Failure.UNSUPPORTED, "GEMINI_API_KEY not set")

    try:
        from google.genai import types
    except ImportError:
        return Err(Failure.UNSUPPORTED, "google-genai not installed")

    part = (
        types.Part.from_bytes(data=blob.data, mime_type=blob.mime)
        if blob.mime.startswith(("image/", "application/pdf"))
        else types.Part.from_text(text=blob.data.decode("utf-8", errors="replace"))
    )

    try:
        response = _client().models.generate_content(
            model=MODEL,
            contents=[PROMPT, part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Label,
                temperature=0,
                seed=SEED,
                max_output_tokens=4096,
            ),
        )
    except Exception as error:
        return Err(Failure.UNREADABLE, f"vision call failed: {error}")

    try:
        label = Label.model_validate_json(response.text or "")
    except ValidationError as error:
        return Err(Failure.UNREADABLE, f"vision returned an unusable payload: {error}")

    ingredients = [i.strip() for i in label.ingredients if i.strip()]
    if not ingredients:
        return Err(Failure.NO_CONTENT, "vision returned no ingredients")
    return Ok({"name": label.product_name, "ingredients": ingredients})
