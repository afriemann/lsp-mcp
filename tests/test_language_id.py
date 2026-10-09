"""languageId in didOpen, and pull-diagnostics responses that must not read as clean."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lsp_mcp.dispatch.router import Dispatcher
from lsp_mcp.lsp.generic_server import GenericLanguageServer, language_id_for
from lsp_mcp.lsp.manager import ServerManager

from .test_generic_server import _make_mock_server_handler
from .test_router import _make_config, _make_entry

DIAG = {
    "range": {
        "start": {"line": 0, "character": 9},
        "end": {"line": 0, "character": 12},
    },
    "message": "invalid-assignment",
    "severity": 1,
}


@pytest.mark.parametrize(
    "name,expected",
    [
        ("a.py", "python"), ("a.pyi", "python"), ("a.ts", "typescript"),
        ("a.mts", "typescript"), ("a.tsx", "typescriptreact"), ("a.js", "javascript"),
        ("a.mjs", "javascript"), ("a.jsx", "javascriptreact"), ("a.go", "go"),
        ("a.c", "c"), ("a.cpp", "cpp"), ("a.html", "html"), ("a.unknown", "plaintext"),
    ],
)  # fmt: skip
def test_language_id_for(name: str, expected: str) -> None:
    assert language_id_for(name) == expected


_DEFAULT = object()


class Wire:
    """Stub handler replicating ty: it only analyses documents opened as python."""

    def __init__(self, tmp_path: Path, pull_reply=_DEFAULT) -> None:
        self.h = _make_mock_server_handler(
            {"capabilities": {"diagnosticProvider": {"identifier": "ty"}}}
        )
        self.language_ids: dict[str, str] = {}
        self.pull_reply = pull_reply
        self.h.notify.did_open_text_document.side_effect = lambda p: (
            self.language_ids.update(
                {p["textDocument"]["uri"]: p["textDocument"]["languageId"]}
            )
        )

        async def send_request(method, params=None):
            if method != "textDocument/diagnostic":
                return None
            if self.pull_reply is not _DEFAULT:
                return self.pull_reply
            uri = params["textDocument"]["uri"]
            if self.language_ids.get(uri) == "python":
                return {"kind": "full", "items": [DIAG]}
            return {"kind": "full", "items": []}  # what ty really answers otherwise

        self.h.send_request.side_effect = send_request
        self.tmp_path = tmp_path

    async def diagnostics(self, name: str = "bad.py"):
        f = self.tmp_path / name
        f.write_text('x: int = "a"\n')
        with patch(
            "multilspy.language_server.LanguageServerHandler", return_value=self.h
        ):
            server = GenericLanguageServer(["x"], str(self.tmp_path))
            async with server.start_server():
                m = MagicMock(spec=ServerManager)
                entry = _make_entry({}, server)

                async def acq(spec, path):
                    return entry

                m.acquire = acq
                m.failure_reason = lambda n, p: None
                d = Dispatcher(_make_config(("*.py", ["s1"])), m)
                return await d.get_diagnostics_for_file(str(f)), server


async def test_python_file_opened_with_python_language_id(tmp_path: Path) -> None:
    w = Wire(tmp_path)
    r, _ = await w.diagnostics()
    assert list(w.language_ids.values()) == ["python"]
    assert [d.message for d in r.diagnostics] == ["invalid-assignment"]


@pytest.mark.parametrize(
    "reply",
    [
        None,
        {"kind": "unchanged", "resultId": "1"},
        {"kind": "full"},
        {"kind": "full", "items": "nope"},
        "garbage",
        [],  # a bare empty list is a valid (clean) answer, see below
    ],
)
async def test_unparsable_pull_reply_is_unknown_not_clean(
    tmp_path: Path, reply
) -> None:
    r, _ = await Wire(tmp_path, pull_reply=reply).diagnostics()
    if reply == []:
        assert r.note == ""
    else:
        assert r.diagnostics == [] and "not clean" in r.note


async def test_explicit_empty_full_report_is_clean(tmp_path: Path) -> None:
    r, _ = await Wire(tmp_path, pull_reply={"kind": "full", "items": []}).diagnostics()
    assert r.diagnostics == [] and r.note == ""
