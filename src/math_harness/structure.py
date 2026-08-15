from __future__ import annotations

import math
import re

import sympy as sp
from pydantic import BaseModel, Field

from math_harness.math_parser import SafeMathParser, build_symbol_table
from math_harness.models import MathPayload, SolveMathTarget

# 数学题的判别信息大多在结构里——根式之差、趋近点、余项阶——而不在词面。
# 这里把结构抽成一组确定性标签，供检索使用；不调用模型，零额外成本。

_TRIG = (sp.sin, sp.cos, sp.tan, sp.asin, sp.acos, sp.atan)
_HYPERBOLIC = (sp.sinh, sp.cosh, sp.tanh)

_PARSER = SafeMathParser()

# 叶到根路径保留的层数。实测在 152 条语料上 depth 3 与 5 效果相同（Hit@1 0.944），
# 但 3 的特征维度更低（83 vs 96），优先取低容量的那个。
PATH_DEPTH = 3

#: 结构特征的版本。**改动下面任何一样东西都必须把它加一**：
#:
#:   - `normalize_math_text` 与它调用的记号归一（`expand_latex`、隐式乘、别名表）；
#:   - `leaf_root_paths` 与 `PATH_DEPTH`；
#:   - 算子、标志、趋近点的命名或取值集合。
#:
#: 存量方法卡的签名是**过去某个版本**算出来的。改了提取器而不改这个数，库里的旧签名
#: 和新算出来的查询特征就不在同一个空间里了——**而且不会报任何错**，只会让命中率
#: 悄悄下降。
#:
#: 跨领域门禁抓不到这件事：它每次从语料重建整个库，两边永远同版本。真正受损的是用户
#: 积累了几个月的存量工作区，而那里没有任何人在看。
FEATURE_VERSION = 1


class StructuralFeatures(BaseModel):
    """一道题的数学结构指纹。解析失败时所有字段为空，检索会自然退回词面路径。"""

    mode: str | None = None
    point_kind: str | None = None
    direction: str | None = None
    has_remainder: bool = False
    operators: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    # 叶到根路径：算子集合是扁平的，`sqrt` 和 `division` 只是两个标签，丢掉了
    # 「谁套在谁外面」。路径保留这层层次，是比标签袋更强的表示。
    paths: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.mode or self.point_kind or self.operators or self.flags or self.paths
        )


