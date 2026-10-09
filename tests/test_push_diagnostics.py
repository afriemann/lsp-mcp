"""Push diagnostics must reflect the document state of *this* call."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lsp_mcp.__main__ import main
from lsp_mcp.dispatch.router import Dispatcher
from lsp_mcp.lsp.generic_server import GenericLanguageServer
from lsp_mcp.lsp.manager import ServerManager

from .test_generic_server import _make_mock_server_handler
from .test_router import _make_config, _make_entry


def _diag(msg: str = "BAD", line: int = 0) -> dict:
    return {
        "range": {
            "start": {"line": line, "character": 0},
            "end": {"line": line, "character": 3},
        },
        "message": msg,
        "severity": 1,
    }


class Env:
    """A real GenericLanguageServer on a stub handler that publishes on open/close."""

    def __init__(self, tmp_path: Path, on_open=None, on_close=None) -> None:
        self.tmp_path = tmp_path
        self.handler = _make_mock_server_handler({"capabilities": {}})
        self.callbacks: dict[str, object] = {}
        self.handler.on_notification.side_effect = lambda method, cb: (
            self.callbacks.__setitem__(method, cb)
        )
        self.on_open = on_open or self.default_on_open
        self.on_close = on_close or (lambda uri: [(0.01, uri, [], None)])
        self.opens = 0
        self.handler.notify.did_open_text_document.side_effect = self._did_open
        self.handler.notify.did_close_text_document.side_effect = self._did_close

    @staticmethod
    def default_on_open(uri, text, version):
        diags = [_diag(line=i) for i, ln in enumerate(text.splitlines()) if "BAD" in ln]
        return [(0.03, uri, diags, version)]

    def _publish(self, plan) -> None:
        loop = asyncio.get_running_loop()
        for delay, uri, diags, version in plan:
            params = {"uri": uri, "diagnostics": diags}
            if version is not None:
                params["version"] = version
            loop.call_later(
                delay,
                lambda p=params: asyncio.ensure_future(
                    self.callbacks["textDocument/publishDiagnostics"](p)
                ),
            )

    def _did_open(self, params) -> None:
        self.opens += 1
        td = params["textDocument"]
        self._publish(self.on_open(td["uri"], td["text"], td["version"]))

    def _did_close(self, params) -> None:
        self._publish(self.on_close(params["textDocument"]["uri"]))

    async def run(self, body, **dispatcher_kw):
        f = self.tmp_path / "app.py"
        if not f.exists():
            f.write_text("BAD\n")
        with patch(
            "multilspy.language_server.LanguageServerHandler", return_value=self.handler
        ):
            server = GenericLanguageServer(["x"], str(self.tmp_path))
            async with server.start_server():
                manager = MagicMock(spec=ServerManager)
                entry = _make_entry({}, server)  # one entry => one shared lock

                async def _acq(spec, path):
                    return entry

                manager.acquire = _acq
                manager.failure_reason = lambda n, p: None
                dispatcher_kw.setdefault("diag_quiet_period", 0.1)
                dispatcher_kw.setdefault("diag_settle_timeout", 1.0)
                d = Dispatcher(_make_config(("*.py", ["s1"])), manager, **dispatcher_kw)
                return await body(d, f, server)


async def test_edit_between_calls_returns_fresh_results(tmp_path: Path) -> None:
    async def body(d, f, server):
        r1 = await d.get_diagnostics_for_file(str(f))
        f.write_text("fine\n")
        r2 = await d.get_diagnostics_for_file(str(f))
        f.write_text("BAD\nBAD\n")
        r3 = await d.get_diagnostics_for_file(str(f))
        return r1, r2, r3

    r1, r2, r3 = await Env(tmp_path).run(body)
    assert len(r1.diagnostics) == 1
    assert r2.diagnostics == [] and r2.note == ""  # genuinely clean
    assert len(r3.diagnostics) == 2


async def test_stale_cache_and_set_event_are_reset_after_open(tmp_path: Path) -> None:
    async def body(d, f, server):
        uri = f.resolve().as_uri()
        # leftovers from an earlier publish (e.g. the document was kept open
        # by someone else): must not be mistaken for this call's result
        server._push_diagnostics[uri] = [_diag("stale")]
        ev = asyncio.Event()
        ev.set()
        server._diag_event[uri] = ev
        return await d.get_diagnostics_for_file(str(f))

    (tmp_path / "app.py").write_text("fine\n")
    # the real publish comes later than the quiet period, so only a reset
    # prevents the stale leftovers from being returned first
    env = Env(tmp_path, on_open=lambda uri, t, v: [(0.3, uri, [], v)])
    r = await env.run(body, diag_settle_timeout=1.0)
    assert r.diagnostics == [] and r.note == ""  # the fresh (empty) publish wins


async def test_close_publish_does_not_make_next_call_falsely_clean(
    tmp_path: Path,
) -> None:
    async def body(d, f, server):
        r1 = await d.get_diagnostics_for_file(str(f))
        await asyncio.sleep(0.05)  # the empty publish caused by didClose lands here
        r2 = await d.get_diagnostics_for_file(str(f))
        return r1, r2

    r1, r2 = await Env(tmp_path).run(body)
    assert len(r1.diagnostics) == 1
    assert len(r2.diagnostics) == 1


async def test_last_publish_within_quiet_period_wins(tmp_path: Path) -> None:
    def on_open(uri, text, version):
        # syntax pass (empty) first, semantic pass shortly after
        return [(0.02, uri, [], version), (0.08, uri, [_diag("semantic")], version)]

    async def body(d, f, server):
        return await d.get_diagnostics_for_file(str(f))

    r = await Env(tmp_path, on_open=on_open).run(body, diag_quiet_period=0.2)
    assert [x.message for x in r.diagnostics] == ["semantic"]


async def test_version_mismatch_discarded(tmp_path: Path) -> None:
    def on_open(uri, text, version):
        return [
            (0.02, uri, [_diag("stale")], version + 7),
            (0.05, uri, [_diag("fresh")], version),
        ]

    async def body(d, f, server):
        return await d.get_diagnostics_for_file(str(f))

    r = await Env(tmp_path, on_open=on_open).run(body)
    assert [x.message for x in r.diagnostics] == ["fresh"]


async def test_only_mismatched_version_is_unknown_not_clean(tmp_path: Path) -> None:
    def on_open(uri, text, version):
        return [(0.02, uri, [], version + 1)]

    async def body(d, f, server):
        return await d.get_diagnostics_for_file(str(f))

    r = await Env(tmp_path, on_open=on_open).run(body, diag_settle_timeout=0.2)
    assert r.diagnostics == [] and "not clean" in r.note


async def test_no_publish_is_unknown_not_clean(tmp_path: Path) -> None:
    async def body(d, f, server):
        return await d.get_diagnostics_for_file(str(f))

    r = await Env(tmp_path, on_open=lambda *a: []).run(body, diag_settle_timeout=0.2)
    assert r.diagnostics == [] and "published" in r.note and "not clean" in r.note


async def test_explicit_empty_publish_is_clean(tmp_path: Path) -> None:
    async def body(d, f, server):
        return await d.get_diagnostics_for_file(str(f))

    r = await Env(tmp_path, on_open=lambda uri, t, v: [(0.02, uri, [], v)]).run(body)
    assert r.diagnostics == [] and r.note == ""


async def test_concurrent_calls_on_same_file(tmp_path: Path) -> None:
    async def body(d, f, server):
        return await asyncio.gather(
            *[d.get_diagnostics_for_file(str(f)) for _ in range(4)]
        )

    env = Env(tmp_path)
    results = await env.run(body)
    for r in results:
        assert len(r.diagnostics) == 1 and r.note == ""
    assert env.opens == 4  # serialised: one open per call


async def test_per_uri_state_dropped_after_call(tmp_path: Path) -> None:
    async def body(d, f, server):
        await d.get_diagnostics_for_file(str(f))
        await asyncio.sleep(0.05)
        return server

    server = await Env(tmp_path).run(body)
    assert server.push_diagnostics == {}
    assert server._diag_event == {}


async def test_call_deadline_still_wins_and_cleans_up(tmp_path: Path) -> None:
    async def body(d, f, server):
        r = await d.get_diagnostics_for_file(str(f))
        return r, server

    (tmp_path / "app.py").write_text("BAD\n")
    r, server = await Env(tmp_path, on_open=lambda *a: []).run(
        body, call_deadline=0.1, diag_settle_timeout=5
    )
    assert "deadline" in r.note
    assert server._diag_event == {} and server.push_diagnostics == {}
    assert server.open_file_buffers == {}  # buffer released, didClose sent


# --- generic-server level ------------------------------------------------------


async def test_publish_for_unopened_uri_ignored(tmp_path: Path) -> None:
    env = Env(tmp_path)
    (tmp_path / "app.py").write_text("x\n")
    with patch(
        "multilspy.language_server.LanguageServerHandler", return_value=env.handler
    ):
        server = GenericLanguageServer(["x"], str(tmp_path))
        async with server.start_server():
            cb = env.callbacks["textDocument/publishDiagnostics"]
            await cb({"uri": "file:///never-opened.py", "diagnostics": [_diag()]})
            assert server.push_diagnostics == {}


# --- CLI / env -------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "-1", "nan", "abc"])
def test_quiet_period_flag_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setattr(sys, "argv", ["lsp-mcp", "--diagnostics-quiet-period", value])
    with pytest.raises(SystemExit) as ei:
        main()
    assert ei.value.code == 2


def test_quiet_period_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from lsp_mcp import server

    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "servers:\n  s:\n    command: [echo]\nfile_handlers:\n  '*.py':\n    - server: s\n"
    )
    captured: dict = {}
    real = server.Dispatcher

    class D(real):
        def __init__(self, *a, **k):
            captured["q"] = k["diag_quiet_period"]
            super().__init__(*a, **k)

    monkeypatch.setattr(server, "Dispatcher", D)
    monkeypatch.delenv("LSP_MCP_DIAGNOSTICS_QUIET_PERIOD", raising=False)
    server.build_app(config_path=str(cfg))
    assert captured["q"] == 0.3
    monkeypatch.setenv("LSP_MCP_DIAGNOSTICS_QUIET_PERIOD", "0.7")
    server.build_app(config_path=str(cfg))
    assert captured["q"] == 0.7
    monkeypatch.setenv("LSP_MCP_DIAGNOSTICS_QUIET_PERIOD", "0")
    server.build_app(config_path=str(cfg))
    assert captured["q"] == 0.3


# --- review round: nested open, buffer release on failure, URI equality -----


async def test_already_open_document_keeps_cache_and_is_not_reset(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)

    async def body(d, f, server):
        uri = f.resolve().as_uri()
        rel = f.name
        with server.open_file(rel):  # outer open: ref-count only for our call
            assert env.opens == 1
            await env.callbacks["textDocument/publishDiagnostics"](
                {"uri": uri, "diagnostics": [_diag("held")]}
            )
            r = await asyncio.wait_for(d.get_diagnostics_for_file(str(f)), 1.0)
            assert env.opens == 1  # nested: no second didOpen
            assert [x.message for x in r.diagnostics] == ["held"]
            assert uri in server.push_diagnostics  # cache not wiped on exit
            assert uri in server.open_file_buffers
        assert uri not in server.open_file_buffers

    await env.run(body)


async def test_deadline_expiry_releases_buffer_and_next_call_is_fresh(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path, on_open=lambda *a: [])

    async def body(d, f, server):
        uri = f.resolve().as_uri()
        r = await d.get_diagnostics_for_file(str(f))
        assert "deadline" in r.note
        assert uri not in server.open_file_buffers
        assert server.open_file_buffers == {}
        assert env.handler.notify.did_close_text_document.call_count == 1
        env.on_open = Env.default_on_open
        d._call_deadline = 5
        r2 = await d.get_diagnostics_for_file(str(f))
        assert len(r2.diagnostics) == 1 and r2.note == ""
        assert env.opens == 2  # a real didOpen, not the nested branch

    await env.run(body, call_deadline=0.1, diag_settle_timeout=5)


async def test_outer_cancellation_releases_buffer(tmp_path: Path) -> None:
    env = Env(tmp_path, on_open=lambda *a: [])

    async def body(d, f, server):
        t = asyncio.create_task(d.get_diagnostics_for_file(str(f)))
        await asyncio.sleep(0.1)
        assert server.open_file_buffers  # waiting inside the open document
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert server.open_file_buffers == {}
        assert server.push_diagnostics == {} and server._diag_event == {}

    await env.run(body, diag_settle_timeout=5, call_deadline=30)


async def test_open_file_releases_on_exception_and_nested_refcount(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)
    (tmp_path / "app.py").write_text("x\n")

    async def body(d, f, server):
        uri = f.resolve().as_uri()
        with pytest.raises(RuntimeError):
            with server.open_file("app.py"):
                raise RuntimeError("boom")
        assert uri not in server.open_file_buffers
        # nested: inner failure releases only the inner reference
        with server.open_file("app.py"):
            with pytest.raises(RuntimeError):
                with server.open_file("app.py"):
                    raise RuntimeError("inner")
            assert server.open_file_buffers[uri].ref_count == 1
        assert uri not in server.open_file_buffers
        assert env.handler.notify.did_open_text_document.call_count == 2
        assert env.handler.notify.did_close_text_document.call_count == 2

    await env.run(body)


async def test_symlinked_and_unnormalised_paths_get_fresh_diagnostics(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "app.py").write_text("BAD\n")
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    env = Env(real)

    async def body(d, f, server):
        a = await d.get_diagnostics_for_file(str(tmp_path / "link" / "app.py"))
        b = await d.get_diagnostics_for_file(str(real / ".." / "real" / "app.py"))
        assert server.document_uri("app.py") == (real / "app.py").resolve().as_uri()
        return a, b

    a, b = await env.run(body)
    assert len(a.diagnostics) == 1 and a.note == ""
    assert len(b.diagnostics) == 1 and b.note == ""
