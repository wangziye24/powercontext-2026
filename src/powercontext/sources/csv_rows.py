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

"""Row-level CSV Sources so a retrieved row can cite its whole original record.

A whole-file citation is too coarse to check and a row number drifts as soon as the
table is re-ordered, so each record becomes its own Source keyed by document and primary
key. Column order and every field value are preserved verbatim: nothing here trims,
decodes, or retypes a cell.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from urllib.parse import quote

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_DEFINITION_VERSION = "1"
CSV_ROW_SOURCE_TYPE = "csv-row"
CSV_IDENTITY_PREFIX = "csv"


@dataclass(frozen=True, slots=True)
class CSVDocumentInput:
    """One offline CSV document and the columns that give each record its identity."""

    document_id: str
    primary_key: str
    content: bytes


@dataclass(frozen=True, slots=True)
class CSVRowInput:
    """One record expressed against its document header."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """A captured CSV record whose identity is document plus primary-key value."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str, /) -> str:
    """Compose the delimiter-safe identity shared by a Source and its evidence."""

    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be a non-empty trimmed string")
    if not isinstance(primary_key_value, str) or not primary_key_value:
        raise ValueError("primary key value must be a non-empty string")
    identity = (
        f"{CSV_IDENTITY_PREFIX}:"
        f"{quote(document_id, safe='')}:"
        f"{quote(primary_key_value, safe='')}"
    )
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("row identity exceeds the maximum Source identifier length")
    return identity


def parse_csv_document(document: CSVDocumentInput, /) -> tuple[CSVRowInput, ...]:
    """Parse and fully validate one CSV document into per-row inputs.

    Validation is all-or-nothing: a document with any encoding, quoting, header, width,
    or primary-key problem fails without yielding the rows that happened to be valid.
    """

    document_id = document.document_id
    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be a non-empty trimmed string")
    primary_key = document.primary_key
    if not isinstance(primary_key, str) or not primary_key.strip():
        raise ValueError("primary_key must be a non-empty string")
    records = _read_records(_decode_document(document.content))
    if not records:
        raise ValueError("CSV document is empty")
    header = records[0]
    _validate_header(header)
    try:
        key_index = header.index(primary_key)
    except ValueError:
        raise ValueError("primary_key must name one of the header columns exactly") from None
    width = len(header)
    seen: set[str] = set()
    parsed: list[CSVRowInput] = []
    for record in records[1:]:
        if not record:
            continue
        if len(record) != width:
            raise ValueError("CSV record width must match the header width")
        key_value = record[key_index]
        if not key_value.strip():
            raise ValueError("CSV primary key values must not be empty or blank")
        if key_value in seen:
            raise ValueError("CSV primary key values must be unique within a document")
        seen.add(key_value)
        parsed.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=key_value,
                columns=tuple(header),
                values=tuple(record),
            ),
        )
    return tuple(parsed)


def _decode_document(content: bytes) -> str:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise ValueError("CSV content must be bytes")
    raw = bytes(content)
    if not raw:
        raise ValueError("CSV document is empty")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("CSV document must be strict UTF-8") from error
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text:
        raise ValueError("CSV document is empty")
    return text


def _read_records(text: str) -> list[list[str]]:
    stream = io.StringIO(text, newline="")
    try:
        return list(csv.reader(stream, strict=True))
    except csv.Error as error:
        raise ValueError("CSV document is not well-formed") from error


def _validate_header(header: list[str]) -> None:
    if not header:
        raise ValueError("CSV document must start with a header record")
    for column in header:
        if not column or not column.strip():
            raise ValueError("CSV header columns must be non-empty and not blank")
    if len(set(header)) != len(header):
        raise ValueError("CSV header columns must be unique")


class CSVRowTextEvidence:
    """Expose one CSV record as standard text evidence with row-level metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> object:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_TYPE,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        ).model_dump(mode="json")


class CSVRowSourceDefinition:
    """Bind the CSV row adapter to its durable version and standard projection."""

    name = CSV_ROW_SOURCE_NAME
    version = CSV_ROW_DEFINITION_VERSION
    input_class = CSVRowInput
    source_class = CSVRowSource
    projections = (CSVRowTextEvidence(),)

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=identity,
            source_id=identity,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


CSV_ROW_SOURCE_DEFINITION = CSVRowSourceDefinition()

__all__ = [
    "CSV_ROW_DEFINITION_VERSION",
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_IDENTITY_PREFIX",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceDefinition",
    "CSVRowTextEvidence",
    "csv_row_source_id",
    "parse_csv_document",
]
