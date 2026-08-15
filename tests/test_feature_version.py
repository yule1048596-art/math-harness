from __future__ import annotations

from math_harness.models import ExampleCreate, MathPayload, WorkspaceCreate
from math_harness.retrieval import MethodRetriever
from math_harness.service import MathHarnessService
from math_harness.structure import (
    FEATURE_VERSION,
    MethodSignature,
    features_from_text,
)

# 结构特征的版本戳。
#
# 方法卡的签名是**过去某个版本**的提取器算出来的。改了提取器而不改版本号，库里的旧
# 签名和新算出来的查询特征就不在同一个空间里——**而且不会报任何错**，只会让命中率
# 悄悄下降。
#
# 这一组测试补的是跨领域门禁看不见的盲区：那条门禁每次从语料重建整个库，两边永远同
# 版本，结构上不可能发现这件事。真正受损的是用户积累了几个月的存量工作区。


def workspace_with_method(tmp_path):
    """建一个工作区，让它长出一张带结构签名的方法卡。"""

    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="版本戳"))
    service.ingest_example(
        workspace.id,
        ExampleCreate(
            problem="求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开到 O(x^-2)",
            solution="先乘共轭式有理化，再令 t=1/x，并使用泰勒展开，得到 1/2-1/(8x)+O(x^-2)。",
            tags=["渐进估计", "根式"],
            reviewed=True,
            math_payload=MathPayload(
                expression="sqrt(x**2 + x) - x",
                expected="1/2 - 1/(8*x)",
                variable="x",
                point="oo",
                remainder_power=2,
            ),
        ),
    )
    return service, workspace.id


def stored_signature(service, workspace_id):
    method = service.list_methods(workspace_id)[0]
    return MethodSignature.model_validate(method.signature or {})


# --- 版本戳本身 -------------------------------------------------------


def test_a_new_signature_carries_the_current_version(tmp_path):
    service, workspace_id = workspace_with_method(tmp_path)

    assert stored_signature(service, workspace_id).feature_version == FEATURE_VERSION


def test_a_signature_without_the_field_counts_as_unknown():
    """v0.20 之前存的卡片没有这个字段，而它们确实可能来自任何一个旧版本。

    当成未知比当成当前版本安全：检索会跳过它的结构分，而不是拿两个不同空间里的
    向量去比。
    """

    assert MethodSignature().feature_version == 0
    assert not MethodSignature().is_current


def test_mixing_versions_invalidates_the_whole_signature():
    """往旧签名上叠加新特征，得到的是两个版本的混合物。它必须整体失效等重建，
    不能因为最新一次累加就自称当前版本。"""

    legacy = MethodSignature(paths={"old/path": 1}, sample_count=1)
    mixed = legacy.accumulate(features_from_text("求 x**2 的导数"))

    assert not mixed.is_current

    merged = MethodSignature(feature_version=FEATURE_VERSION).combined_with(legacy)
    assert not merged.is_current


# --- 检索：不同版本绝不放在一起比 -------------------------------------


def test_a_stale_signature_does_not_contribute_a_structure_score(tmp_path):
    service, workspace_id = workspace_with_method(tmp_path)
    methods = service.list_methods(workspace_id)
    features = features_from_text("求 sqrt(x^2+3*x)-x 在无穷远的展开")
    retriever = MethodRetriever()

    fresh = retriever.search(methods, "求导", features=features)
    assert fresh
    assert any("数学结构契合度" in reason for reason in fresh[0].reasons)

    stale = [
        method.model_copy(
            update={"signature": {**method.signature, "feature_version": 0}}
        )
        for method in methods
    ]
    result = retriever.search(stale, "求导", features=features)

    assert result
    assert all(
        "数学结构契合度" not in reason for match in result for reason in match.reasons
    )


def test_retrieval_still_works_when_every_signature_is_stale(tmp_path):
    """降级的判据是**还能检索**。版本对不上不是错误，是少一路信号。"""

    service, workspace_id = workspace_with_method(tmp_path)
    stale = [
        method.model_copy(update={"signature": {"feature_version": 0}})
        for method in service.list_methods(workspace_id)
    ]

    result = MethodRetriever().search(
        stale,
        "求根式之差的展开",
        features=features_from_text("求 sqrt(x^2+3*x)-x 的展开"),
    )

    assert result
    assert result[0].score > 0


# --- 重建 -------------------------------------------------------------


def test_rebuilding_restores_the_structure_score(tmp_path):
    service, workspace_id = workspace_with_method(tmp_path)
    store = service.workspaces.store(workspace_id)
    method = service.list_methods(workspace_id)[0]
    before = MethodSignature.model_validate(method.signature or {})
    store.replace_method_signature(
        method.id, before.model_copy(update={"feature_version": 0})
    )

    assert service.stale_signature_count(workspace_id) == 1
    assert service.rebuild_method_signatures(workspace_id) == 1
    assert service.stale_signature_count(workspace_id) == 0

    after = stored_signature(service, workspace_id)
    assert after.is_current
    # 重建是从来源例题重新算的，结果必须和当初累积出来的一致。
    assert after.paths == before.paths
    assert after.sample_count == before.sample_count


def test_rebuilding_does_not_bump_the_card_version(tmp_path):
    """签名是派生数据，重建它不是一次知识修订——卡片说了什么一个字没变。"""

    service, workspace_id = workspace_with_method(tmp_path)
    store = service.workspaces.store(workspace_id)
    method = service.list_methods(workspace_id)[0]
    store.replace_method_signature(method.id, MethodSignature())

    service.rebuild_method_signatures(workspace_id)

    assert service.list_methods(workspace_id)[0].version == method.version


def test_rebuilding_a_current_workspace_changes_nothing(tmp_path):
    service, workspace_id = workspace_with_method(tmp_path)

    assert service.stale_signature_count(workspace_id) == 0
    assert service.rebuild_method_signatures(workspace_id) == 0


# --- API ---------------------------------------------------------------


def test_the_api_reports_and_rebuilds_stale_signatures(tmp_path):
    from fastapi.testclient import TestClient

    from math_harness.api import create_app

    app = create_app(tmp_path)
    service = app.state.service
    client = TestClient(app)
    workspace = client.post("/workspaces", json={"name": "版本戳"}).json()
    client.post(
        f"/workspaces/{workspace['id']}/examples",
        json={
            "problem": "求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开到 O(x^-2)",
            "solution": "先乘共轭式有理化，再令 t=1/x，得到 1/2-1/(8x)+O(x^-2)。",
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
    store = service.workspaces.store(workspace["id"])
    for method in service.list_methods(workspace["id"]):
        store.replace_method_signature(method.id, MethodSignature())

    health = client.get(f"/workspaces/{workspace['id']}/methods/signature-health")
    assert health.json()["stale"] > 0
    assert health.json()["feature_version"] == FEATURE_VERSION

    rebuilt = client.post(f"/workspaces/{workspace['id']}/methods/signature-rebuild")
    assert rebuilt.json()["rebuilt"] > 0
    assert (
        client.get(f"/workspaces/{workspace['id']}/methods/signature-health").json()[
            "stale"
        ]
        == 0
    )
