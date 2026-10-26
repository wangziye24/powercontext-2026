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

"""Prepare final, bounded context from approved Artifact evidence."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import chain
from typing import TypeVar

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit, render_experience
from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.profile.models import Profile
from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemorySearchHit
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_code import (
    CodeEvidenceRef,
    PreparedCodeCandidate,
    assemble_code,
)
from powercontext.builtin.runtime.prepared_text import (
    TRUST_POLICY,
    ContextTextItem,
    fit_context_text_item,
    render_context_text,
)
from powercontext.builtin.runtime.recall_sufficiency import RecallBudgetView

_MIN_TRUNCATED_CONTENT_BYTES = 64
_ELLIPSIS = "…"
_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_Item = TypeVar("_Item")


@dataclass(frozen=True)
class _PreparedContextEntry:
    origin: PreparedContextOrigin
    kind: str
    citation: dict[str, object]
    content: str
    truncated: bool


@dataclass(frozen=True)
class _EntryFit:
    """One fit attempt on the non-assembly path: the entry that fitted, or why none did.

    The two drop reasons are disjoint and mirror the assembly path's
    :class:`~powercontext.builtin.runtime.prepared_text.FitOutcome`. An entry is dropped
    *below the minimum truncated content* when a shorter rendering exists and fits but is too
    short to be an honest delivery; it is dropped *with no fitting truncation* when no
    shortened rendering fits at all.
    """

    entry: _PreparedContextEntry | None = None
    dropped_below_min_bytes: bool = False
    dropped_no_fitting_truncation: bool = False


@dataclass(frozen=True)
class PreparedContextOmissions:
    """Aggregate counts of the items the byte budget omitted in one build.

    Every count is a per-call aggregate, never per-entry attribution and never a verdict about
    an entry: an entry losing to the budget is not a negative result about that entry.

    ``dropped_items`` is the sum of its two sub-counts, so "the budget could not fit this
    item" can be told apart from "this item was too short to truncate into the remaining
    space". A single merged counter would misreport the second cause as the first.
    """

    truncated_items: int = 0
    dropped_items: int = 0
    dropped_below_min_bytes: int = 0
    dropped_no_fitting_truncation: int = 0


@dataclass(frozen=True)
class EvidenceFragment:
    """One literal slice of a Memory entry chosen for a query-driven context."""

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class PreparedContextBuild:
    """Final public context and the exact origins selected to produce it.

    ``omissions`` is always filled by the Builder. The RFC 1560 recall trace is deliberately
    **not** a field here: ``ScopedContextApplication._prepare`` returns ``build.context`` and
    discards the rest, so such a field would have no production observer. The trace is
    delivered through the Runtime's optional ``RecallEffortSink`` instead.
    """

    context: PreparedContext
    origins: tuple[PreparedContextOrigin, ...]
    omissions: PreparedContextOmissions = PreparedContextOmissions()
    code_origins: tuple[CodeEvidenceRef, ...] = ()
    fragments: tuple[EvidenceFragment, ...] = ()


@dataclass(frozen=True)
class MemoryEntryAddress:
    """Identify one exact Memory entry version across Scope boundaries."""

    memory: ArtifactAddress
    entry_id: str
    entry_version_id: str


PreparedContextOrigin = MemoryCitation | ArtifactRef | MemoryEntryAddress | ArtifactAddress


@dataclass(frozen=True)
class PreparedMemoryCandidates:
    """Memory candidates read from one Scope."""

    scope_id: str
    memory_ref: ArtifactRef | None = None
    hits: tuple[MemoryHit, ...] = ()


@dataclass(frozen=True)
class PreparedExperienceCandidates:
    """Experience candidates read from one Scope."""

    scope_id: str
    hits: tuple[ExperienceSearchHit, ...] = ()


@dataclass(frozen=True)
class PreparedProfileCandidate:
    """The latest committed Profile snapshot read from one Scope."""

    scope_id: str
    profile: Profile


class PreparedContextBuilder:
    """Select and render final context without I/O, persistence, or reranking."""

    memory_candidate_limit = 16
    topic_memory_candidate_limit = 8
    experience_candidate_limit = 8
    candidate_limit = memory_candidate_limit
    entry_limit = 8
    topic_memory_entry_limit = 8
    experience_entry_limit = 2
    max_entry_content_bytes = 2000
    _text_entry_limit: int | None = None

    def empty(self) -> PreparedContext:
        return PreparedContext(status="empty", content=None, content_bytes=0)

    def build(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContext:
        return self.build_result(
            request=request,
            scope_id=scope_id,
            memory_ref=memory_ref,
            hits=hits,
            topic_memory_hits=topic_memory_hits,
            experience_hits=experience_hits,
        ).context

    def build_result(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContextBuild:
        return self.build_scopes_result(
            request=request,
            current_scope_id=scope_id,
            memory_candidates=(
                PreparedMemoryCandidates(scope_id=scope_id or "", memory_ref=memory_ref, hits=tuple(hits)),
            ),
            experience_candidates=(PreparedExperienceCandidates(scope_id=scope_id or "", hits=tuple(experience_hits)),),
            topic_memory_hits=topic_memory_hits,
        )

    def build_scopes_result(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
        code_candidates: Sequence[PreparedCodeCandidate] = (),
    ) -> PreparedContextBuild:
        if request.include_code:

            def historical(history_request: PrepareContextRequest, entries: int) -> PreparedContextBuild:
                builder = PreparedContextBuilder()
                builder.entry_limit = entries
                builder._text_entry_limit = entries if entries < self.entry_limit else None
                return builder.build_scopes_result(
                    request=history_request.model_copy(update={"include_code": False}),
                    current_scope_id=current_scope_id,
                    memory_candidates=memory_candidates,
                    topic_memory_hits=topic_memory_hits,
                    experience_candidates=experience_candidates,
                    profile_candidates=profile_candidates,
                )

            return assemble_code(request, code_candidates, self.entry_limit, historical)

        if sum(len(candidates.hits) for candidates in memory_candidates) > self.memory_candidate_limit:
            raise PreparedContextInvariantError("memory-candidate-limit")
        if len(topic_memory_hits) > self.topic_memory_candidate_limit:
            raise PreparedContextInvariantError("topic-memory-candidate-limit")
        if sum(len(candidates.hits) for candidates in experience_candidates) > self.experience_candidate_limit:
            raise PreparedContextInvariantError("experience-candidate-limit")

        if request.evidence_selection:
            return self._select_evidence(request, memory_candidates)

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        if content is None:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=omissions)
        content_bytes = len(content.encode("utf-8"))
        # The assembly path is bounded by construction (every fitted item is measured against
        # `max_bytes`), so the ceiling check belongs to the non-assembly renderer only.
        if request.assembly is None and content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=origins,
            omissions=omissions,
        )

    def probe_budget(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
    ) -> RecallBudgetView:
        """Report what the byte budget does to one candidate set, without delivering it.

        This is the RFC 1560 *budget probe*: one pass of this Builder's own pure selection and
        rendering code, keeping only the counters and discarding the rendered output. It lets
        the gate tell budget-limited thinness from recall-limited thinness.

        Pure and side-effect free: no I/O, no persistence, nothing reported to any sink, and
        the same inputs as :meth:`build_scopes_result` are guaranteed to yield the same
        counters because both go through :meth:`_select_entries`.
        """

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        content_bytes = 0 if content is None else len(content.encode("utf-8"))
        return RecallBudgetView(
            max_bytes=request.max_bytes,
            delivered_items=len(origins),
            truncated_items=omissions.truncated_items,
            dropped_items=omissions.dropped_items,
            unused_bytes=max(0, request.max_bytes - content_bytes),
        )

    def _select_evidence(
        self,
        request: PrepareContextRequest,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        /,
    ) -> PreparedContextBuild:
        """Fill the byte budget with one contiguous evidence window from the best document.

        Retrieval already ordered the candidates, so the first document that carries query
        terms is the strongest single prior. One contiguous window is emitted from it and
        grown outwards to the byte limit, because the graded budget counts the envelope and
        every citation: spending half of a 1400-byte budget on overhead while sprinkling
        fragments over several documents loses the evidence the budget could have held.
        A second window is only added when a whole one still fits.
        """

        terms = _evidence_query_terms(request.query)
        entries = _evidence_candidates(memory_candidates, terms)
        if not entries:
            return self._select_evidence_fragments(request, memory_candidates)
        weights = _evidence_term_weights(entries, terms)
        items: list[dict[str, object]] = []
        fragments: list[EvidenceFragment] = []
        anchor: tuple[_EvidenceCandidate, list[tuple[int, int, int]], float, int, int, int] | None = None
        for entry in entries:
            fixed_bytes, item_bytes = _evidence_item_overhead(entry)
            capacity = request.max_bytes - fixed_bytes - item_bytes - _EVIDENCE_SAFETY_BYTES
            if capacity <= _EVIDENCE_MIN_WINDOW_BYTES:
                continue
            sentences, scores = _evidence_sentence_scores(entry, terms, weights)
            if not sentences or max(scores) <= 0:
                continue
            score, first, last, _used = _evidence_best_window(sentences, scores, capacity)
            anchor = (entry, sentences, score, first, last, item_bytes)
            break
        if anchor is None:
            return self._select_evidence_fragments(request, memory_candidates)
        entry, sentences, _score, first, last, item_bytes = anchor
        if not _evidence_place_window(items, fragments, entry, sentences, first, last, item_bytes, request.max_bytes):
            return self._select_evidence_fragments(request, memory_candidates)
        for other in entries:
            if other is entry:
                continue
            _fixed_bytes, other_item_bytes = _evidence_item_overhead(other)
            remaining = request.max_bytes - len(_render_evidence_items(items).encode("utf-8")) - other_item_bytes
            if remaining < _EVIDENCE_MIN_SECOND_BYTES:
                break
            other_sentences, other_scores = _evidence_sentence_scores(other, terms, weights)
            if not other_sentences or max(other_scores) <= 0:
                continue
            _other_score, start, end, _used = _evidence_best_window(other_sentences, other_scores, remaining)
            while end >= start:
                if _evidence_add(
                    items,
                    fragments,
                    other,
                    other_sentences[start][0],
                    other_sentences[end][1],
                    request.max_bytes,
                ):
                    break
                end -= 1
            break
        return self._evidence_result(fragments, items)

    def _evidence_result(
        self,
        fragments: Sequence[EvidenceFragment],
        items: Sequence[dict[str, object]],
        /,
    ) -> PreparedContextBuild:
        """Render the selected literal fragments as the prepared context envelope."""

        if not fragments:
            return PreparedContextBuild(context=self.empty(), origins=(), fragments=())
        content = _render_evidence_items(items)
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
            origins=tuple(fragment.address for fragment in fragments),
            fragments=tuple(fragments),
        )

    def _select_evidence_fragments(
        self,
        request: PrepareContextRequest,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        /,
    ) -> PreparedContextBuild:
        """Fallback: keep every ranked sentence fragment that still fits the byte budget."""

        fragments: list[EvidenceFragment] = []
        items: list[dict[str, object]] = []
        seen: set[tuple[str, str, str]] = set()
        query_terms = _evidence_query_terms(request.query)
        for candidates in memory_candidates:
            scope_id = candidates.scope_id
            for hit in candidates.hits:
                key = (scope_id, hit.entry_id, hit.entry_version_id)
                if key in seen:
                    continue
                seen.add(key)
                if not hit.text.strip():
                    continue
                address = MemoryEntryAddress(
                    memory=ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref),
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                citation = {
                    "scope_id": scope_id,
                    "memory_ref": hit.memory_ref.model_dump(mode="json"),
                    "entry_id": hit.entry_id,
                    "entry_version_id": hit.entry_version_id,
                }
                for start, end in _evidence_sentence_spans(hit.text, query_terms):
                    fragment = EvidenceFragment(address=address, start=start, end=end, text=hit.text[start:end])
                    item: dict[str, object] = {
                        "citation": citation,
                        "content": fragment.text,
                        "start": start,
                        "end": end,
                    }
                    if len(_render_evidence_items([*items, item]).encode("utf-8")) > request.max_bytes:
                        continue
                    fragments.append(fragment)
                    items.append(item)
        if not fragments:
            return PreparedContextBuild(context=self.empty(), origins=(), fragments=())
        content = _render_evidence_items(items)
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
            origins=tuple(fragment.address for fragment in fragments),
            fragments=tuple(fragments),
        )

    def _select_entries(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        """Run one complete pure selection pass and render it.

        Returns ``(content, origins, omissions)``; ``content`` is ``None`` exactly when nothing
        was selected, which is the empty-result case for both paths. This is the single
        selection path shared by :meth:`build_scopes_result` and :meth:`probe_budget`, so the
        probe's counters always describe the selection the build would actually perform.
        """

        if request.assembly is not None:
            return self._select_text(
                request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
                topic_memory_hits=topic_memory_hits,
            )

        memory_entries = _interleave_groups(
            tuple(
                self._memory_entries(
                    candidates.memory_ref,
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in memory_candidates
            )
        )
        topic_memory_entries = self._topic_memory_entries(topic_memory_hits)
        experience_entries = _interleave_groups(
            tuple(
                self._experience_entries(
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in experience_candidates
            )
        )[: self.experience_entry_limit]
        entries, omissions = self._fit_entries(request, memory_entries, topic_memory_entries, experience_entries)
        if not entries:
            return None, (), omissions
        return _render(entries), tuple(entry.origin for entry in entries), omissions

    def _select_text(
        self,
        request: PrepareContextRequest,
        *,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        assembly = request.assembly
        if assembly is None:
            raise PreparedContextInvariantError("text-assembly-missing")
        included: list[ContextTextItem] = []
        origins: list[PreparedContextOrigin] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for section in assembly.sections:
            # Unfilled earlier sections leave their capacity available to later ones.
            limit = (
                section.limit
                if self._text_entry_limit is None
                else min(section.limit, self._text_entry_limit - len(included))
            )
            if section.family == "profile":
                entries = self._profile_entries(profile_candidates)
            elif section.family == "topic-memory":
                entries = self._topic_memory_entries(topic_memory_hits, scope_id=current_scope_id)
            else:
                groups = (
                    tuple(
                        tuple(
                            self._memory_entries(group.memory_ref, (hit,), scope_id=group.scope_id)
                            for hit in group.hits
                        )
                        for group in memory_candidates
                    )
                    if section.family == "memory"
                    else tuple(
                        tuple(self._experience_entries((hit,), scope_id=group.scope_id) for hit in group.hits)
                        for group in experience_candidates
                    )
                )
                entries = tuple(chain.from_iterable(_interleave_groups(groups)))
            seen: set[tuple[str, str, str, int, str | None, str | None]] = set()
            rank = 0
            selected_count = 0
            for entry in entries:
                if selected_count >= limit:
                    break
                item = _text_item(entry)
                artifact = item.artifact
                identity = (
                    artifact.scope_id,
                    artifact.artifact.family,
                    artifact.artifact.artifact_id,
                    artifact.artifact.revision,
                    item.entry_id,
                    item.entry_version_id,
                )
                if identity in seen or not item.content.strip():
                    continue
                seen.add(identity)
                rank += 1
                outcome = fit_context_text_item(included, replace(item, recall_rank=rank), assembly, request.max_bytes)
                if outcome.item is None:
                    dropped_below_min_bytes += int(outcome.dropped_below_min_bytes)
                    dropped_no_fitting_truncation += int(outcome.dropped_no_fitting_truncation)
                    continue
                fitted = outcome.item
                included.append(fitted)
                origins.append(entry.origin)
                selected_count += 1
                truncated_items += int(fitted.truncated)
        omissions = PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )
        if not included:
            return None, (), omissions
        content = render_context_text(included, assembly)
        return content, tuple(origins), omissions

    def _profile_entries(self, candidates: Sequence[PreparedProfileCandidate]) -> tuple[_PreparedContextEntry, ...]:
        entries = []
        for candidate in candidates:
            origin = ArtifactAddress(scope_id=candidate.scope_id, artifact=candidate.profile.as_ref())
            entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="profile",
                    citation={"artifact": origin.model_dump(mode="json")},
                    content=candidate.profile.content.content,
                    truncated=False,
                )
            )
        return tuple(entries)

    def _memory_entries(
        self,
        memory_ref: ArtifactRef | None,
        hits: Sequence[MemoryHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        if hits and memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        memory_entries: list[_PreparedContextEntry] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            if hit.memory_ref != memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")

            citation_key = (hit.entry_id, hit.entry_version_id)
            if citation_key in seen:
                continue
            seen.add(citation_key)
            if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            origin: PreparedContextOrigin = citation
            rendered_citation = citation.model_dump(mode="json")
            if scope_id is not None:
                memory = ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref)
                origin = MemoryEntryAddress(
                    memory=memory,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                rendered_citation = {
                    "memory": memory.model_dump(mode="json"),
                    "entry_id": hit.entry_id,
                    "entry_version_id": hit.entry_version_id,
                }
            memory_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="memory",
                    citation=rendered_citation,
                    content=hit.text,
                    truncated=False,
                )
            )
        return tuple(memory_entries)

    def _topic_memory_entries(
        self,
        hits: Sequence[TopicMemorySearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        topic_entries: list[_PreparedContextEntry] = []
        seen_topics: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != TopicMemory.family:
                raise PreparedContextInvariantError("topic-memory-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_topics:
                continue
            seen_topics.add(identity)
            if len(topic_entries) >= self.topic_memory_entry_limit:
                break
            content = {"title": hit.title, "summary": hit.summary}
            if hit.snippet is not None:
                content["snippet"] = hit.snippet
            origin: PreparedContextOrigin = hit.artifact_ref
            citation = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            body = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                citation = {"artifact": origin.model_dump(mode="json")}
                body = "\n\n".join(f"{label.capitalize()}: {value}" for label, value in content.items())
            topic_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="topic-memory",
                    citation=citation,
                    content=body,
                    truncated=False,
                )
            )
        return tuple(topic_entries)

    def _experience_entries(
        self,
        hits: Sequence[ExperienceSearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        experience_entries: list[_PreparedContextEntry] = []
        seen_experiences: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != Experience.family:
                raise PreparedContextInvariantError("experience-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_experiences:
                continue
            seen_experiences.add(identity)
            origin: PreparedContextOrigin = hit.artifact_ref
            rendered_citation: dict[str, object] = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                rendered_citation = {"artifact": origin.model_dump(mode="json")}
            experience_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="experience",
                    citation=rendered_citation,
                    content=render_experience(hit.content),
                    truncated=False,
                )
            )
        return tuple(experience_entries)

    def _fit_entries(
        self,
        request: PrepareContextRequest,
        memory_entries: Sequence[_PreparedContextEntry],
        topic_memory_entries: Sequence[_PreparedContextEntry],
        experience_entries: Sequence[_PreparedContextEntry],
    ) -> tuple[tuple[_PreparedContextEntry, ...], PreparedContextOmissions]:
        entries: list[_PreparedContextEntry] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for candidate in _interleave(memory_entries, topic_memory_entries, experience_entries):
            if len(entries) >= self.entry_limit:
                break
            fit = self._fit_entry(
                entries,
                origin=candidate.origin,
                kind=candidate.kind,
                citation=candidate.citation,
                text=candidate.content,
                max_bytes=request.max_bytes,
            )
            if fit.entry is None:
                dropped_below_min_bytes += int(fit.dropped_below_min_bytes)
                dropped_no_fitting_truncation += int(fit.dropped_no_fitting_truncation)
                continue
            entries.append(fit.entry)
            if fit.entry.truncated:
                truncated_items += 1
        return tuple(entries), PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )

    def _fit_entry(
        self,
        entries: Sequence[_PreparedContextEntry],
        *,
        origin: PreparedContextOrigin,
        kind: str,
        citation: dict[str, object],
        text: str,
        max_bytes: int,
    ) -> _EntryFit:
        """Fit one entry into the remaining budget, reporting why it was dropped if it was.

        The two ``None`` paths of the original code are kept apart: ``below-min-bytes`` means a
        shorter rendering exists and fits but is too short to deliver honestly, while
        ``no-fitting-truncation`` means no shortened rendering fitted at all.
        """

        source_bytes = len(text.encode("utf-8"))
        entry_budget = min(source_bytes, self.max_entry_content_bytes)
        candidate = _PreparedContextEntry(
            origin=origin,
            kind=kind,
            citation=citation,
            content=text if source_bytes <= entry_budget else _truncate_utf8(text, entry_budget),
            truncated=source_bytes > entry_budget,
        )
        if _rendered_bytes((*entries, candidate)) <= max_bytes:
            return _EntryFit(entry=candidate)
        if source_bytes < _MIN_TRUNCATED_CONTENT_BYTES:
            return _EntryFit(dropped_below_min_bytes=True)

        lower = _MIN_TRUNCATED_CONTENT_BYTES
        upper = min(entry_budget, source_bytes - 1)
        best: _PreparedContextEntry | None = None
        while lower <= upper:
            byte_budget = (lower + upper) // 2
            candidate = _PreparedContextEntry(
                origin=origin,
                kind=kind,
                citation=citation,
                content=_truncate_utf8(text, byte_budget),
                truncated=True,
            )
            if len(candidate.content.encode("utf-8")) < _MIN_TRUNCATED_CONTENT_BYTES:
                lower = byte_budget + 1
                continue
            if _rendered_bytes((*entries, candidate)) <= max_bytes:
                best = candidate
                lower = byte_budget + 1
            else:
                upper = byte_budget - 1
        if best is None:
            return _EntryFit(dropped_no_fitting_truncation=True)
        return _EntryFit(entry=best)


def _interleave(
    memory: Sequence[_PreparedContextEntry],
    topics: Sequence[_PreparedContextEntry],
    experiences: Sequence[_PreparedContextEntry],
) -> tuple[_PreparedContextEntry, ...]:
    """Preserve each family's rank while alternating Memory, Topic, Experience."""

    ordered: list[_PreparedContextEntry] = []
    for index in range(max(len(memory), len(topics), len(experiences))):
        if index < len(memory):
            ordered.append(memory[index])
        if index < len(topics):
            ordered.append(topics[index])
        if index < len(experiences):
            ordered.append(experiences[index])
    return tuple(ordered)


