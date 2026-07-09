"""Tests for apply_edit and apply_workspace_edit."""

from __future__ import annotations

from pathlib import Path

from lsp_mcp.dispatch.edits import apply_edit, apply_workspace_edit
from lsp_mcp.dispatch.symbols import SymbolRange


# ---------------------------------------------------------------------------
# apply_edit
# ---------------------------------------------------------------------------


def test_apply_edit_simple(tmp_path: Path) -> None:
    f = tmp_path / "test.py"
    f.write_text("def foo():\n    pass\n", encoding="utf-8")
    sym_range = SymbolRange(start_line=0, start_char=0, end_line=1, end_char=8)
    apply_edit(str(f), sym_range, "def bar():\n    return 1", servers=[])
    content = f.read_text(encoding="utf-8")
    assert "def bar():" in content
    assert "def foo():" not in content


def test_apply_edit_multi_byte(tmp_path: Path) -> None:
    # Line 0: "🎉 = 1\n"
    # The emoji is 1 code point but 2 UTF-16 units.
    # We replace from char=2 (after emoji) to char=7 (end of " = 1")
    f = tmp_path / "emoji.py"
    f.write_text("🎉 = 1\nx = 2\n", encoding="utf-8")
    # Replace the second line entirely
    sym_range = SymbolRange(start_line=1, start_char=0, end_line=1, end_char=5)
    apply_edit(str(f), sym_range, "y = 99", servers=[])
    lines = f.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "🎉 = 1"
    assert lines[1] == "y = 99"


# ---------------------------------------------------------------------------
# apply_workspace_edit
# ---------------------------------------------------------------------------


def test_apply_workspace_edit_single_file(tmp_path: Path) -> None:
    f = tmp_path / "foo.py"
    f.write_text("def foo():\n    pass\n", encoding="utf-8")
    uri = f.as_uri()

    workspace_edit = {
        "changes": {
            uri: [
                {
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 7},
                    },
                    "newText": "bar",
                }
            ]
        }
    }
    changed = apply_workspace_edit(workspace_edit, servers=[])
    assert str(f) in changed
    content = f.read_text(encoding="utf-8")
    assert "def bar():" in content


def test_apply_workspace_edit_multi_occurrence_bottom_up(tmp_path: Path) -> None:
    """Multiple edits in the same file must be applied bottom-up."""
    f = tmp_path / "multi.py"
    f.write_text("foo foo foo\n", encoding="utf-8")
    uri = f.as_uri()

    workspace_edit = {
        "changes": {
            uri: [
                # Replace second "foo" (char 4-7)
                {
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 7},
                    },
                    "newText": "bar",
                },
                # Replace third "foo" (char 8-11)
                {
                    "range": {
                        "start": {"line": 0, "character": 8},
                        "end": {"line": 0, "character": 11},
                    },
                    "newText": "baz",
                },
            ]
        }
    }
    apply_workspace_edit(workspace_edit, servers=[])
    content = f.read_text(encoding="utf-8").strip()
    assert content == "foo bar baz"


def test_apply_workspace_edit_document_changes(tmp_path: Path) -> None:
    """documentChanges format is also handled."""
    f = tmp_path / "doc.py"
    f.write_text("old_name = 1\n", encoding="utf-8")
    uri = f.as_uri()

    workspace_edit = {
        "documentChanges": [
            {
                "textDocument": {"uri": uri, "version": 1},
                "edits": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 8},
                        },
                        "newText": "new_name",
                    }
                ],
            }
        ]
    }
    changed = apply_workspace_edit(workspace_edit, servers=[])
    assert str(f) in changed
    assert "new_name = 1" in f.read_text(encoding="utf-8")