class MethodSignature(BaseModel):
    """一张方法卡「通常用在什么结构上」，由它关联例子的结构特征累积而来。"""

    modes: dict[str, int] = Field(default_factory=dict)
    point_kinds: dict[str, int] = Field(default_factory=dict)
    operators: dict[str, int] = Field(default_factory=dict)
    flags: dict[str, int] = Field(default_factory=dict)
    paths: dict[str, int] = Field(default_factory=dict)
    sample_count: int = 0
    #: 这份签名是哪个版本的提取器算出来的。
    #
    # 缺省 0 表示「不知道」——v0.20 之前存的卡片没有这个字段，而它们确实可能来自任何
    # 一个旧版本。当成未知比当成当前版本安全：检索会跳过它的结构分，而不是拿两个不同
    # 空间里的向量去比。
    feature_version: int = 0

    @property
    def is_current(self) -> bool:
        """这份签名能不能和当前算出来的查询特征放在一起比。"""

        return self.feature_version == FEATURE_VERSION

    def combined_with(self, other: MethodSignature) -> MethodSignature:
        """合并两张方法卡的签名：逐项相加，样本数相加。"""

        def merge(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
            merged = dict(left)
            for key, value in right.items():
                merged[key] = merged.get(key, 0) + value
            return merged

        return MethodSignature(
            modes=merge(self.modes, other.modes),
            point_kinds=merge(self.point_kinds, other.point_kinds),
            operators=merge(self.operators, other.operators),
            flags=merge(self.flags, other.flags),
            paths=merge(self.paths, other.paths),
            sample_count=self.sample_count + other.sample_count,
            # 合并两个不同版本的签名，结果不属于任何一个版本。取小的那个（未知=0 最小）
            # 让它整体失效，等重建——把混过一次的东西当成当前版本，正是安静出错。
            feature_version=min(self.feature_version, other.feature_version),
        )

    def accumulate(self, features: StructuralFeatures) -> MethodSignature:
        if features.is_empty:
            return self

        modes = dict(self.modes)
        point_kinds = dict(self.point_kinds)
        operators = dict(self.operators)
        flags = dict(self.flags)
        paths = dict(self.paths)
        if features.mode:
            modes[features.mode] = modes.get(features.mode, 0) + 1
        if features.point_kind:
            point_kinds[features.point_kind] = (
                point_kinds.get(features.point_kind, 0) + 1
            )
        for operator in features.operators:
            operators[operator] = operators.get(operator, 0) + 1
        for flag in features.flags:
            flags[flag] = flags.get(flag, 0) + 1
        for path in features.paths:
            paths[path] = paths.get(path, 0) + 1

        return MethodSignature(
            modes=modes,
            point_kinds=point_kinds,
            operators=operators,
            flags=flags,
            paths=paths,
            sample_count=self.sample_count + 1,
            # 往一份旧签名上叠加新特征，得到的是两个版本的混合物。同样取小的：
            # 它必须整体重建，不能因为最新一次累加就自称当前版本。
            feature_version=(
                FEATURE_VERSION
                if self.sample_count == 0
                else min(self.feature_version, FEATURE_VERSION)
            ),
        )


def _classify_point(point: str) -> str:
    normalized = point.strip().lower().replace(" ", "")
    if normalized in {"oo", "+oo", "inf", "infinity"}:
        return "pos_infinity"
    if normalized in {"-oo", "-inf", "-infinity"}:
        return "neg_infinity"
    if normalized in {"0", "0.0", "-0"}:
        return "zero"
    return "finite"


def _collect_operators(expression: sp.Expr, variable: sp.Symbol) -> set[str]:
    operators: set[str] = set()
    for node in sp.preorder_traversal(expression):
        if isinstance(node, sp.Pow):
            exponent = node.exp
            if exponent.is_Rational and exponent.q == 2:
                operators.add("radical")
            if exponent.is_number and exponent.is_negative:
                operators.add("division")
            if variable in exponent.free_symbols:
                operators.add("symbolic_pow")
            continue
        if isinstance(node, sp.exp):
            operators.add("exp")
        elif isinstance(node, sp.log):
            operators.add("log")
        elif isinstance(node, _TRIG):
            operators.add("trig")
        elif isinstance(node, _HYPERBOLIC):
            operators.add("hyperbolic")
        elif isinstance(node, sp.gamma):
            operators.add("gamma")
        elif isinstance(node, sp.factorial):
            operators.add("factorial")
    return operators


def _node_label(node: sp.Expr, variable: sp.Symbol) -> str:
    if isinstance(node, sp.Pow):
        exponent = node.exp
        if exponent.is_Rational and exponent.q == 2:
            return "Sqrt"
        if exponent.is_number and exponent.is_negative:
            return "Div"
        if variable in exponent.free_symbols:
            return "SymPow"
        return "Pow"
    if isinstance(node, sp.Add):
        return "Add"
    if isinstance(node, sp.Mul):
        return "Mul"
    if isinstance(node, sp.exp):
        return "Exp"
    if isinstance(node, sp.log):
        return "Log"
    if isinstance(node, _TRIG):
        return "Trig"
    if isinstance(node, _HYPERBOLIC):
        return "Hyp"
    if isinstance(node, sp.gamma):
        return "Gamma"
    if isinstance(node, sp.factorial):
        return "Fact"
    return type(node).__name__


def leaf_root_paths(
    expression: sp.Expr,
    variable: sp.Symbol,
    depth: int = PATH_DEPTH,
) -> set[str]:
    """算子树上每个叶子到根的路径（保留最靠近叶子的 `depth` 层算子）。

    取自 approach0 在数学公式检索上的做法。只保留有限层是为了控制特征维度——
    路径的表达力远高于扁平算子集合，语料不足时会因稀疏而变差。
    """

    collected: set[str] = set()

    def walk(node: sp.Expr, prefix: tuple[str, ...]) -> None:
        if isinstance(node, sp.MatrixBase):
            # 矩阵不是 `Expr`，没有 `is_Symbol`——不单独处理的话整个线性代数领域
            # 会在这里抛 AttributeError，被上层兜底吞成「没有结构」。
            # 阶数进标签：二阶和三阶行列式该不该用代数余子式展开，答案是不一样的。
            label = f"matrix{node.rows}x{node.cols}"
            for entry in node:
                walk(entry, prefix + (label,))
            return
        if node.is_Symbol:
            leaf = "VAR" if node == variable else "PARAM"
            collected.add("/".join(prefix[-depth:] + (leaf,)))
            return
        if node.is_Number:
            collected.add("/".join(prefix[-depth:] + ("NUM",)))
            return
        label = _node_label(node, variable)
        for argument in node.args:
            walk(argument, prefix + (label,))

    walk(expression, ())
    return collected


#: 一个不会出现在任何表达式里的哑符号。
#
# `leaf_root_paths` 靠它区分 VAR（趋近变量）和 PARAM（其余符号），那个区分只在渐进
# 设定下有意义。通用表达式里没有天然的「主变量」，硬挑一个是任意的，任意的区分只会
# 添噪声——所以这里让所有符号统一落到 PARAM。
_NO_DISTINGUISHED_VARIABLE = sp.Symbol("__none__")


def operator_paths(
    expression_text: str,
    parser: SafeMathParser | None = None,
    depth: int = PATH_DEPTH,
) -> set[str]:
    """任意表达式文本的叶到根路径。

    渐进路径走 `extract_features`，它要一个 `SolveMathTarget`。这个入口只要文本，
    让跨领域的题面也能拿到同一种结构指纹。

    解析失败返回空集：检索不能因为某个片段形状古怪就整体失败。
    """

    safe_parser = parser or SafeMathParser()
    symbols = {
        name: sp.Symbol(name)
        for name in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression_text))
        if name not in safe_parser.FUNCTIONS and name not in safe_parser.CONSTANTS
    }
    try:
        expression = safe_parser.parse(expression_text, symbols)
    except Exception:  # noqa: BLE001
        return set()
    try:
        return leaf_root_paths(expression, _NO_DISTINGUISHED_VARIABLE, depth)
    except Exception:  # noqa: BLE001
        return set()


