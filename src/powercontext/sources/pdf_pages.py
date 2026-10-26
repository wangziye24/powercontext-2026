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
    """Return distinct, exact page citations for one natural-language question."""

    from powercontext.artifacts import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    result = await memory.search(  # type: ignore[attr-defined]
        SearchMemoryRequest(query=question, mode="fts", limit=max(limit * 4, limit)),
    )
    citations: list[object] = []
    seen: set[tuple[str, int]] = set()
    for hit in result.hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry  # type: ignore[attr-defined]
        for reference in entry.sources:
            location = decode_pdf_page_identity(reference.source_id)
            if location is None or location in seen:
                continue
            seen.add(location)
            citations.append(citation)
            break
        if len(citations) >= limit:
            break
    return tuple(citations)


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
