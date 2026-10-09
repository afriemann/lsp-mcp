"""CLI validation for timeout options."""

from __future__ import annotations

import sys

import pytest

from lsp_mcp.__main__ import main


@pytest.mark.parametrize(
    "flag", ["--request-timeout", "--start-timeout", "--call-deadline"]
)
@pytest.mark.parametrize("value", ["0", "-1", "nan", "abc"])
def test_non_positive_timeouts_rejected(
    monkeypatch: pytest.MonkeyPatch, flag: str, value: str
) -> None:
    monkeypatch.setattr(sys, "argv", ["lsp-mcp", flag, value])
    with pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 2
