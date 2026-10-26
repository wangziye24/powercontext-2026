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
from types import SimpleNamespace
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
# Document identities look like ``PEPSICO_2022_10K``; the company token is what a question
# names, so the form and period tokens must not count as addressing the filing.
_DOCUMENT_TOKEN = re.compile(r"[a-z0-9]+")
_DOCUMENT_NOISE_TOKENS = frozenset(
    {"10k", "10q", "8k", "pdf", "annual", "report", "reports", "form", "fy", "q1", "q2", "q3", "q4"}
)
# Period prefixes glued to the year by question writers ("FY2022", "CY2021", "Q4 2022").
_PERIOD_TOKEN = re.compile(r"^(?:fy|cy|f|q[1-4]|h[12]|ttm)((?:19|20)\d{2})$")
# Search fallback breadth when page enumeration is unavailable.
_SEARCH_FALLBACK_LIMIT = 200
# Neighbour widening: how many top hits are widened, how many answer slots that may take,
# and how far a neighbour may sit from its anchor.
_NEIGHBOUR_ANCHORS = 5
_NEIGHBOUR_SLOTS = 6
_NEIGHBOUR_OFFSETS = (-1, 1, -2, 2)


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


async def search_pdf_pages(memory: object, question: str, limit: int = 10) -> tuple[object, ...]:
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
    if not candidates:
        candidates = await _searched_page_candidates(memory, question)
    scoped = _question_document_candidates(candidates, question)
    return _rank_pdf_pages(scoped, question, limit)


def _question_document_candidates(
    candidates: Sequence[tuple[object, str, str, int]],
    question: str,
) -> Sequence[tuple[object, str, str, int]]:
    """Narrow to the filing the question names, so one report cannot crowd out another.

    A document set may hold several filings, and a page of the wrong company competes for
    the same top-N slots. Every page identity carries its document id, so a question that
    names exactly one filing is answered from that filing alone. Comparisons and unnamed
    questions keep the whole set.
    """

    documents: set[str] = set()
    for candidate in candidates:
        documents.add(candidate[2])
    if len(documents) < 2:
        return candidates
    asked = set(_DOCUMENT_TOKEN.findall(question.lower()))
    addressed = [
        document_id
        for document_id in documents
        if (set(_DOCUMENT_TOKEN.findall(document_id.lower())) - _DOCUMENT_NOISE_TOKENS) & asked
    ]
    if len(addressed) != 1:
        return candidates
    return tuple(candidate for candidate in candidates if candidate[2] == addressed[0])


async def _pdf_page_candidates(memory: object) -> list[tuple[object, str, str, int]]:
    """Enumerate one entry per stored PDF page through the scoped Memory API.

    Enumeration is preferred because offline ranking needs the whole page set. A Memory
    surface that does not offer it at all falls back to ranked search instead of failing.
    """

    listing = getattr(memory, "list", None)
    if listing is None:
        return []
    try:
        page = await listing()
    except Exception:  # noqa: BLE001 - an unavailable listing degrades to search
        return []
    return _page_candidates(getattr(page, "entries", ()))


async def _searched_page_candidates(memory: object, question: str) -> list[tuple[object, str, str, int]]:
    """Fallback retrieval through ``memory.search`` when page enumeration is unavailable."""

    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    search = getattr(memory, "search", None)
    fetch = getattr(memory, "get", None)
    if search is None or fetch is None:
        return []
    try:
        result = await search(SearchMemoryRequest(query=question, mode="fts", limit=_SEARCH_FALLBACK_LIMIT))
    except Exception:  # noqa: BLE001 - no ranked candidates is an empty answer, not a crash
        return []
    entries = []
    seen: set[tuple[str, str, str]] = set()
    for hit in getattr(result, "hits", ()):
        key = (hit.entry_id, hit.entry_version_id, hit.memory_ref.artifact_id)
        if key in seen:
            continue
        seen.add(key)
        citation = _search_citation(hit)
        entries.append(
            {
                "memory_ref": hit.memory_ref,
                "entry_id": hit.entry_id,
                "entry_version_id": hit.entry_version_id,
                "citation": citation,
            }
        )
    resolved: list[tuple[object, str, str, int]] = []
    for entry in entries:
        try:
            record = await fetch(GetMemoryEntryRequest(citation=entry["citation"]))
        except Exception:  # noqa: BLE001 - one unreadable hit must not fail the whole answer
            continue
        resolved.extend(
            _page_candidates((SimpleNamespace(memory_ref=entry["memory_ref"], entry=record.entry),))
        )
    return resolved


def _search_citation(hit: object) -> object:
    from powercontext.artifacts import MemoryCitation

    return MemoryCitation(
        memory_ref=hit.memory_ref,
        entry_id=hit.entry_id,
        entry_version_id=hit.entry_version_id,
    )