def features_from_text(
    text: str,
    parser: SafeMathParser | None = None,
) -> StructuralFeatures:
    """从任意自然语言文本里抽结构特征。

    `extract_features` 要一个渐进形状的 `SolveMathTarget`，而聊天路径的提问和知识
    草稿都没有——于是那条路上**结构检索一次都不会启动**，只剩词面。

    这里用的是同一套算子标签和叶到根路径，只是入口换成文本：把题面里能安全解析的
    片段都找出来，路径取并集。渐进专属的那几个 facet（趋近点、方向、余项）留空，
    它们在这里没有意义。
    """

    from math_harness.claim_drafting import extract_expressions

    safe_parser = parser or SafeMathParser()
    operators: set[str] = set()
    paths: set[str] = set()
    for expression_text in extract_expressions(text, safe_parser):
        symbols = {
            name: sp.Symbol(name)
            for name in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression_text))
            if name not in safe_parser.FUNCTIONS and name not in safe_parser.CONSTANTS
        }
        try:
            expression = safe_parser.parse(expression_text, symbols)
            operators |= _collect_operators(expression, _NO_DISTINGUISHED_VARIABLE)
            paths |= leaf_root_paths(expression, _NO_DISTINGUISHED_VARIABLE)
        except Exception:  # noqa: BLE001, S112 —— 单个片段解析不了就跳过它，
            # 检索不能因为题面里混着一段奇怪的字符就整体失败。
            continue

    return StructuralFeatures(
        operators=sorted(operators),
        paths=sorted(paths),
    )


