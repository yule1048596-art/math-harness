from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from math_harness.api import create_app
from math_harness.models import ProviderTestRequest
from math_harness.provider_probe import probe_provider

SECRET = "sk-super-secret-key-do-not-leak"


class _FakeClient:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict] = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


def _request(**overrides) -> ProviderTestRequest:
    base = {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": SECRET,
    }
    return ProviderTestRequest(**{**base, **overrides})


def test_successful_probe_reports_latency_and_echo():
    client = _FakeClient(SimpleNamespace(model="deepseek-chat", output_text="ok"))

    result = probe_provider(_request(), client=client)

    assert result.ok
    assert result.model == "deepseek-chat"
    assert result.sample == "ok"
    assert result.error is None
    assert result.duration_ms >= 0


def test_probe_does_not_persist_the_response():
    client = _FakeClient(SimpleNamespace(model="m", output_text="ok"))

    probe_provider(_request(), client=client)

    assert client.calls[0]["store"] is False
    # 一次「测试」不该因为传输层重试变成多次计费请求。
    assert client.calls[0]["max_output_tokens"] <= 32


def test_probe_never_returns_the_api_key():
    client = _FakeClient(SimpleNamespace(model="m", output_text=SECRET))

    result = probe_provider(_request(), client=client)

    assert SECRET not in result.model_dump_json()


def test_auth_failure_is_translated_to_actionable_text():
    client = _FakeClient(error=RuntimeError("Error code: 401 - authentication failed"))

    result = probe_provider(_request(), client=client)

    assert not result.ok
    assert "密钥" in (result.error or "")
    assert SECRET not in (result.error or "")


def test_connection_failure_is_translated():
    client = _FakeClient(error=RuntimeError("Connection error while reaching host"))

    result = probe_provider(_request(), client=client)

    assert not result.ok
    assert "无法连接" in (result.error or "")


def test_rate_limit_is_distinguished_from_bad_key():
    client = _FakeClient(error=RuntimeError("Error code: 429 rate limit exceeded"))

    result = probe_provider(_request(), client=client)

    assert not result.ok
    assert "限流" in (result.error or "")


def test_error_text_never_leaks_the_key():
    # SDK 异常里有时会带上请求详情，必须确认不会原样吐回去。
    client = _FakeClient(error=RuntimeError(f"boom with {SECRET} inside"))

    result = probe_provider(_request(), client=client)

    assert not result.ok
    assert SECRET not in json.dumps(result.model_dump(), ensure_ascii=False)


def test_endpoint_rejects_non_http_base_url(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/providers/test",
        json={"base_url": "ftp://x", "model": "m"},
    )

    assert response.status_code == 422


def test_health_reports_provider_config_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MATH_HARNESS_PROVIDERS", "{broken json")
    client = TestClient(create_app(tmp_path))

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["provider_config_error"]


def test_health_has_no_error_when_config_is_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("MATH_HARNESS_PROVIDERS", raising=False)
    client = TestClient(create_app(tmp_path))

    assert client.get("/health").json()["provider_config_error"] is None
