from __future__ import annotations

import json
import stat

from fastapi.testclient import TestClient

from math_harness.api import create_app
from math_harness.server import _watch_parent, _write_ready_file, build_parser


def test_local_token_protects_every_route(tmp_path):
    client = TestClient(create_app(tmp_path, local_token="app-secret"))

    assert client.get("/health").status_code == 401
    assert (
        client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )

    response = client.get(
        "/health",
        headers={"Authorization": "Bearer app-secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.8.0"}


def test_unconfigured_api_remains_backwards_compatible(tmp_path, monkeypatch):
    monkeypatch.delenv("MATH_HARNESS_LOCAL_TOKEN", raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get("/health").status_code == 200


def test_ready_file_is_private_and_contains_connection_metadata(tmp_path):
    ready_file = tmp_path / "runtime" / "ready.json"
    _write_ready_file(ready_file, 54321)

    payload = json.loads(ready_file.read_text(encoding="utf-8"))
    assert payload["base_url"] == "http://127.0.0.1:54321"
    assert payload["port"] == 54321
    assert payload["version"] == "0.8.0"
    assert stat.S_IMODE(ready_file.stat().st_mode) == 0o600


def test_server_cli_defaults_to_an_ephemeral_loopback_port():
    args = build_parser().parse_args([])
    assert args.port == 0
    assert args.ready_file is None
    assert args.parent_pid is None


def test_parent_watcher_stops_orphaned_backend(monkeypatch):
    class FakeServer:
        should_exit = False

    server = FakeServer()

    def missing_process(_, __):
        raise ProcessLookupError

    monkeypatch.setattr("math_harness.server.os.kill", missing_process)
    _watch_parent(123, server)  # type: ignore[arg-type]
    assert server.should_exit is True
