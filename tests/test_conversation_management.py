from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from math_harness.api import create_app
from math_harness.models import (
    ConversationCreate,
    ConversationStatus,
    ConversationTurnRequest,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService


@pytest.fixture
def workspace_with_history(tmp_path):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="会话管理"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="根式讨论")
    )
    for message in ("怎样处理根式相减", "无穷远处的展开该怎么做"):
        service.send_conversation_turn(
            workspace.id,
            conversation.id,
            ConversationTurnRequest(message=message),
        )
    return service, workspace, conversation


# --- 重命名 -----------------------------------------------------------


def test_rename_changes_the_title(workspace_with_history):
    service, workspace, conversation = workspace_with_history

    renamed = service.rename_conversation(workspace.id, conversation.id, "  渐进估计  ")

    assert renamed.title == "渐进估计"
    assert service.get_conversation(workspace.id, conversation.id).title == "渐进估计"


def test_rename_keeps_the_messages(workspace_with_history):
    service, workspace, conversation = workspace_with_history
    before = service.list_conversation_messages(workspace.id, conversation.id)

    service.rename_conversation(workspace.id, conversation.id, "新标题")

    assert len(service.list_conversation_messages(workspace.id, conversation.id)) == (
        len(before)
    )


# --- 归档 -------------------------------------------------------------


def test_archive_and_restore_round_trip(workspace_with_history):
    service, workspace, conversation = workspace_with_history

    archived = service.set_conversation_status(
        workspace.id, conversation.id, ConversationStatus.ARCHIVED
    )
    restored = service.set_conversation_status(
        workspace.id, conversation.id, ConversationStatus.ACTIVE
    )

    assert archived.status is ConversationStatus.ARCHIVED
    assert restored.status is ConversationStatus.ACTIVE


def test_conversations_start_active(workspace_with_history):
    _service, _workspace, conversation = workspace_with_history

    assert conversation.status is ConversationStatus.ACTIVE


def test_archiving_never_deletes_messages(workspace_with_history):
    """归档不删除——与人工纠正、方法卡版本快照同一条审计原则。"""

    service, workspace, conversation = workspace_with_history
    service.set_conversation_status(
        workspace.id, conversation.id, ConversationStatus.ARCHIVED
    )

    assert service.list_conversation_messages(workspace.id, conversation.id)


# --- 搜索 -------------------------------------------------------------


def test_search_finds_a_chinese_substring(workspace_with_history):
    service, workspace, _conversation = workspace_with_history

    hits = service.search_conversation_messages(workspace.id, "根式相减")

    assert hits
    assert all("根式相减" in hit.content for hit in hits)


def test_search_returns_nothing_for_a_blank_query(workspace_with_history):
    service, workspace, _conversation = workspace_with_history

    assert service.search_conversation_messages(workspace.id, "   ") == []


def test_search_misses_are_empty_not_errors(workspace_with_history):
    service, workspace, _conversation = workspace_with_history

    assert service.search_conversation_messages(workspace.id, "拉普拉斯鞍点") == []


def test_search_respects_the_limit(workspace_with_history):
    service, workspace, _conversation = workspace_with_history

    assert len(service.search_conversation_messages(workspace.id, "怎", limit=1)) <= 1


def test_search_is_workspace_scoped(tmp_path):
    service = MathHarnessService(tmp_path)
    first = service.create_workspace(WorkspaceCreate(name="甲"))
    second = service.create_workspace(WorkspaceCreate(name="乙"))
    conversation = service.create_conversation(first.id, ConversationCreate())
    service.send_conversation_turn(
        first.id,
        conversation.id,
        ConversationTurnRequest(message="只属于甲空间的内容"),
    )

    assert service.search_conversation_messages(first.id, "只属于甲空间")
    assert service.search_conversation_messages(second.id, "只属于甲空间") == []


def test_search_treats_wildcards_as_literal_text(workspace_with_history):
    """`%` 是 LIKE 的通配符；用户搜它时不该变成「匹配一切」。"""

    service, workspace, _conversation = workspace_with_history

    assert service.search_conversation_messages(workspace.id, "%") == []


# --- API --------------------------------------------------------------


def test_conversation_management_endpoints(tmp_path):
    client = TestClient(create_app(tmp_path))
    workspace = client.post("/workspaces", json={"name": "接口"}).json()
    conversation = client.post(
        f"/workspaces/{workspace['id']}/conversations", json={"title": "旧标题"}
    ).json()
    client.post(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/turns",
        json={"message": "共轭有理化怎么用"},
    )

    renamed = client.patch(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}",
        json={"title": "新标题"},
    )
    archived = client.put(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/status",
        params={"status": "archived"},
    )
    found = client.get(
        f"/workspaces/{workspace['id']}/conversations/search",
        params={"q": "共轭有理化"},
    )

    assert renamed.json()["title"] == "新标题"
    assert archived.json()["status"] == "archived"
    assert found.status_code == 200
    assert found.json()


def test_rename_rejects_a_blank_title(tmp_path):
    client = TestClient(create_app(tmp_path))
    workspace = client.post("/workspaces", json={"name": "空标题"}).json()
    conversation = client.post(
        f"/workspaces/{workspace['id']}/conversations", json={"title": "x"}
    ).json()

    response = client.patch(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}",
        json={"title": "   "},
    )

    assert response.status_code == 422
