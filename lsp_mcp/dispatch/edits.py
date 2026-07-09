"""Apply on-disk edits and notify language servers of changes."""

from __future__ import annotations

import logging
import pathlib
import textwrap
from typing import Any
from urllib.parse import unquote, urlparse

from .offsets import position_to_index
from .symbols import SymbolRange

logger = logging.getLogger(__name__)


def _normalize_replacement(new_text: str, indent_chars: int) -> str:
    """
    Prepare *new_text* for splicing at a symbol whose start column is
    *indent_chars*.

    ``text[:start_idx]`` already provides the symbol's leading indentation
    on its line, so the replacement text must start at column 0.  Body lines
    that follow must then be re-indented by *indent_chars* spaces to preserve
    their indentation relative to the def/class line.

    Algorithm:
    1. ``textwrap.dedent`` strips common leading whitespace from all lines so
       the first line starts at column 0 regardless of what the caller passed.
    2. Every line after the first gets *indent_chars* spaces prepended so the
       body has the correct absolute indentation in the file.
    3. Trailing newlines are stripped — ``text[end_idx:]`` already provides
       the line separator that follows the symbol.
    """
    dedented = textwrap.dedent(new_text.rstrip("\n"))
    lines = dedented.splitlines(keepends=True)
    if len(lines) <= 1:
        return dedented
    prefix = " " * indent_chars
    result = [lines[0]]
    result.extend(prefix + line for line in lines[1:])
    return "".join(result)


def apply_edit(
    file_path: str,
    sym_range: SymbolRange,
    new_text: str,
    servers: list[Any],
) -> None:
    """
    Replace the text in *file_path* covered by *sym_range* with *new_text*.

    After writing the file, sends ``textDocument/didChange`` to every server
    in *servers* that currently has the file open (tracked in
    ``server.open_file_buffers``).

    Uses UTF-16 code unit offsets (via ``position_to_index``) for correct
    handling of non-BMP characters.
    """
    abs_path = str(pathlib.Path(file_path).resolve())
    text = pathlib.Path(abs_path).read_text(encoding="utf-8")

    # Normalise indentation: text[:start_idx] already contains the leading
    # whitespace for this symbol's line (the symbol's indent column), so
    # new_text must start at column 0 and body lines must be re-indented by
    # start_char spaces to preserve their indentation within the file.
    new_text = _normalize_replacement(new_text, sym_range.start_char)

    start_idx = position_to_index(text, sym_range.start_line, sym_range.start_char)
    end_idx = position_to_index(text, sym_range.end_line, sym_range.end_char)

    new_content = text[:start_idx] + new_text + text[end_idx:]
    pathlib.Path(abs_path).write_text(new_content, encoding="utf-8")

    # Notify open servers
    uri = pathlib.Path(abs_path).as_uri()
    for server in servers:
        bufs = getattr(server, "open_file_buffers", {})
        if uri in bufs:
            buf = bufs[uri]
            buf.contents = new_content
            buf.version += 1
            try:
                server.server.notify.did_change_text_document(
                    {
                        "textDocument": {"uri": uri, "version": buf.version},
                        "contentChanges": [{"text": new_content}],
                    }
                )
            except Exception as exc:
                logger.debug("didChange notification failed for %r: %s", abs_path, exc)


def _edit_start_key(edit: dict[str, Any]) -> tuple[int, int]:
    """Sort key for a TextEdit: (start_line, start_character)."""
    r = edit.get("range", {})
    s = r.get("start", {})
    return (s.get("line", 0), s.get("character", 0))


def apply_workspace_edit(
    workspace_edit: dict[str, Any],
    servers: list[Any],
) -> list[str]:
    """
    Apply a ``WorkspaceEdit`` returned by ``textDocument/rename``.

    Edits within each file are applied **bottom-up** (highest offset first)
    so that earlier edits don't invalidate later offsets.  After each file is
    written, ``textDocument/didChange`` is sent to open servers.

    Returns the sorted list of changed absolute file paths.
    """
    # workspace_edit may use `changes` (map uri -> TextEdit[]) or
    # `documentChanges` (TextDocumentEdit[]).  We handle both.
    changes_by_uri: dict[str, list[dict[str, Any]]] = {}

    if "documentChanges" in workspace_edit:
        for item in workspace_edit["documentChanges"]:
            if isinstance(item, dict) and "textDocument" in item:
                uri = item["textDocument"]["uri"]
                changes_by_uri.setdefault(uri, []).extend(item.get("edits", []))
    elif "changes" in workspace_edit:
        for uri, edits in workspace_edit["changes"].items():
            changes_by_uri.setdefault(uri, []).extend(edits)

    changed_files: list[str] = []
    for uri, edits in changes_by_uri.items():
        abs_path = _uri_to_path(uri)
        try:
            text = pathlib.Path(abs_path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %r for rename edit: %s", abs_path, exc)
            continue

        # Sort edits bottom-up by start offset (descending)
        sorted_edits = sorted(edits, key=_edit_start_key, reverse=True)
        for edit in sorted_edits:
            r = edit.get("range", {})
            sr = r.get("start", {})
            er = r.get("end", {})
            start_idx = position_to_index(
                text, sr.get("line", 0), sr.get("character", 0)
            )
            end_idx = position_to_index(text, er.get("line", 0), er.get("character", 0))
            new_text = edit.get("newText", "")
            text = text[:start_idx] + new_text + text[end_idx:]

        pathlib.Path(abs_path).write_text(text, encoding="utf-8")
        changed_files.append(abs_path)

        # Notify open servers
        for server in servers:
            bufs = getattr(server, "open_file_buffers", {})
            if uri in bufs:
                buf = bufs[uri]
                buf.contents = text
                buf.version += 1
                try:
                    server.server.notify.did_change_text_document(
                        {
                            "textDocument": {"uri": uri, "version": buf.version},
                            "contentChanges": [{"text": text}],
                        }
                    )
                except Exception as exc:
                    logger.debug(
                        "didChange notification failed for %r: %s", abs_path, exc
                    )

    return sorted(changed_files)


def _uri_to_path(uri: str) -> str:
    """Convert a ``file://`` URI to an absolute path, decoding percent-encoding."""
    if uri.startswith("file://"):
        return str(pathlib.Path(unquote(urlparse(uri).path)))
    return uri