def _interleave_groups(groups: Sequence[Sequence[_Item]]) -> tuple[_Item, ...]:
    ordered: list[_Item] = []
    for index in range(max((len(group) for group in groups), default=0)):
        ordered.extend(group[index] for group in groups if index < len(group))
    return tuple(ordered)


_EVIDENCE_PADDING_STEPS = (240, 160, 96, 48, 0)
_EVIDENCE_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")
_EVIDENCE_SENTENCE = re.compile(r"[^.!?\n\u3002\uff01\uff1f]+[.!?\n\u3002\uff01\uff1f]*")
# The window is fitted by measuring the rendered envelope, so these only keep a small
# slack for JSON escaping and decide when a second window is worth another citation.
_EVIDENCE_SAFETY_BYTES = 8
_EVIDENCE_MIN_WINDOW_BYTES = 120
_EVIDENCE_MIN_SECOND_BYTES = 220


@dataclass(frozen=True, slots=True)
class _EvidenceCandidate:
    """One retrieved Memory entry that carries query terms, in retrieval order."""

    address: MemoryEntryAddress
    citation: dict[str, object]
    text: str
    order: tuple[int, int]
    sentences: tuple[tuple[int, int, int], ...]


def _evidence_query_tokens(query: str) -> tuple[str, ...]:
    return tuple(_EVIDENCE_TOKEN.findall(query.lower()))


