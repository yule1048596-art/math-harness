from __future__ import annotations

from fastapi.testclient import TestClient

from math_harness.api import create_app


def test_api_vertical_slice(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["version"] == "0.14.0"

    response = client.post(
        "/workspaces",
        json={"name": "渐进估计", "description": "测试空间"},
    )
    assert response.status_code == 201
    workspace_id = response.json()["id"]

    response = client.post(
        f"/workspaces/{workspace_id}/math-target-drafts",
        json={"problem": "求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开到 O(x^-2)"},
    )
    assert response.status_code == 200
    assert response.json()["requires_confirmation"] is True
    assert response.json()["target"]["expression"] == "sqrt(x**2+x)-x"

    response = client.post(
        f"/workspaces/{workspace_id}/examples",
        json={
            "problem": "求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开到 O(x^-2)",
            "solution": "先有理化，再令 t=1/x，并做泰勒展开。",
            "tags": ["渐进估计", "根式"],
            "reviewed": True,
            "math_payload": {
                "expression": "sqrt(x**2 + x) - x",
                "expected": "1/2 - 1/(8*x)",
                "variable": "x",
                "point": "oo",
                "remainder_power": 2,
            },
        },
    )
    assert response.status_code == 201
    assert response.json()["example"]["verification"]["status"] == "verified"
    assert response.json()["example"]["extraction"]["status"] == "success"
    assert len(response.json()["learned_methods"]) == 3

    response = client.post(
        f"/workspaces/{workspace_id}/solve-plan",
        json={
            "query": "根式相减抵消时如何求无穷远渐进展开？",
            "tags": ["asymptotic", "radical"],
            "top_k": 3,
        },
    )
    assert response.status_code == 200
    assert response.json()["recommended_methods"]

    response = client.post(
        f"/workspaces/{workspace_id}/solve",
        json={
            "problem": "求 sqrt(x^2+x)-x 在无穷远处的渐进展开",
            "tags": ["radical", "cancellation"],
            "top_k": 3,
            "math_target": {
                "expression": "sqrt(x**2 + x) - x",
                "variable": "x",
                "point": "oo",
                "remainder_power": 2,
            },
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "verified"
    attempt_id = response.json()["id"]

    response = client.get(f"/workspaces/{workspace_id}/attempts/{attempt_id}")
    assert response.status_code == 200
    assert response.json()["generation"]["provider"] == "sympy"

    response = client.get(f"/workspaces/{workspace_id}/examples")
    assert response.status_code == 200
    captured = next(
        example
        for example in response.json()
        if example["source_attempt_id"] == attempt_id
    )
    assert captured["origin"] == "conversation"
    assert captured["status"] == "pending_review"

    response = client.post(f"/workspaces/{workspace_id}/attempts/{attempt_id}/capture")
    assert response.status_code == 200
    assert response.json()["created"] is False
    assert response.json()["example"]["id"] == captured["id"]

    response = client.patch(
        f"/workspaces/{workspace_id}/examples/{captured['id']}",
        json={
            "expected_revision": 1,
            "problem": captured["problem"],
            "solution": captured["solution"] + "\n人工确认余项阶数。",
            "tags": captured["tags"],
            "method_hint": captured["method_hint"],
            "math_payload": {
                "expression": "sqrt(x**2 + x) - x",
                "expected": "1/2 - 1/(8*x)",
                "variable": "x",
                "point": "oo",
                "remainder_power": 2,
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["example"]["revision"] == 2
    assert response.json()["example"]["verification"]["status"] == "verified"

    response = client.get(
        f"/workspaces/{workspace_id}/examples/{captured['id']}/versions"
    )
    assert response.status_code == 200
    assert [item["revision"] for item in response.json()] == [1]

    response = client.post(
        f"/workspaces/{workspace_id}/examples/{captured['id']}/review",
        json={
            "decision": "approve",
            "expected_revision": 2,
            "reviewer_note": "API smoke review",
        },
    )
    assert response.status_code == 200
    assert response.json()["example"]["status"] == "promoted"
    assert response.json()["example"]["reviewed"] is True

    response = client.post(
        f"/workspaces/{workspace_id}/attempts/{attempt_id}/corrections",
        json={
            "answer_text": "人工确认答案为 1/2 - 1/(8*x)。",
            "answer_expression": "1/2 - 1/(8*x)",
            "reviewer_note": "API correction smoke test",
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "verified"
    assert response.json()["correction_of"] == attempt_id

    response = client.get(f"/workspaces/{workspace_id}/attempts")
    assert response.status_code == 200
    assert len(response.json()) == 2

    response = client.post(
        f"/workspaces/{workspace_id}/evaluations",
        json={
            "name": "api-smoke",
            "top_k": 1,
            "cases": [
                {
                    "id": "radical",
                    "problem": "根式相减抵消",
                    "tags": ["radical", "cancellation"],
                    "expected_method_keys": ["rationalization"],
                }
            ],
        },
    )
    assert response.status_code == 201
    assert response.json()["metrics"]["hit_at_1"] == 1

    response = client.get(f"/workspaces/{workspace_id}/evaluations")
    assert response.status_code == 200
    assert len(response.json()) == 1

    response = client.post(
        f"/workspaces/{workspace_id}/solve-evaluations",
        json={
            "name": "solve-api-smoke",
            "top_k": 2,
            "cases": [
                {
                    "id": "radical-solve",
                    "problem": "根式相减的渐进展开",
                    "tags": ["radical"],
                    "math_target": {
                        "expression": "sqrt(x**2 + x) - x",
                        "point": "oo",
                        "remainder_power": 2,
                    },
                }
            ],
        },
    )
    assert response.status_code == 201
    assert response.json()["metrics"]["verified_rate"] == 1

    response = client.get(f"/workspaces/{workspace_id}/methods")
    method_id = next(
        method["id"] for method in response.json() if method["key"] == "rationalization"
    )
    response = client.patch(
        f"/workspaces/{workspace_id}/methods/{method_id}",
        json={"status": "deprecated"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "deprecated"

    response = client.get(f"/workspaces/{workspace_id}/learning-events")
    assert response.status_code == 200
    assert any(
        event["event_type"] == "evaluation_completed" for event in response.json()
    )
    assert any(
        event["event_type"] == "method_status_changed" for event in response.json()
    )


def test_review_api_enforces_verified_example_boundary(tmp_path):
    client = TestClient(create_app(tmp_path))
    workspace_id = client.post(
        "/workspaces",
        json={"name": "复核边界"},
    ).json()["id"]
    ingestion = client.post(
        f"/workspaces/{workspace_id}/examples",
        json={
            "problem": "解释一个尚未结构化的证明",
            "solution": "保留这个案例，之后再验证。",
        },
    )
    assert ingestion.status_code == 201
    example = ingestion.json()["example"]
    method = ingestion.json()["learned_methods"][0]

    response = client.post(
        f"/workspaces/{workspace_id}/examples/{example['id']}/review",
        json={"decision": "approve", "expected_revision": 1},
    )
    assert response.status_code == 409
    assert "独立数学验证" in response.json()["detail"]

    response = client.patch(
        f"/workspaces/{workspace_id}/methods/{method['id']}",
        json={"status": "promoted"},
    )
    assert response.status_code == 409
    assert "已验证例题" in response.json()["detail"]


def test_conversation_api_persists_multi_turn_chat_and_verified_solves(tmp_path):
    client = TestClient(create_app(tmp_path))
    workspace_id = client.post(
        "/workspaces",
        json={"name": "会话 API"},
    ).json()["id"]
    created = client.post(
        f"/workspaces/{workspace_id}/conversations",
        json={},
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    chat = client.post(
        f"/workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={"message": "你好", "turn_id": "api-chat-1"},
    )
    assert chat.status_code == 201
    assert chat.json()["assistant_message"]["role"] == "assistant"
    assert chat.json()["assistant_message"]["kind"] == "chat"

    solve = client.post(
        f"/workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={
            "message": "求 sqrt(x^2+x)-x 在无穷远的展开",
            "turn_id": "api-solve-1",
            "math_target": {
                "expression": "sqrt(x**2+x)-x",
                "point": "oo",
                "remainder_power": 2,
            },
        },
    )
    assert solve.status_code == 201
    payload = solve.json()
    assert payload["attempt"]["status"] == "verified"
    assert payload["knowledge_draft"]["status"] == "pending_review"
    assert payload["assistant_message"]["verification_status"] == "verified"

    repeated = client.post(
        f"/workspaces/{workspace_id}/conversations/{conversation_id}/turns",
        json={
            "message": "求 sqrt(x^2+x)-x 在无穷远的展开",
            "turn_id": "api-solve-1",
            "math_target": {
                "expression": "sqrt(x**2+x)-x",
                "point": "oo",
                "remainder_power": 2,
            },
        },
    )
    assert repeated.status_code == 201
    assert (
        repeated.json()["assistant_message"]["id"] == payload["assistant_message"]["id"]
    )

    messages = client.get(
        f"/workspaces/{workspace_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == 200
    assert [item["ordinal"] for item in messages.json()] == [1, 2, 3, 4]