def _collect_flags(
    expression: sp.Expr,
    variable: sp.Symbol,
    operators: set[str],
    point_kind: str,
    parameters: list[str],
) -> set[str]:
    flags: set[str] = set()
    if parameters:
        flags.add("has_parameters")
    if {"gamma", "factorial"} & operators:
        flags.add("factorial_or_gamma")
    if "symbolic_pow" in operators:
        flags.add("exponential_power")
    if "trig" in operators and point_kind in {"pos_infinity", "neg_infinity"}:
        flags.add("oscillatory_at_infinity")

    transcendental = {"exp", "log", "trig", "hyperbolic", "gamma", "factorial"}
    if not (operators & transcendental) and "radical" not in operators:
        flags.add("rational_function")
        if expression.is_polynomial(variable):
            flags.add("polynomial")

    top_level = sp.Add.make_args(expression)
    if len(top_level) >= 2:
        signed = any(term.could_extract_minus_sign() for term in top_level)
        if signed:
            flags.add("cancellation_risk")
        # 有理化的触发形状是「根式参与的主项抵消」，不是「出现了根式加项」。
        #
        # 早先这里刻意不看符号，理由是 sqrt(x^2+2x)+x 在 x→-∞ 同样是 ∞-∞。那个理由
        # 只在负无穷方向成立：在 +∞ 处 sqrt(x^2+1)+sqrt(x^2+2) 各项同号，没有任何
        # 抵消，有理化用不上。放宽到「有根式加项就算」会把这类题错误地推向有理化——
        # cross_family 切片上量到的正是这个失败。
        radical_addend = any(
            "radical" in _collect_operators(term, variable) for term in top_level
        )
        if radical_addend and (signed or point_kind == "neg_infinity"):
            flags.add("radical_difference")
    return flags


def extract_features(
    target: SolveMathTarget | MathPayload | None,
) -> StructuralFeatures:
    """把结构化目标转成结构特征。

    任何解析或遍历失败都降级为空特征——检索不能因为某个表达式形状古怪就整体失败。
    """

    if target is None:
        return StructuralFeatures()

    point_kind = _classify_point(target.point)
    features = StructuralFeatures(
        mode=target.mode.value,
        point_kind=point_kind,
        direction=target.direction.value,
        has_remainder=target.remainder_power is not None,
    )
    try:
        symbols = build_symbol_table(
            target.variable,
            target.parameters,
            target.assumptions,
        )
        expression = _PARSER.parse(target.expression, symbols)
        variable = symbols[target.variable]
        operators = _collect_operators(expression, variable)
        flags = _collect_flags(
            expression,
            variable,
            operators,
            point_kind,
            target.parameters,
        )
        paths = leaf_root_paths(expression, variable)
    except Exception:  # noqa: BLE001
        return features

    return features.model_copy(
        update={
            "operators": sorted(operators),
            "flags": sorted(flags),
            "paths": sorted(paths),
        }
    )


def _distribution(counts: dict[str, int]) -> dict[str, float]:
    total = sum(counts.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in counts.items()}


def _histogram_intersection(left: dict[str, int], right: dict[str, int]) -> float:
    """两个计数分布的重合度，取值 [0, 1]。"""

    a = _distribution(left)
    b = _distribution(right)
    if not a or not b:
        return 0.0
    return sum(min(a.get(key, 0.0), b.get(key, 0.0)) for key in a.keys() | b.keys())


def signature_similarity(left: MethodSignature, right: MethodSignature) -> float:
    """两张方法卡「用在什么结构上」有多接近，取值 [0, 1]。

    用于去重：结构签名高度重合意味着两张卡很可能是同一个方法的不同命名。
    与 `signature_score` 用同一套权重取向——路径最重，因为它判别力最强。
    """

    if left.sample_count <= 0 or right.sample_count <= 0:
        return 0.0
    return round(
        _histogram_intersection(left.modes, right.modes) * 0.15
        + _histogram_intersection(left.point_kinds, right.point_kinds) * 0.10
        + _histogram_intersection(left.operators, right.operators) * 0.10
        + _histogram_intersection(left.flags, right.flags) * 0.15
        + _histogram_intersection(left.paths, right.paths) * 0.50,
        6,
    )


def path_idf(signatures: list[MethodSignature]) -> dict[str, float]:
    """按方法卡出现频次给路径算逆文档频率。

    `Add/VAR` 这种路径几乎每个方法都有，判别力接近零；`Gamma/Add/VAR` 只属于少数
    方法，命中时应当占更大权重。IDF 在检索时对候选方法集现算，成本可忽略。
    """

    document_frequency: dict[str, int] = {}
    for signature in signatures:
        for path in signature.paths:
            document_frequency[path] = document_frequency.get(path, 0) + 1
    total = len(signatures)
    return {
        path: math.log(1 + total / (1 + frequency))
        for path, frequency in document_frequency.items()
    }


