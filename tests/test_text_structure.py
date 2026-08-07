from __future__ import annotations

from math_harness.models import (
    ApproachDirection,
    SolveMathTarget,
    VerificationMode,
)
from math_harness.structure import (
    StructuralFeatures,
    extract_features,
    features_from_text,
    operator_paths,
)

# 从自然语言文本抽结构特征。
#
# 这一层存在的理由：聊天路径上永远没有 `math_target`，而 `extract_features` 只认
# 那个形状。以前那条路上结构检索**一次都不会启动**，只剩词面和标签。


def test_a_chinese_question_yields_structure():
    features = features_from_text("求 x^3*cos(x) 的导数。")

    assert not features.is_empty
    assert features.paths


def test_prose_without_expressions_yields_nothing():
    """抽不出结构不是失败，检索会自然退回词面——那正是 text_only 那一格量的东西。"""

    features = features_from_text("说明为什么两个奇数之和一定是偶数。")

    assert features.is_empty


def test_asymptotic_facets_stay_empty_for_plain_text():
    """趋近点、方向、余项是渐进设定专属的，在通用文本上没有意义，留空而不是瞎填。"""

    features = features_from_text("化简 (a+b)^2-(a-b)^2。")

    assert features.mode is None
    assert features.point_kind is None
    assert features.direction is None
    assert not features.has_remainder


def test_matrices_get_a_fingerprint():
    """`leaf_root_paths` 原本假定节点是 `Expr`，碰到矩阵抛 AttributeError 被兜底吞掉。

    线性代数是点名要覆盖的领域之一，却在结构上完全隐形。
    """

    paths = operator_paths("Matrix([[3,1],[1,3]])")

    assert paths


def test_matrix_order_is_part_of_the_shape():
    """二阶和三阶行列式该不该用代数余子式展开，答案不一样。"""

    assert operator_paths("Matrix([[1,2],[3,4]])") != operator_paths(
        "Matrix([[1,2,3],[4,5,6],[7,8,10]])"
    )


def test_the_same_shape_with_different_numbers_matches():
    """路径表示对系数不敏感，这是刻意的——换个数字不该变成另一道题。"""

    assert features_from_text("化简 (y+c)^2-(y-c)^2。").paths == (
        features_from_text("化简 (m+d)^2-(m-d)^2。").paths
    )


def test_different_shapes_do_not_match():
    assert features_from_text("求 x^2*sin(x) 的导数。").paths != (
        features_from_text("求 sin(x^2) 的导数。").paths
    )


def test_human_notation_parses():
    """`9x`、`Γ(n)`、`ln(x)` 是人写数学的常态，不认就等于题面全部抽不出结构。"""

    for text in (
        "求 x→∞ 时 sqrt(x^2+9x)-x 的渐进展开。",
        "求 n→∞ 时 Gamma(n+7) 的渐进等价式。",
        "求 ln(1+x) 在 x=0 附近的展开。",
        "化简 2(a+b)(a-b)。",
    ):
        assert not features_from_text(text).is_empty, text


def test_a_confirmed_target_still_uses_the_original_path():
    """有渐进目标时行为逐字不变——那几个数字花了两个版本才拿到。"""

    target = SolveMathTarget(
        expression="sqrt(x**2 + x) - x",
        variable="x",
        point="oo",
        direction=ApproachDirection.TWO_SIDED,
        mode=VerificationMode.ASYMPTOTIC_EXPANSION,
        remainder_power=2,
    )

    features = extract_features(target)

    assert features.mode == "asymptotic_expansion"
    assert features.point_kind == "pos_infinity"
    assert features.has_remainder


def test_extract_features_still_returns_empty_for_none():
    assert extract_features(None) == StructuralFeatures()


def test_a_broken_fragment_never_raises():
    """检索不能因为某个片段形状古怪就整体失败。"""

    assert features_from_text("求 ((((  的值。").is_empty
    assert operator_paths("((((") == set()
