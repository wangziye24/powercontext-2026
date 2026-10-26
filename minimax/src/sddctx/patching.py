"""Apply the unified diff produced by the coding assistant.

The applier is intentionally dependency free: the patch is validated against
the current file content before anything is written, so a wrong patch fails
loudly instead of corrupting the working tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

HUNK_HEADER = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")
FILE_HEADER = re.compile(r"^(?:---|\+\+\+)\s+(?P<path>.+?)(?:\t.*)?$")


class PatchError(RuntimeError):
    """Raised when a patch cannot be applied to the current tree."""


@dataclass
class Hunk:
    """One hunk of a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class FilePatch:
    """The hunks that belong to one file."""

    path: str
    hunks: list[Hunk] = field(default_factory=list)
    is_new: bool = False


def extract_diff(text: str) -> str:
    """Return the unified diff contained in a model answer."""

    fenced = re.findall(r"```(?:diff|patch)?\s*\n(.*?)```", text, flags=re.DOTALL)
    for block in fenced:
        if "--- " in block and "+++ " in block:
            return block.strip("\n")
    start = text.find("--- ")
    if start == -1:
        raise PatchError("the model answer does not contain a unified diff")
    return text[start:].strip("\n")


def parse_unified_diff(diff: str) -> list[FilePatch]:
    """Parse a unified diff into per file hunks."""

    patches: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: Hunk | None = None
    pending_old = ""

    for raw in diff.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("--- "):
            match = FILE_HEADER.match(line)
            pending_old = (match.group("path") if match else "").strip()
            current = None
            hunk = None
            continue
        if line.startswith("+++ "):
            match = FILE_HEADER.match(line)
            path = (match.group("path") if match else "").strip()
            if path.startswith("b/"):
                path = path[2:]
            elif path.startswith("a/"):
                path = path[2:]
            current = FilePatch(path=path, is_new=pending_old == "/dev/null")
            patches.append(current)
            hunk = None
            continue
        header = HUNK_HEADER.match(line)
        if header:
            if current is None:
                raise PatchError("a hunk appeared before any file header")
            hunk = Hunk(
                old_start=int(header.group(1)),
                old_count=int(header.group(2) or 1),
                new_start=int(header.group(3)),
                new_count=int(header.group(4) or 1),
            )
            current.hunks.append(hunk)
            continue
        if hunk is None:
            continue
        if line.startswith("+"):
            hunk.lines.append(("+", line[1:]))
        elif line.startswith("-"):
            hunk.lines.append(("-", line[1:]))
        elif line.startswith(" "):
            hunk.lines.append((" ", line[1:]))
        elif line == "":
            hunk.lines.append((" ", ""))
        elif line.startswith("\\"):
            continue
    return [patch for patch in patches if patch.hunks]


def _strip_eol(value: str) -> str:
    return value.rstrip("\r\n")


def _same(left: str, right: str) -> bool:
    """Compare two lines ignoring the trailing end of line marker."""

    return _strip_eol(left) == _strip_eol(right)


def _find_block(lines: list[str], block: list[str], start: int) -> int | None:
    """Locate a hunk block in the file, ignoring the line numbers of the diff.

    A generated patch rarely numbers its hunks exactly like the file on disk.
    Like ``patch(1)``, the context decides where a hunk belongs: the block is
    searched for as a whole, starting at the position of the previous hunk.
    """

    if not block:
        return start
    limit = len(lines) - len(block) + 1
    for position in range(start, max(start, limit)):
        if all(_same(lines[position + offset], block[offset]) for offset in range(len(block))):
            return position
    for position in range(0, max(0, limit)):
        if all(_same(lines[position + offset], block[offset]) for offset in range(len(block))):
            return position
    return None


def apply_unified_diff(root: Path, diff: str) -> list[dict[str, object]]:
    """Apply a unified diff under ``root`` and return a per file summary."""

    root = Path(root)
    patches = parse_unified_diff(diff)
    if not patches:
        raise PatchError("the unified diff does not contain any hunk")

    summary: list[dict[str, object]] = []
    for patch in patches:
        target = root / patch.path
        existed = target.exists()
        original = target.read_text(encoding="utf-8").splitlines() if existed else []
        result: list[str] = []
        cursor = 0
        added = 0
        removed = 0
        for hunk in patch.hunks:
            old_block = [text for kind, text in hunk.lines if kind in (" ", "-")]
            new_block = [text for kind, text in hunk.lines if kind in (" ", "+")]
            position = _find_block(original, old_block, cursor)
            if position is None:
                expected = " / ".join(_strip_eol(text) for text in old_block[:3])
                raise PatchError(
                    f"the patch does not match {patch.path} after line {cursor + 1};"
                    f" expected a block starting with: {expected}"
                )
            result.extend(original[cursor:position])
            result.extend(new_block)
            removed += len(old_block) - len([text for kind, text in hunk.lines if kind == " "])
            added += len(new_block) - len([text for kind, text in hunk.lines if kind == " "])
            cursor = position + len(old_block)
        result.extend(original[cursor:])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(result) + "\n", encoding="utf-8")
        summary.append(
            {
                "path": patch.path,
                "added": added,
                "removed": removed,
                "hunks": len(patch.hunks),
                "created": not existed,
            }
        )
    return summary
