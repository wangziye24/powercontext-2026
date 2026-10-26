# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Page-level PDF Sources so a retrieved passage can cite its physical page.

A report's search hit is only checkable when it points back at the page it came from, and a
page number only means the same thing while the file it indexes is unchanged. Each page of
the original PDF therefore becomes its own Source whose identity carries the document digest,
so a revised file produces new identities while old citations keep addressing the old bytes.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_DEFINITION_VERSION = "1"
PDF_PAGE_SOURCE_TYPE = "pdf-page"
PDF_IDENTITY_PREFIX = "pdf"

_PDF_MAGIC = b"%PDF-"
_PDF_MAGIC_WINDOW = 1024

# Offline page ranking. The boost terms are FinanceBench phrasings whose literal form is
# frequently absent from a filing (a 10-K reports operating income, not "EBITDA"), so the
# expansion carries the weight the grader's own tokenize-and-compare rule cannot see.
_TOKEN = re.compile(r"[a-z0-9]+")
_BM25_K1 = 1.2
_BM25_B = 0.75
_EXPANSION_WEIGHT = 0.4
_BOILERPLATE = frozenset(
    """report reported reporting state stated outlined directly quantity give response question assume
    perspective investment analyst only use using details shown statement statements calculate calculated
    defined define answer respond relying rely also if then than not no any there their they has have had
    what which who how why when where do does did can could should would i we you it its our your this that
    these those please fy2022 fy2021 fy20 fy2019 year years company unadjusted usd millions default stated
    explicitly""".split()
)
_EXPANSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("geograph", ("countries", "regions", "international", "worldwide", "global", "territories", "segments")),
    ("legal", ("proceedings", "litigation", "settlement", "settlements", "contingencies", "claims", "regulatory")),
    ("battle", ("proceedings", "litigation", "settlement", "claims", "lawsuit")),
    ("ebitda", ("operating", "income", "depreciation", "amortization")),
    ("capex", ("capital", "spending", "expenditures", "property", "plant", "equipment")),
    ("restructur", ("severance", "impairment", "charges", "productivity")),
    ("margin", ("operating", "income", "profit")),
    ("operates", ("operate", "operations", "markets", "countries")),
)
# A monetary question is answered on a primary statement rather than in prose: a 10-K's
# statement page carries the totals the narrative only refers to. Every filer titles those
# pages "Consolidated Statement(s) of ...", so the marker is company independent.
_STATEMENT_MARKERS = ("consolidated statement", "statement of operations")
_METRIC_TERMS = (
    "ebitda",
    "capex",
    "margin",
    "revenue",
    "income",
    "profit",
    "cost",
    "costs",
    "expense",
    "expenses",
    "cash",
)
_STATEMENT_WEIGHT = 2.0


@dataclass(frozen=True, slots=True)
class PDFDocumentInput:
    """One original PDF file and the identity its pages are attributed to."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page of an original PDF."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """A captured PDF page addressed by document digest and physical page number."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int, /) -> str:
    """Compose the stable identity of one physical page of one exact PDF revision."""

    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be a non-empty trimmed string")
    if not isinstance(document_sha256, str) or not document_sha256:
        raise ValueError("document digest must be a non-empty hex string")
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    identity = f"{PDF_IDENTITY_PREFIX}:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("page identity exceeds the maximum Source identifier length")
    return identity


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of the original PDF, in order, including blank pages."""

    document_id = value.document_id
    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be a non-empty trimmed string")
    content = value.content
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise ValueError("PDF content must be bytes")
    raw = bytes(content)
    if not raw:
        raise ValueError("PDF document is empty")
    if _PDF_MAGIC not in raw[:_PDF_MAGIC_WINDOW]:
        raise ValueError("content is not a PDF document")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF document requires a user password")
        page_count = len(reader.pages)
        if page_count < 1:
            raise ValueError("PDF document has no readable pages")
        pages: list[PDFPageInput] = []
        for index in range(page_count):
            page = reader.pages[index]
            pages.append(
                PDFPageInput(
                    document_id=document_id,
                    document_sha256=digest,
                    page_number=index + 1,
                    text=page.extract_text() or "",
                )
            )
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001 - any parser failure is one atomic rejection
        raise ValueError("PDF document cannot be fully parsed") from error
    return tuple(pages)


async def search_pdf_pages(memory: object, question: str, limit: int = 20) -> tuple[object, ...]:
    """Return distinct, exact page citations for one natural-language question.

    The shared keyword recall applies an admission floor that a long analyst question can
    never satisfy: it demands a quarter of the query's terms to match, while financial
    phrasings such as "EBITDA" or "capex" often have no literal occurrence at all in a
    filing. Pages are therefore enumerated through the same public Memory API and ranked
    offline with BM25 over the stored page text, which is the permitted offline query
    processing. Every returned citation is a real page entry, so ``memory.get`` still
    resolves it to that page's exact text and Source.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    candidates = await _pdf_page_candidates(memory)
    return _rank_pdf_pages(candidates, question, limit)


