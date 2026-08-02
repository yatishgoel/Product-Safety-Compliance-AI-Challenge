"""Batched LLM judge, the last stage of the cascade.

One call per product covering every unmatched ingredient against the whole forbidden list.
Catches naming no database lists, such as E-numbers and other languages.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from pydantic import BaseModel, Field, ValidationError

from app.models import Err, Evidence, Failure, Ok, Result
from app.vision import MODEL, SEED, _client, configured


class Match(BaseModel):
    ingredient: str = Field(description="The ingredient, exactly as it appeared on the label.")
    forbidden: str = Field(description="The forbidden entry it denotes, copied verbatim.")
    reason: str | None = Field(default=None, description="Why they are the same compound.")


class Judgement(BaseModel):
    """Constrains the model to claims that can be checked, not prose it invents around them."""

    matches: list[Match] = Field(
        default_factory=list, description="Empty when nothing on the label is forbidden."
    )

PROMPT = """You are a chemical safety expert.

Below is a list of INGREDIENTS from a product label and a list of FORBIDDEN substances.
For each ingredient, decide whether it denotes the SAME COMPOUND as a forbidden entry.

THESE ARE MATCHES - the same compound under a different name:
  Benceno / Benzol /苯          are Benzene           (other languages)
  E216                          is Propylparaben      (E-number)
  128-37-0                      is BHT                (CAS number)
  Butylated Hydroxytoluene      is BHT                (spelled-out acronym)
  Methyl 4-hydroxybenzoate      is Methylparaben      (IUPAC name)
  Hydrogen peroxide             is H2O2               (name vs formula)

THESE ARE NOT MATCHES - different compounds that merely resemble each other:
  Sodium Laureth Sulfate  vs  Sodium Lauryl Sulfate   (different molecule)
  H2O                     vs  H2O2                    (different molecule)
  Ethylparaben            vs  Methylparaben           (different ester)
  Vanillin                vs  Methylparaben           (isomers, both C8H8O3)
  Fulvene                 vs  Benzene                 (isomers, both C6H6)

Sharing a molecular formula is not enough. Isomers arrange the same atoms differently and
are different substances. But a compound written in another language, as a CAS number, an
E-number, an acronym or a formula IS the same compound and IS a match.

If you cannot tell whether two names are the same compound, do not report a match.

Return only JSON: {"matches": [{"ingredient": string, "forbidden": string,
"reason": string}]}"""


def judge(residuals: Sequence[str], entries: Sequence[str]) -> Result[list[Evidence]]:
    if not residuals or not entries:
        return Ok([])
    if not configured():
        return Err(Failure.UNSUPPORTED, "GEMINI_API_KEY not set")

    try:
        from google.genai import types
    except ImportError:
        return Err(Failure.UNSUPPORTED, "google-genai not installed")

    question = (f"INGREDIENTS:\n{chr(10).join(residuals)}\n\n"
                f"FORBIDDEN:\n{chr(10).join(entries)}")

    try:
        response = _client().models.generate_content(
            model=os.getenv("JUDGE_MODEL", MODEL),
            contents=[PROMPT, question],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Judgement,
                temperature=0,
                seed=SEED,
                max_output_tokens=2048,
            ),
        )
    except Exception as error:
        return Err(Failure.UNREADABLE, f"judge call failed: {error}")

    try:
        judgement = Judgement.model_validate_json(response.text or "")
    except ValidationError as error:
        return Err(Failure.UNREADABLE, f"judge returned an unusable payload: {error}")

    # The schema cannot stop the model naming a substance that is not on the list, so every
    # claimed entry is looked up rather than trusted.
    known = {e.lower(): e for e in entries}
    seen: set[str] = set()
    found: list[Evidence] = []
    for row in judgement.matches:
        entry = known.get(row.forbidden.strip().lower())
        ingredient = row.ingredient.strip()
        if entry and ingredient and entry not in seen:
            seen.add(entry)
            found.append(Evidence(ingredient=ingredient, entry=entry,
                                  drawer="llm", tier="strong"))
    return Ok(found)
