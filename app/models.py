"""Value types shared across the pipeline.

`Ok` and `Err` are separate types so a caller cannot mistake "checked, found nothing" for
"could not answer". That distinction is what stops an unreadable file becoming an Accept.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Generic, TypeVar

T = TypeVar("T")


class Failure(str, Enum):
    UNSUPPORTED = "unsupported"
    UNREADABLE = "unreadable"
    NO_CONTENT = "no_content"


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True, slots=True)
class Err:
    failure: Failure
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.failure.value}: {self.detail}" if self.detail else self.failure.value


Result = Ok[T] | Err


@dataclass(frozen=True)
class Blob:
    filename: str
    data: bytes

    @classmethod
    def from_path(cls, path: str | Path) -> Blob:
        p = Path(path)
        return cls(p.name, p.read_bytes())

    @property
    def suffix(self) -> str:
        return Path(self.filename).suffix.lower()

    @property
    def mime(self) -> str:
        return mimetypes.guess_type(self.filename)[0] or "application/octet-stream"


@dataclass(frozen=True)
class Product:
    name: str | None
    ingredients: tuple[str, ...]
    source: str
    suspect: tuple[str, ...] = ()


class Status(str, Enum):
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"
    NEEDS_REVIEW = "Needs Review"


@dataclass(frozen=True)
class Evidence:
    ingredient: str
    entry: str
    drawer: str
    tier: str

    def render(self) -> str:
        if self.drawer == "literal":
            via = ("exact match" if self.tier == "certain"
                   else f"listed as {self.ingredient!r}, a known synonym")
        else:
            via = {
                "hill": "molecular formula matches",
                "ocr_variant": f"OCR read it as {self.ingredient!r}",
                "inchikey": "identical structure",
                "inchikey_parent": "same compound, different salt",
                "pubchem_cid": "same PubChem compound",
                "llm": f"identified as {self.ingredient!r}",
            }.get(self.drawer, self.drawer)
        return f"Contains {self.entry} ({via})"


@dataclass(frozen=True)
class Verdict:
    product_name: str | None
    status: Status
    reason: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    unread: tuple[str, ...] = ()
    index_version: str = ""
    ingredients: tuple[str, ...] = ()
    extraction_source: str | None = None

    @property
    def entries(self) -> frozenset[str]:
        return frozenset(e.entry for e in self.evidence)
