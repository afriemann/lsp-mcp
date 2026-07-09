"""UTF-16 code unit offset conversion for LSP position handling.

LSP uses UTF-16 code unit offsets for character positions.  A Python str is
UTF-32 (one code point per element), so characters in the Basic Multilingual
Plane map 1:1, but non-BMP characters (code point > U+FFFF, e.g. emoji) are
encoded as a surrogate pair in UTF-16 and therefore count as *two* units.

The helper in this module converts an (line, character) LSP position to a
Python string index correctly for any Unicode content.
"""

from __future__ import annotations


def position_to_index(text: str, line: int, character: int) -> int:
    """
    Convert an LSP ``{line, character}`` position to a Python string index.

    *line* is 0-based; *character* is a UTF-16 code unit offset from the
    start of that line.

    Raises ``ValueError`` if *line* is out of range.
    """
    lines = text.split("\n")
    if line < 0 or line >= len(lines):
        raise ValueError(f"Line {line} out of range (file has {len(lines)} lines)")

    # Index of the first character of the target line in the full text.
    line_start_index = sum(len(lines[i]) + 1 for i in range(line))

    line_text = lines[line]
    # Walk the line counting UTF-16 code units until we reach *character*.
    utf16_units = 0
    py_index = 0
    while py_index < len(line_text) and utf16_units < character:
        cp = ord(line_text[py_index])
        # Code points > 0xFFFF use a surrogate pair in UTF-16 (2 units).
        utf16_units += 2 if cp > 0xFFFF else 1
        py_index += 1

    return line_start_index + py_index


def index_to_position(text: str, index: int) -> tuple[int, int]:
    """
    Convert a Python string index to an LSP ``{line, character}`` position.

    Returns ``(line, character)`` where *character* is a UTF-16 code unit
    offset.  Useful when converting edit positions back to LSP form.
    """
    if index < 0 or index > len(text):
        raise ValueError(f"Index {index} out of range for text of length {len(text)}")

    prefix = text[:index]
    lines = prefix.split("\n")
    line = len(lines) - 1
    last_line = lines[-1]

    # Count UTF-16 units on the last line.
    character = sum(2 if ord(c) > 0xFFFF else 1 for c in last_line)
    return line, character
