"""Apply on-disk edits and notify language servers of changes."""

from __future__ import annotations

import logging
import pathlib
from typing import Any

from .offsets import position_to_index
from .symbols import SymbolRange

logger = logging.getLogger(__name__)


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
        def _sort_key(edit: dict[str, Any]) -> tuple[int, int]:
            r = edit.get("range", {})
            s = r.get("start", {})
            return (s.get("line", 0), s.get("character", 0))

        sorted_edits = sorted(edits, key=_sort_key, reverse=True)
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
    return (
        pathlib.Path(uri.replace("file://", "")).as_posix()
        if uri.startswith("file://")
        else uri
    )
