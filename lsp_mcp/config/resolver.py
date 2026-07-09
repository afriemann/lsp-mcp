"""Resolve a file path to an ordered list of ServerSpecs."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from .model import Config, ServerSpec


def resolve(file_path: str | Path, config: Config) -> list[ServerSpec]:
    """
    Return the ordered list of ServerSpecs for *file_path*.

    Matching rules
    ──────────────
    - A ``*.ext`` style pattern (no directory separator) is matched against
      the *basename* of the file path.
    - A pattern containing ``/`` or ``**`` is matched against the full
      (possibly relative) path string.
    - When multiple handlers match, their server lists are concatenated in
      file order; duplicates are removed by keeping the first occurrence.
    - Returns an empty list when no handler matches.
    """
    path = Path(file_path)
    basename = path.name
    full = str(path)

    collected: list[str] = []
    seen: set[str] = set()

    for handler in config.handlers:
        pattern = handler.pattern
        # Decide whether to match against basename or full path
        if "/" in pattern or "**" in pattern:
            subject = full
        else:
            subject = basename

        if fnmatch.fnmatch(subject, pattern):
            for name in handler.server_names:
                if name not in seen:
                    seen.add(name)
                    collected.append(name)

    return [config.servers[name] for name in collected]
