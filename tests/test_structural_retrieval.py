from __future__ import annotations

from math_harness.models import (
    ExampleCreate,
    KnowledgeStatus,
    MathPayload,
    MethodCard,
    SolveMathTarget,
    WorkspaceCreate,
    utc_now,
)
from math_harness.retrieval import MethodRetriever
from math_harness.service import MathHarnessService
from math_harness.structure import (
    MethodSignature,
    StructuralFeatures,
    extract_features,
    signature_score,
)


def _target(**overrides) -> SolveMathTarget:
    base = {
        "expression": "sqrt(x**2 + x) - x",
        "variable": "x",
        "point": "oo",
        "mode": "asymptotic_expansion",
        "remainder_power": 2,
    }
    return SolveMathTarget(**{**base, **overrides})


def _card(key: str, signature: MethodSignature, **overrides) -> MethodCard:
    base = {
        "id": f"id-{key}",
        "workspace_id": "w",
        "key": key,
        "name": key,
        "goal": "目标",
        "applicable_when": [],
        "procedure": [],
        "failure_modes": [],
        "tags": [],
        "status": KnowledgeStatus.PROMOTED,
        "signature": signature.model_dump(),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    return MethodCard(**{**base, **overrides})


# --- 特征提取 -----------------------------------------------------------


def test_extracts_operators_flags_and_point_kind():
    features = extract_features(_target())

    assert features.mode == "asymptotic_expansion"
    assert features.point_kind == "pos_infinity"
    assert features.has_remainder is True
    assert "radical" in features.operators
    assert "radical_difference" in features.flags
    assert "cancellation_risk" in features.flags


def test_radical_difference_is_detected_without_an_explicit_minus_sign():
    # sqrt(x^2+2x)+x 在 x→-∞ 同样是 ∞-∞ 抵消；按符号判断会漏掉这一类。
    features = extract_features(_target(expression="sqrt(x**2+2*x)+x", point="-oo"))

    assert features.point_kind == "neg_infinity"
    assert "radical_difference" in features.flags


def test_classifies_point_and_transcendental_operators():
    features = extract_features(
        _target(expression="gamma(n+1)/n**n", variable="n", mode="limit", point="oo")
    )

    assert set(features.operators) >= {"gamma", "division", "symbolic_pow"}
    assert "factorial_or_gamma" in features.flags
    assert "exponential_power" in features.flags


def test_oscillatory_at_infinity_flag():
    features = extract_features(
        _target(expression="sin(x)", mode="asymptotic_equivalence", point="oo")
    )

    assert "oscillatory_at_infinity" in features.flags


def test_polynomial_is_marked_as_rational():
    features = extract_features(
        _target(expression="x**3+x**2", mode="asymptotic_equivalence", point="oo")
    )

    assert {"polynomial", "rational_function"} <= set(features.flags)


def test_math_payload_is_accepted_as_well():
    features = extract_features(
        MathPayload(
            expression="log(1+x)",
            expected="x",
            variable="x",
            point="0",
            mode="asymptotic_equivalence",
        )
    )

    assert features.point_kind == "zero"
    assert "log" in features.operators


def test_unparseable_expression_degrades_instead_of_raising():
    features = extract_features(_target(expression="zzz(x) ** unknown"))

    # 检索绝不能因为某个表达式形状古怪就整体失败。
    assert features.operators == []
    assert features.flags == []
    assert features.mode == "asymptotic_expansion"


def test_missing_target_yields_empty_features():
    features = extract_features(None)

    assert features.is_empty is True


# --- 签名累积与打分 -----------------------------------------------------


def test_signature_accumulates_counts():
    signature = MethodSignature()
    signature = signature.accumulate(extract_features(_target()))
    signature = signature.accumulate(
        extract_features(_target(expression="sqrt(x**2+5*x)-x"))
    )

    assert signature.sample_count == 2
    assert signature.operators["radical"] == 2
    assert signature.modes["asymptotic_expansion"] == 2


def test_empty_features_do_not_pollute_the_signature():
    signature = MethodSignature().accumulate(StructuralFeatures())

    assert signature.sample_count == 0


def test_cold_start_signature_scores_zero():
    assert signature_score(MethodSignature(), extract_features(_target())) == 0.0


def test_matching_signature_outscores_an_unrelated_one():
    radical = MethodSignature().accumulate(extract_features(_target()))
    factorial = MethodSignature().accumulate(
        extract_features(
            _target(
                expression="gamma(n+1)",
                variable="n",
                mode="asymptotic_equivalence",
                remainder_power=None,
            )
        )
    )
    query = extract_features(_target(expression="sqrt(x**2+7*x)-x"))

    assert signature_score(radical, query) > signature_score(factorial, query)


# --- 检索打分的两条路径 -------------------------------------------------


def test_without_features_scoring_is_unchanged():
    signature = MethodSignature().accumulate(extract_features(_target()))
    methods = [_card("rationalization", signature, tags=["asymptotic", "radical"])]
    retriever = MethodRetriever()

    legacy = retriever.search(methods, "根式相减的渐进展开", tags=["asymptotic"])
    explicitly_none = retriever.search(
        methods, "根式相减的渐进展开", tags=["asymptotic"], features=None
    )

    assert legacy[0].score == explicitly_none[0].score
    assert all("数学结构契合度" not in reason for reason in legacy[0].reasons)


def test_structure_reranks_when_a_target_is_supplied():
    retriever = MethodRetriever()
    radical_signature = MethodSignature().accumulate(extract_features(_target()))
    factorial_signature = MethodSignature().accumulate(
        extract_features(
            _target(
                expression="gamma(n+1)",
                variable="n",
                mode="asymptotic_equivalence",
                remainder_power=None,
            )
        )
    )
    # 让无关方法在词面上占优，只有结构信号能把正确方法顶到第一。
    methods = [
        _card("stirling", factorial_signature, name="根式 渐进 展开 抵消"),
        _card("rationalization", radical_signature, name="无关名称"),
    ]
    query = "根式 渐进 展开 抵消"

    without = retriever.search(methods, query)
    assert without[0].method.key == "stirling"

    with_structure = retriever.search(
        methods,
        query,
        features=extract_features(_target(expression="sqrt(x**2+9*x)-x")),
    )
    assert with_structure[0].method.key == "rationalization"
    assert any("数学结构契合度" in r for r in with_structure[0].reasons)


# --- 端到端 -------------------------------------------------------------


def test_ingested_examples_build_a_signature(tmp_path, verified_asymptotic_example):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    service.ingest_example(workspace.id, verified_asymptotic_example)

    methods = {method.key: method for method in service.list_methods(workspace.id)}
    signature = MethodSignature.model_validate(methods["rationalization"].signature)

    assert signature.sample_count == 1
    assert signature.modes["asymptotic_expansion"] == 1
    assert "radical" in signature.operators


def test_methods_from_one_example_share_a_signature(
    tmp_path, verified_asymptotic_example
):
    # 同一道题抽出的多个方法拥有相同结构签名，结构信号无法区分它们。
    # 区分能力来自例子的多样性，这是签名机制的真实边界。
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    result = service.ingest_example(workspace.id, verified_asymptotic_example)

    signatures = {
        MethodSignature.model_validate(method.signature).model_dump_json()
        for method in result.learned_methods
    }
    assert len(result.learned_methods) > 1
    assert len(signatures) == 1


def test_solve_plan_prefers_the_structurally_closer_method(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    service.ingest_example(workspace.id, verified_asymptotic_example)
    service.ingest_example(
        workspace.id,
        ExampleCreate(
            problem="求 n→∞ 时 Gamma(n+1) 的渐进等价式",
            solution="使用 Stirling 斯特林公式处理阶乘型增长。",
            tags=["渐进估计", "阶乘"],
            reviewed=True,
            math_payload=MathPayload(
                expression="gamma(n+1)",
                expected="sqrt(2*pi*n)*(n/E)**n",
                variable="n",
                point="oo",
                mode="asymptotic_equivalence",
            ),
        ),
    )

    plan = service.build_solve_plan(
        workspace.id,
        "求这个表达式在无穷远处的渐进展开",
        top_k=5,
        math_target=_target(expression="sqrt(x**2+9*x)-x"),
    )
    ranking = [match.method.key for match in plan.recommended_methods]

    # 根式题应当把 stirling 排在有理化之后。
    assert "stirling" in ranking
    assert ranking.index("rationalization") < ranking.index("stirling")