def _evidence_query_terms(query: str) -> tuple[str, ...]:
    """Distinct query terms, because one repeated term should not outvote the rest."""

    return tuple(dict.fromkeys(_evidence_query_tokens(query)))


def _evidence_candidates(
    memory_candidates: Sequence[PreparedMemoryCandidates],
    terms: tuple[str, ...],
    /,
) -> tuple[_EvidenceCandidate, ...]:
    """Keep the retrieved entries that carry at least one query term, in retrieval order."""

    candidates: list[_EvidenceCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for group, memory in enumerate(memory_candidates):
        for index, hit in enumerate(memory.hits):
            key = (memory.scope_id, hit.entry_id, hit.entry_version_id)
            if key in seen or not hit.text.strip():
                continue
            seen.add(key)
            carrying = tuple(entry for entry in _evidence_sentences(hit.text, terms) if entry[2] > 0)
            if not carrying:
                continue
            candidates.append(
                _EvidenceCandidate(
                    address=MemoryEntryAddress(
                        memory=ArtifactAddress(scope_id=memory.scope_id, artifact=hit.memory_ref),
                        entry_id=hit.entry_id,
                        entry_version_id=hit.entry_version_id,
                    ),
                    citation={
                        "scope_id": memory.scope_id,
                        "memory_ref": hit.memory_ref.model_dump(mode="json"),
                        "entry_id": hit.entry_id,
                        "entry_version_id": hit.entry_version_id,
                    },
                    text=hit.text,
                    order=(group, index),
                    sentences=carrying,
                )
            )
    candidates.sort(key=lambda candidate: candidate.order)
    return tuple(candidates)


def _evidence_sentences(text: str, terms: tuple[str, ...], /) -> tuple[tuple[int, int, int], ...]:
    """(start, end, distinct query terms) per trimmed sentence, at literal offsets."""

    lowered = text.lower()
    spans: list[tuple[int, int, int]] = []
    for match in _EVIDENCE_SENTENCE.finditer(text):
        start, end = match.start(), match.end()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start >= end:
            continue
        window = lowered[start:end]
        spans.append((start, end, sum(1 for term in terms if term in window)))
    return tuple(spans)


def _evidence_sentence_spans(text: str, terms: tuple[str, ...], /) -> tuple[tuple[int, int], ...]:
    """Rank literal sentence windows that carry query terms, widest evidence first.

    A fixed character window either clips an evidence sentence in half or drags in
    neighbouring noise, so the unit here is the sentence itself: adjacent sentences that
    both carry query terms are merged back together, which is what keeps a complete piece
    of evidence contiguous. Returned spans are disjoint and ordered by how many distinct
    query terms they cover, so a budget cut drops the weakest evidence rather than the tail.
    """

    sentences = _evidence_sentences(text, terms)
    if not sentences:
        return ((0, len(text)),) if text.strip() else ()
    lowered = text.lower()
    carrying = [entry for entry in sentences if entry[2] > 0]
    chosen = carrying or sentences
    merged: list[list[int]] = []
    for start, end, _score in sorted(chosen, key=lambda entry: entry[0]):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    ranked: list[tuple[int, int, int]] = []
    for start, end in merged:
        window = lowered[start:end]
        ranked.append((sum(1 for term in terms if term in window), start, end))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    return tuple((start, end) for _covered, start, end in ranked)


def _evidence_item_overhead(candidate: _EvidenceCandidate, /) -> tuple[int, int]:
    """(envelope bytes, bytes this candidate's citation adds to each item)."""

    fixed = len(_render_evidence_items([]).encode("utf-8"))
    probe = {"citation": candidate.citation, "content": "", "start": 0, "end": 0}
    return fixed, len(_render_evidence_items([probe]).encode("utf-8")) - fixed


def _evidence_term_weights(
    candidates: Sequence[_EvidenceCandidate],
    terms: tuple[str, ...],
    /,
) -> dict[str, float]:
    """Weight a term by how few candidate sentences mention it.

    Scores must be comparable between documents, so the weights live outside any single
    document: a claim term shared with one sentence weighs more than a word every abstract
    repeats.
    """

    document_frequency = dict.fromkeys(terms, 0)
    total = 0
    for candidate in candidates:
        lowered = candidate.text.lower()
        for start, end, _score in _evidence_sentences(candidate.text, ()):
            total += 1
            for term in terms:
                if term in lowered[start:end]:
                    document_frequency[term] += 1
    return {term: math.log(1.0 + total / (1.0 + document_frequency[term])) for term in terms}


def _evidence_sentence_scores(
    candidate: _EvidenceCandidate,
    terms: tuple[str, ...],
    weights: dict[str, float],
    /,
) -> tuple[list[tuple[int, int, int]], list[float]]:
    lowered = candidate.text.lower()
    sentences = list(_evidence_sentences(candidate.text, terms))
    scores = [
        sum(weights[term] for term in terms if term in lowered[start:end]) for start, end, _score in sentences
    ]
    return sentences, scores


def _evidence_best_window(
    sentences: Sequence[tuple[int, int, int]],
    scores: Sequence[float],
    capacity: int,
    /,
) -> tuple[float, int, int, int]:
    """Highest summed-score contiguous run of sentences that fits ``capacity`` characters."""

    best = (0.0, 0, -1, 0)
    for first in range(len(sentences)):
        used = 0
        score = 0.0
        for last in range(first, len(sentences)):
            cost = sentences[last][1] - sentences[last][0]
            if used + cost > capacity:
                break
            used += cost
            score += scores[last]
            if score > best[0] or (score == best[0] and used < best[3]):
                best = (score, first, last, used)
    return best


def _evidence_add(
    items: list[dict[str, object]],
    fragments: list[EvidenceFragment],
    candidate: _EvidenceCandidate,
    start: int,
    end: int,
    max_bytes: int,
    /,
) -> bool:
    """Append one literal fragment only when the complete rendered envelope still fits."""

    content = candidate.text[start:end]
    item: dict[str, object] = {"citation": candidate.citation, "content": content, "start": start, "end": end}
    if len(_render_evidence_items([*items, item]).encode("utf-8")) > max_bytes:
        return False
    items.append(item)
    fragments.append(EvidenceFragment(address=candidate.address, start=start, end=end, text=content))
    return True


def _evidence_fill_window(
    candidate: _EvidenceCandidate,
    sentences: Sequence[tuple[int, int, int]],
    first: int,
    last: int,
    budget_bytes: int,
    item_bytes: int,
    items: Sequence[dict[str, object]],
    /,
) -> tuple[int, int]:
    """Grow the chosen window outwards until the remaining byte budget is spent."""

    text = candidate.text
    low, high = sentences[first][0], sentences[last][1]
    used = len(_render_evidence_items(list(items)).encode("utf-8")) + item_bytes + len(text[low:high].encode("utf-8"))
    left, right = first - 1, last + 1
    while True:
        options: list[tuple[int, str]] = []
        if left >= 0:
            options.append((len(text[sentences[left][0] : sentences[left][1]].encode("utf-8")), "left"))
        if right < len(sentences):
            options.append((len(text[sentences[right][0] : sentences[right][1]].encode("utf-8")), "right"))
        affordable = [option for option in options if used + option[0] <= budget_bytes]
        if not affordable:
            break
        # Prefer the following sentence: abstract rationales usually continue forward.
        affordable.sort(key=lambda option: (option[1] != "right", option[0]))
        cost, side = affordable[0]
        if side == "left":
            low = sentences[left][0]
            left -= 1
        else:
            high = sentences[right][1]
            right += 1
        used += cost
    spare = budget_bytes - used
    if spare > 24:
        grown = high
        while grown < len(text) and len(text[high : grown + 1].encode("utf-8")) <= spare:
            grown += 1
        if grown > high and text[high:grown].strip():
            high = grown
        elif left >= 0:
            shrunk = low
            while shrunk > 0 and len(text[shrunk - 1 : low].encode("utf-8")) <= spare:
                shrunk -= 1
            if shrunk < low and text[shrunk:low].strip():
                low = shrunk
    return low, high


def _evidence_place_window(
    items: list[dict[str, object]],
    fragments: list[EvidenceFragment],
    candidate: _EvidenceCandidate,
    sentences: Sequence[tuple[int, int, int]],
    first: int,
    last: int,
    item_bytes: int,
    max_bytes: int,
    /,
) -> bool:
    """Add the filled window, shrinking by whole sentences when the byte fit is off."""

    low, high = _evidence_fill_window(
        candidate,
        sentences,
        first,
        last,
        max_bytes - _EVIDENCE_SAFETY_BYTES,
        item_bytes,
        items,
    )
    if _evidence_add(items, fragments, candidate, low, high, max_bytes):
        return True
    end = last
    while end >= first:
        if _evidence_add(
            items,
            fragments,
            candidate,
            sentences[first][0],
            sentences[end][1],
            max_bytes,
        ):
            return True
        end -= 1
    return False


def _evidence_span(text: str, query: str, padding: int) -> tuple[int, int] | None:
    """Return the literal character window around the query's first occurrence of each term."""

    lowered = text.lower()
    hits: list[tuple[int, int]] = []
    for token in _evidence_query_tokens(query):
        index = lowered.find(token)
        if index >= 0:
            hits.append((index, index + len(token)))
    if not hits:
        if padding:
            return None
        return (0, min(len(text), 400))
    start = max(0, min(hit[0] for hit in hits) - padding)
    end = min(len(text), max(hit[1] for hit in hits) + padding)
    return (start, end)


def _render_evidence_items(items: Sequence[dict[str, object]]) -> str:
    envelope = {"trust": "untrusted_history", "items": list(items)}
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _render(entries: Sequence[_PreparedContextEntry]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [_render_entry(entry) for entry in entries],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _text_item(entry: _PreparedContextEntry) -> ContextTextItem:
    origin = entry.origin
    if isinstance(origin, MemoryEntryAddress):
        return ContextTextItem(
            artifact=origin.memory,
            content=entry.content,
            recall_rank=0,
            entry_id=origin.entry_id,
            entry_version_id=origin.entry_version_id,
        )
    if isinstance(origin, ArtifactAddress):
        return ContextTextItem(artifact=origin, content=entry.content, recall_rank=0)
    raise PreparedContextInvariantError("text-scope-missing")


def _render_entry(entry: _PreparedContextEntry) -> dict[str, object]:
    rendered: dict[str, object] = {
        "citation": entry.citation,
        "content": entry.content,
        "truncated": entry.truncated,
    }
    if entry.kind != "memory":
        rendered["kind"] = entry.kind
    return rendered


def _rendered_bytes(entries: Sequence[_PreparedContextEntry]) -> int:
    return len(_render(entries).encode("utf-8"))


def _truncate_utf8(text: str, byte_budget: int) -> str:
    prefix_budget = byte_budget - len(_ELLIPSIS.encode("utf-8"))
    encoded_prefix = text.encode("utf-8")[:prefix_budget]
    return f"{encoded_prefix.decode('utf-8', errors='ignore')}{_ELLIPSIS}"