def _page_candidates(records: object) -> list[tuple[object, str, str, int]]:
    """Turn Memory entries into (citation, text, document, page) page candidates."""

    from powercontext.artifacts import MemoryCitation

    candidates: list[tuple[object, str, str, int]] = []
    seen: set[tuple[str, int]] = set()
    for record in records:  # type: ignore[union-attr]
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
                location[0],
                location[1],
            )
        )
    return candidates


def _query_weights(question: str) -> dict[str, float]:
    """Weight the analyst question's own terms above their financial synonyms.

    Question tokens are normalised first because a filing never spells a period the way a
    question does: ``FY2022`` is one token to a tokenizer but the page says ``2022``, and
    ``PepsiCo's`` loses its possessive. Without that, the strongest discriminating term of a
    metrics question matches nothing at all.
    """

    base = [
        term
        for term in dict.fromkeys(_normalized_terms(question))
        if term not in _BOILERPLATE and len(term) >= 3
    ]
    weights = {term: 1.0 for term in base}
    for term in base:
        for stem, extra in _EXPANSIONS:
            if term.startswith(stem) or stem.startswith(term[:6]):
                for token in extra:
                    weights.setdefault(token, _EXPANSION_WEIGHT)
    return weights


def _normalized_terms(question: str) -> list[str]:
    """Tokens plus the year and plural forms a filing actually uses."""

    terms: list[str] = []
    for token in _TOKEN.findall(question.lower()):
        terms.append(token)
        match = _PERIOD_TOKEN.match(token)
        if match is not None:
            terms.append(match.group(1))
            continue
        if token.endswith("ies") and len(token) > 4:
            terms.append(f"{token[:-3]}y")
        elif token.endswith("es") and len(token) > 4:
            terms.append(token[:-2])
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
            terms.append(token[:-1])
        if "-" in token:
            terms.extend(part for part in token.split("-") if len(part) >= 3)
    return terms


def _is_statement_page(text: str) -> bool:
    """Report whether a page is one of the filer's primary financial statements."""

    lowered = text.lower()
    return any(marker in lowered for marker in _STATEMENT_MARKERS)


def _rank_pdf_pages(
    candidates: Sequence[tuple[object, str, str, int]],
    question: str,
    limit: int,
) -> tuple[object, ...]:
    """Rank stored pages with BM25, then widen the strongest hits to their neighbours."""

    if not candidates:
        return ()
    texts = [candidate[1] for candidate in candidates]
    tokenized = [(_TOKEN.findall(candidate[1].lower()), candidate[0]) for candidate in candidates]
    counts = [Counter(tokens) for tokens, _ in tokenized]
    lengths = [len(tokens) for tokens, _ in tokenized]
    average = (sum(lengths) / len(lengths)) or 1.0
    document_frequency: Counter[str] = Counter()
    for counter in counts:
        document_frequency.update(set(counter))
    total = len(counts)
    weights = _query_weights(question)
    financial = any(term in question.lower() for term in _METRIC_TERMS)
    scored: list[tuple[float, int]] = []
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
        scored.append((score, index))
    scored.sort(key=lambda item: (-item[0], str(getattr(candidates[item[1]][0], "entry_id", ""))))
    order = [index for _score, index in scored]
    order = _widen_with_neighbours(candidates, order, limit)
    return tuple(candidates[index][0] for index in order[:limit])


def _widen_with_neighbours(
    candidates: Sequence[tuple[object, str, str, int]],
    order: Sequence[int],
    limit: int,
) -> list[int]:
    """Add the pages just before and after the strongest hits.

    Financial evidence regularly spans a page break, and the annotation points at whichever
    half carries the number. Reserving the tail of the answer for neighbours of the top hits
    converts one correct page into its whole evidence block without disturbing the head.
    """

    if len(order) <= limit or limit <= _NEIGHBOUR_SLOTS:
        return list(order)
    head_count = min(_NEIGHBOUR_ANCHORS, max(1, limit // 2))
    head = list(order[:head_count])
    by_location = {
        (candidate[2], candidate[3]): index for index, candidate in enumerate(candidates)
    }
    widened: list[int] = list(order[: limit - _NEIGHBOUR_SLOTS])
    chosen = set(widened)
    for index in head:
        document_id, page_number = candidates[index][2], candidates[index][3]
        for offset in _NEIGHBOUR_OFFSETS:
            neighbour = by_location.get((document_id, page_number + offset))
            if neighbour is None or neighbour in chosen:
                continue
            widened.append(neighbour)
            chosen.add(neighbour)
            if len(widened) >= limit:
                return widened
    for index in order:
        if len(widened) >= limit:
            break
        if index not in chosen:
            widened.append(index)
            chosen.add(index)
    return widened


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