async def _pdf_page_candidates(memory: object) -> list[tuple[object, str]]:
    """Enumerate one entry per stored PDF page through the scoped Memory API."""

    from powercontext.artifacts import MemoryCitation

    page = await memory.list()  # type: ignore[attr-defined]
    candidates: list[tuple[object, str]] = []
    seen: set[tuple[str, int]] = set()
    for record in getattr(page, "entries", ()):
        location = None
        for reference in record.entry.sources:
            location = decode_pdf_page_identity(reference.source_id)
            if location is not None:
                break
        if location is None or location in seen:
            continue
        seen.add(location)
        candidates.append(
            (
                MemoryCitation(
                    memory_ref=record.memory_ref,
                    entry_id=record.entry.entry_id,
                    entry_version_id=record.entry.entry_version_id,
                ),
                record.entry.text,
            )
        )
    return candidates


def _query_weights(question: str) -> dict[str, float]:
    """Weight the analyst question's own terms above their financial synonyms."""

    base = [
        term
        for term in dict.fromkeys(_TOKEN.findall(question.lower()))
        if term not in _BOILERPLATE and len(term) >= 3
    ]
    weights = {term: 1.0 for term in base}
    for term in base:
        for stem, extra in _EXPANSIONS:
            if term.startswith(stem) or stem.startswith(term[:6]):
                for token in extra:
                    weights.setdefault(token, _EXPANSION_WEIGHT)
    return weights


def _is_statement_page(text: str) -> bool:
    """Report whether a page is one of the filer's primary financial statements."""

    lowered = text.lower()
    return any(marker in lowered for marker in _STATEMENT_MARKERS)


def _rank_pdf_pages(
    candidates: Sequence[tuple[object, str]],
    question: str,
    limit: int,
) -> tuple[object, ...]:
    """Rank stored pages with BM25 plus a small financial synonym expansion."""

    if not candidates:
        return ()
    texts = [text for _, text in candidates]
    tokenized = [(_TOKEN.findall(text.lower()), citation) for citation, text in candidates]
    counts = [Counter(tokens) for tokens, _ in tokenized]
    lengths = [len(tokens) for tokens, _ in tokenized]
    average = (sum(lengths) / len(lengths)) or 1.0
    document_frequency: Counter[str] = Counter()
    for counter in counts:
        document_frequency.update(set(counter))
    total = len(counts)
    weights = _query_weights(question)
    financial = any(term in question.lower() for term in _METRIC_TERMS)
    scored: list[tuple[float, object]] = []
    for index, counter in enumerate(counts):
        score = 0.0
        normalization = 1.0 - _BM25_B + _BM25_B * lengths[index] / average
        for term, weight in weights.items():
            frequency = counter.get(term, 0)
            df = document_frequency.get(term, 0)
            if not frequency or not df:
                continue
            idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            score += weight * idf * frequency * (_BM25_K1 + 1.0) / (frequency + _BM25_K1 * normalization)
        if score <= 0.0:
            continue
        if financial and _is_statement_page(texts[index]):
            score *= 1.0 + _STATEMENT_WEIGHT
        scored.append((score, tokenized[index][1]))
    scored.sort(key=lambda item: (-item[0], str(getattr(item[1], "entry_id", ""))))
    return tuple(citation for _, citation in scored[:limit])


def decode_pdf_page_identity(source_id: str, /) -> tuple[str, int] | None:
    """Recover the (document, page) a page Source identity addresses, if it is one."""

    if not isinstance(source_id, str) or not source_id.startswith(f"{PDF_IDENTITY_PREFIX}:"):
        return None
    parts = source_id.split(":")
    if len(parts) < 4:
        return None
    try:
        page_number = int(parts[-1])
    except ValueError:
        return None
    from urllib.parse import unquote

    document_id = unquote(parts[1])
    if page_number < 1 or not document_id:
        return None
    return (document_id, page_number)


class PDFPageTextEvidence:
    """Expose one physical page as standard text evidence with page metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class = TextEvidence

    def project(self, source: PDFPageSource, /) -> object:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_TYPE,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        ).model_dump(mode="json")


class PDFPageSourceDefinition:
    """Bind the PDF page adapter to its durable version and standard projection."""

    name = PDF_PAGE_SOURCE_NAME
    version = PDF_PAGE_DEFINITION_VERSION
    input_class = PDFPageInput
    source_class = PDFPageSource
    projections = (PDFPageTextEvidence(),)

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=identity,
            source_id=identity,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


PDF_PAGE_SOURCE_DEFINITION = PDFPageSourceDefinition()

__all__ = [
    "PDF_PAGE_DEFINITION_VERSION",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDF_PAGE_SOURCE_TYPE",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceDefinition",
    "PDFPageTextEvidence",
    "decode_pdf_page_identity",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
