"""Tests for UTF-16 offset conversion helper."""

from __future__ import annotations

import pytest

from lsp_mcp.dispatch.offsets import index_to_position, position_to_index


# ---------------------------------------------------------------------------
# position_to_index
# ---------------------------------------------------------------------------


def test_ascii_single_line() -> None:
    text = "hello world"
    assert position_to_index(text, 0, 0) == 0
    assert position_to_index(text, 0, 5) == 5
    assert position_to_index(text, 0, 11) == 11


def test_ascii_multi_line() -> None:
    text = "line1\nline2\nline3"
    # line 0: "line1" (5 chars + newline)
    assert position_to_index(text, 0, 0) == 0
    assert position_to_index(text, 0, 5) == 5
    # line 1 starts at index 6
    assert position_to_index(text, 1, 0) == 6
    assert position_to_index(text, 1, 5) == 11
    # line 2 starts at index 12
    assert position_to_index(text, 2, 0) == 12


def test_emoji_start_of_line() -> None:
    # 🎉 is U+1F389, which is 2 UTF-16 code units.
    # After the emoji (character=2 in UTF-16) we should be at py index 1.
    text = "🎉hello"
    assert position_to_index(text, 0, 0) == 0
    # character=2 means after the emoji (1 code point, 2 UTF-16 units)
    assert position_to_index(text, 0, 2) == 1
    # character=3 means 'h' (index 1) + 1 BMP char
    assert position_to_index(text, 0, 3) == 2


def test_cjk_chars() -> None:
    # CJK chars are in BMP (U+4E00–U+9FFF), 1 UTF-16 unit each.
    text = "日本語"
    assert position_to_index(text, 0, 0) == 0
    assert position_to_index(text, 0, 1) == 1
    assert position_to_index(text, 0, 3) == 3


def test_mixed_emoji_and_ascii() -> None:
    # "a🎉b" — emoji at index 1 (2 UTF-16 units)
    # char 0 → index 0 ('a')
    # char 1 → index 1 ('🎉' start; emoji = 2 units)
    # char 3 → index 2 ('b', after emoji)
    text = "a🎉b"
    assert position_to_index(text, 0, 0) == 0
    assert position_to_index(text, 0, 1) == 1
    assert position_to_index(text, 0, 3) == 2


def test_line_out_of_range() -> None:
    with pytest.raises(ValueError, match="out of range"):
        position_to_index("hello", 5, 0)


def test_character_at_end_of_line() -> None:
    text = "abc\ndef"
    assert position_to_index(text, 0, 3) == 3  # end of first line
    assert position_to_index(text, 1, 3) == 7  # end of second line


# ---------------------------------------------------------------------------
# index_to_position
# ---------------------------------------------------------------------------


def test_index_to_position_basic() -> None:
    text = "line1\nline2"
    assert index_to_position(text, 0) == (0, 0)
    assert index_to_position(text, 5) == (0, 5)
    assert index_to_position(text, 6) == (1, 0)
    assert index_to_position(text, 11) == (1, 5)


def test_index_to_position_emoji() -> None:
    text = "🎉hi"
    assert index_to_position(text, 0) == (0, 0)
    # After emoji (1 code point) → character=2 in UTF-16
    assert index_to_position(text, 1) == (0, 2)
    assert index_to_position(text, 2) == (0, 3)


def test_roundtrip() -> None:
    text = "hello 🌍 world\nline2 test"
    for idx in [0, 1, 7, 8, 13, 14, 20]:
        line, char = index_to_position(text, idx)
        recovered = position_to_index(text, line, char)
        assert recovered == idx, f"roundtrip failed for index {idx}"
