"""languageId in didOpen, and pull-diagnostics responses that must not read as clean."""

# spec: openspec/changes/fix-python-diagnostics-false-clean/specs/lsp-lifecycle/spec.md

from __future__ import annotations

import shutil
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
        ("a.PY", "python"), ("A.TSX", "typescriptreact"), ("a.cxx", "cpp"), ("a.c++", "cpp"),
        ("a.hh", "cpp"), ("a.hxx", "cpp"), ("a.jsonc", "jsonc"), ("a.scss", "scss"),
        ("a.less", "less"), ("a.md", "markdown"), ("a.yaml", "yaml"), ("a.yml", "yaml"),
        ("a.toml", "toml"), ("a.lua", "lua"), ("a.kt", "kotlin"), ("a.cs", "csharp"),
        ("a.php", "php"), ("a.swift", "swift"), ("a.vue", "vue"), ("a.svelte", "svelte"),
        ("a.tf", "terraform"), ("a.zig", "zig"), ("a.xml", "xml"),
        ("Makefile", "makefile"), ("makefile", "makefile"), ("Dockerfile", "dockerfile"),
        ("sub/dir/Dockerfile", "dockerfile"), ("Makefile.bak", "plaintext"),
        (".gitignore", "plaintext"), (".py", "plaintext"), ("noext", "plaintext"),
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
    ],
)
async def test_unparsable_pull_reply_is_unknown_not_clean(
    tmp_path: Path, reply
) -> None:
    r, _ = await Wire(tmp_path, pull_reply=reply).diagnostics()
    assert r.diagnostics == [] and "not clean" in r.note


async def test_bare_empty_list_pull_reply_is_clean(tmp_path: Path) -> None:
    r, _ = await Wire(tmp_path, pull_reply=[]).diagnostics()
    assert r.diagnostics == [] and r.note == ""


async def test_explicit_empty_full_report_is_clean(tmp_path: Path) -> None:
    r, _ = await Wire(tmp_path, pull_reply={"kind": "full", "items": []}).diagnostics()
    assert r.diagnostics == [] and r.note == ""


@pytest.mark.parametrize(
    "name,expected",
    [("a.tsx", "typescriptreact"), ("a.mts", "typescript"), ("A.PY", "python"),
     ("Dockerfile", "dockerfile"), ("a.jsx", "javascriptreact")],
)  # fmt: skip
async def test_did_open_language_id_through_real_open_file(
    tmp_path: Path, name: str, expected: str
) -> None:
    w = Wire(tmp_path)
    (tmp_path / name).write_text("x\n")
    with patch("multilspy.language_server.LanguageServerHandler", return_value=w.h):
        server = GenericLanguageServer(["x"], str(tmp_path))
        async with server.start_server():
            with server.open_file(name):
                assert list(w.language_ids.values()) == [expected]
                assert (
                    server.open_file_buffers[server.document_uri(name)].language_id
                    == expected
                )


async def test_still_open_buffer_keeps_original_language_id(tmp_path: Path) -> None:
    w = Wire(tmp_path)
    (tmp_path / "a.py").write_text("x\n")
    with patch("multilspy.language_server.LanguageServerHandler", return_value=w.h):
        server = GenericLanguageServer(["x"], str(tmp_path))
        async with server.start_server():
            with server.open_file("a.py"):
                with server.open_file("a.py"):
                    assert w.h.notify.did_open_text_document.call_count == 1
                buf = server.open_file_buffers[server.document_uri("a.py")]
                assert buf.ref_count == 1 and buf.language_id == "python"
                assert list(w.language_ids.values()) == ["python"]


@pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx/ty not available")
async def test_live_ty_reports_type_error(tmp_path: Path) -> None:
    """Smoke test against the real ty (skipped when uvx is missing)."""
    import asyncio

    from lsp_mcp.config.model import Config, FileHandler, ServerSpec

    (tmp_path / "pyproject.toml").write_text("")
    f = tmp_path / "bad.py"
    f.write_text('x: int = "a"\n')
    cfg = Config(
        servers={"ty": ServerSpec("ty", ("uvx", "ty", "server"))},
        handlers=(FileHandler("*.py", ("ty",)),),
    )
    m = ServerManager(start_timeout=60)
    try:
        r = await asyncio.wait_for(
            Dispatcher(cfg, m).get_diagnostics_for_file(str(f)), 90
        )
        if not r.diagnostics and "failed to start" in r.note:
            pytest.skip(f"ty unavailable: {r.note}")
        assert any("not assignable" in d.message for d in r.diagnostics)
    finally:
        await m.aclose()