def path_background(signatures: list[MethodSignature]) -> dict[str, float]:
    """每条路径在全部方法上的平均条件概率。

    这是把似然变成后验的分母。IDF 只看「有多少方法出现过这条路径」，看不出**强度**：
    26 条训练样本全含根式的方法，`P(根式路径|它) ≈ 1.0`，于是任何带根式的查询都先
    命中它——哪怕根式对这个方法根本不构成判别信息。v0.13 的 cross_family 切片量到的
    正是这个偏差。

    与 `path_idf` 在检索时同一处对候选方法集现算，成本可忽略。
    """

    totals: dict[str, float] = {}
    counted = 0
    for signature in signatures:
        if signature.sample_count <= 0:
            continue
        counted += 1
        for path, count in signature.paths.items():
            totals[path] = totals.get(path, 0.0) + min(
                count / signature.sample_count, 1.0
            )
    if counted == 0:
        return {}
    return {path: total / counted for path, total in totals.items()}


def _posterior(likelihood: float, background: float) -> float:
    """把 `P(特征|方法)` 折算成有界的后验式证据强度。

    `likelihood == background` 时得 0.5，即「这条特征对区分方法没有信息」；远高于
    背景才接近 1。背景缺失时退回原始似然，保持旧行为。
    """

    if background <= 0:
        return likelihood
    total = likelihood + background
    return likelihood / total if total > 0 else 0.0


def _conditional_hit_rate(
    counts: dict[str, int],
    items: list[str],
    sample_count: int,
) -> float:
    """查询中的结构项在该方法卡历史里出现得有多普遍。"""

    if not items or sample_count <= 0:
        return 0.0
    total = sum(min(counts.get(item, 0) / sample_count, 1.0) for item in items)
    return total / len(items)


def _weighted_path_hit_rate(
    counts: dict[str, int],
    paths: list[str],
    sample_count: int,
    idf: dict[str, float],
    background: dict[str, float] | None = None,
) -> float:
    """查询路径在该方法卡历史里的 IDF 加权命中强度。

    背景分布存在时按后验折算，把「训练样本单一的方法通吃共享特征」这个偏差消掉；
    缺失时退回原始条件命中率，保持旧行为。
    """

    if not paths or sample_count <= 0:
        return 0.0
    weight_total = sum(idf.get(path, 1.0) for path in paths)
    if weight_total <= 0:
        return 0.0
    hit = 0.0
    for path in paths:
        likelihood = min(counts.get(path, 0) / sample_count, 1.0)
        if background:
            likelihood = _posterior(likelihood, background.get(path, 0.0))
        hit += likelihood * idf.get(path, 1.0)
    return hit / weight_total


def signature_score(
    signature: MethodSignature,
    features: StructuralFeatures,
    idf: dict[str, float] | None = None,
    background: dict[str, float] | None = None,
) -> float:
    """方法卡的结构签名与当前题目结构的契合度，取值 [0, 1]。

    路径拿到最大权重：实测在 152 条语料上纯路径打分（0.944）已经超过 v0.4.0 的
    全部混合打分（0.889）。其余低容量特征保留下来，是为了在方法卡样本很少、路径
    统计还不可靠时兜底。
    """

    if signature.sample_count <= 0 or features.is_empty:
        return 0.0

    count = signature.sample_count
    mode_score = (
        min(signature.modes.get(features.mode, 0) / count, 1.0)
        if features.mode
        else 0.0
    )
    point_score = (
        min(signature.point_kinds.get(features.point_kind, 0) / count, 1.0)
        if features.point_kind
        else 0.0
    )
    operator_score = _conditional_hit_rate(
        signature.operators, features.operators, count
    )
    flag_score = _conditional_hit_rate(signature.flags, features.flags, count)
    path_score = _weighted_path_hit_rate(
        signature.paths, features.paths, count, idf or {}, background
    )

    return round(
        mode_score * 0.15
        + point_score * 0.10
        + operator_score * 0.10
        + flag_score * 0.15
        + path_score * 0.50,
        6,
    )
