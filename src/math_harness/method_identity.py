from __future__ import annotations

import re
from dataclasses import dataclass

import sympy as sp

from math_harness.checks.claim import Claim
from math_harness.math_parser import SafeMathParser

# 从推导的结构变化导出方法身份。
#
# 离线提炼器原本是 7 个渐进模板，靠**散文里的词**匹配。那条路走不通：词是无界的、
# 依赖语言的，而且出现在公式里就会出错——`z*conjugate(z)` 被标成「共轭有理化」正是
# 这么来的。实测跨领域覆盖率 0.333，一多半的题解什么方法都提不出。
#
# 这里换一个依据：**一步推导在算子树上做掉了什么**。
#
#   diff(x^2, x) = 2x            根上的求导消失了      -> differentiate
#   (a+b)^2 = a^2+2ab+b^2        幂消失、和出现        -> expand_power
#   Sum(f,(k,0,n)) = 2**n        求和号消失            -> evaluate_sum
#
# 与词表的差别不是「换了一张表」：算子集合**有界**，就是安全解析器放行的那些，所以
# 这套规则可以对它完备；而散文词表永远不可能。名字是机械的，但检索要的是能区分，
# 不是好听——有模型时仍以模型提炼为主路，它给的名字更可读。

_PARSER = SafeMathParser()

#: 算子标签 → 它被做掉时对应的技法名。按优先级从上到下匹配。
#
# 顺序是有讲究的：一步推导常常同时动了好几层，最外层的那个才是这一步在干的事。
# `det(Matrix(...) - 3*eye(2)) = 0` 里矩阵和减法都在，但这一步做的是求行列式。
_ELIMINATION_RULES: tuple[tuple[str, str], ...] = (
    ("diff", "differentiate"),
    ("Derivative", "differentiate"),
    ("Integral", "integrate"),
    ("det", "evaluate_determinant"),
    ("trace", "evaluate_trace"),
    ("Sum", "evaluate_sum"),
    ("Product", "evaluate_product"),
    ("binomial", "binomial_identity"),
    ("factorial", "simplify_factorial"),
    ("gamma", "simplify_gamma"),
    ("gcd", "gcd_lcm_identity"),
    ("lcm", "gcd_lcm_identity"),
    ("Mod", "modular_arithmetic"),
    ("conjugate", "use_conjugate"),
    ("Abs", "use_modulus"),
    ("Equivalent", "logical_equivalence"),
    ("Implies", "logical_equivalence"),
    ("And", "logical_equivalence"),
    ("Or", "logical_equivalence"),
)

#: 三角与指数之间的互换，两边都要看才判得出来。
_TRIGONOMETRIC = frozenset({"sin", "cos", "tan", "sinh", "cosh", "tanh"})


#: 从**文本**里认函数调用，而不是从解析结果里认。
#
# 解析器会即时求值：`diff(x^2*sin(x), x)` 解析出来就是求导的结果，求导这件事在算子树
# 上根本不存在了；`det(...)`、`gcd(...)` 同理，全被算成了一个数。要看「这一步做掉了
# 什么」，就只能在求值之前看。
#
# 这仍然不是散文匹配：词表是**解析器的函数白名单**——有界、无歧义、而且这两边都是
# 已经过检查的断言，不是随口写的句子。
_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _called_functions(text: str) -> set[str]:
    return {name for name in _CALL.findall(text) if name in _PARSER.FUNCTIONS}


def _parse(text: str) -> sp.Basic | None:
    import re

    symbols = {
        name: sp.Symbol(name)
        for name in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
        if name not in _PARSER.FUNCTIONS and name not in _PARSER.CONSTANTS
    }
    try:
        return _PARSER.parse(text, symbols)
    except Exception:  # noqa: BLE001
        return None


def transformation_key(claim: Claim) -> str | None:
    """一步推导做掉了什么。判不出来返回 None。"""

    if claim.rhs is None:
        return None
    left_ops = _called_functions(claim.lhs)
    right_ops = _called_functions(claim.rhs)
    removed = left_ops - right_ops
    added = right_ops - left_ops

    for label, key in _ELIMINATION_RULES:
        if label in removed:
            return key

    # 指数与三角互换：欧拉公式那一类。两边都得看，因为它是「换过去」而不是「做掉」。
    if "exp" in removed and _TRIGONOMETRIC & added:
        return "exponential_to_trigonometric"
    if _TRIGONOMETRIC & removed and "exp" in added:
        return "trigonometric_to_exponential"
    left = _parse(claim.lhs)
    right = _parse(claim.rhs)
    if left is None or right is None:
        return None

    # 三角项全消成常数：勾股恒等式那一类。
    if _TRIGONOMETRIC & removed and not right.free_symbols:
        return "trigonometric_identity"
    # 一个三角项变成几个三角项之积：倍角公式那一类。
    if _TRIGONOMETRIC & left_ops and _TRIGONOMETRIC & right_ops:
        if _trig_calls(claim.rhs) > _trig_calls(claim.lhs):
            return "angle_expansion"
        if _trig_calls(claim.lhs) > _trig_calls(claim.rhs):
            return "angle_contraction"

    # 展开与因式分解：看**顶层的加项数**变多还是变少。
    #
    # 数树上所有 Add 节点是不行的——那会把深层的括号也算进去，`(x-h)^2+(y-k)^2` 展开
    # 成六项时反而被判成因式分解。真正的判据是最外层被拆开了还是被合起来了。
    left_terms = _top_level_terms(left)
    right_terms = _top_level_terms(right)
    if right_terms > left_terms:
        return (
            "expand_power"
            if "**" in claim.lhs or "^" in claim.lhs
            else ("expand_product")
        )
    if left_terms > right_terms:
        return "factor_expression"
    return None


def _trig_calls(text: str) -> int:
    return sum(1 for name in _CALL.findall(text) if name in _TRIGONOMETRIC)


def _top_level_terms(expression: sp.Basic) -> int:
    """最外层有几个加项。"""

    return len(expression.args) if expression.is_Add else 1


def derive_method_key(steps: list[Claim]) -> str | None:
    """从一段推导导出它教的是什么方法。

    取**最后一个判得出来的步骤**：前面的步骤常常是铺垫（展开、代入），最后一步才是
    这段推导的落点。判不出来返回 None——**宁可不学，也不要学一个编出来的名字**，
    那正是通用兜底卡的老问题。
    """

    for claim in reversed(steps):
        key = transformation_key(claim)
        if key is not None:
            return key
    return None


@dataclass(frozen=True)
class MethodDescription:
    """一个结构导出的方法键对人可读的说法。

    键是机械的，卡片却是给人复核的。名字可以不好听，但必须说清楚它什么时候适用、
    怎么做、什么时候不成立——不然复核的人无从判断该不该让它进知识库。
    """

    name: str
    goal: str
    applicable_when: str
    procedure: str
    failure_mode: str


METHOD_DESCRIPTIONS: dict[str, MethodDescription] = {
    "differentiate": MethodDescription(
        "求导",
        "对表达式求导并化简。",
        "需要求一个表达式的导数",
        "按和、积、商与复合的求导法则逐层处理",
        "分不清哪一层是内层时会整条算错",
    ),
    "integrate": MethodDescription(
        "求原函数",
        "求出或验证一个原函数。",
        "需要求不定积分或验证候选原函数",
        "凑微分、换元或分部；验证时把候选求导回去比对",
        "求出的原函数相差一个常数",
    ),
    "evaluate_determinant": MethodDescription(
        "求行列式",
        "把方阵的行列式算成具体值或方程。",
        "出现方阵，需要它的行列式或特征值",
        "按行列展开，或代入特征方程令其为零",
        "阶数高时计算量爆炸",
    ),
    "evaluate_trace": MethodDescription(
        "求迹",
        "由主对角线之和得到特征值之和。",
        "只需要特征值的和",
        "把主对角线元素相加",
        "只给出和，不能区分具体是哪几个数",
    ),
    "evaluate_sum": MethodDescription(
        "求和化闭式",
        "把一个求和式化成闭式。",
        "出现带上下界的求和号",
        "识别求和项的形状，套用对应的求和公式或裂项相消",
        "上下界不完整时公式不适用",
    ),
    "evaluate_product": MethodDescription(
        "求积化闭式",
        "把连乘式化成闭式。",
        "出现连乘号",
        "取对数化为求和，或直接套用已知连乘公式",
        "含零因子时结论平凡",
    ),
    "binomial_identity": MethodDescription(
        "二项式恒等式",
        "化简含二项式系数的表达式。",
        "出现二项式系数",
        "对应到二项式定理的展开式，代入具体的底",
        "求和范围不完整时不能直接套用",
    ),
    "simplify_factorial": MethodDescription(
        "阶乘化简",
        "约去或估计阶乘。",
        "出现阶乘",
        "相邻阶乘相约，或用渐进公式估计",
        "阶乘增长极快，近似要注意精度",
    ),
    "simplify_gamma": MethodDescription(
        "Gamma 函数化简",
        "利用递推关系化简 Gamma 函数。",
        "出现 Gamma 函数",
        "用 Gamma(n+1)=n*Gamma(n) 递推约分",
        "非正整数处 Gamma 有极点",
    ),
    "gcd_lcm_identity": MethodDescription(
        "最大公约数与最小公倍数",
        "利用两者之积等于两数之积。",
        "同时涉及 gcd 与 lcm",
        "分别求出二者，用乘积恒等式互相反推",
        "推广到三个数时该恒等式不成立",
    ),
    "modular_arithmetic": MethodDescription(
        "同余与取模",
        "求余数或证明整除性。",
        "问题落在余数、整除或周期性上",
        "把大数按模化简，利用同余的加乘性质",
        "模数不互素时不能随意约分",
    ),
    "use_conjugate": MethodDescription(
        "利用共轭",
        "用共轭把复数关系化成实量。",
        "表达式里出现复数的共轭",
        "把复数与其共轭相乘或相加，交叉项抵消",
        "把共轭当成取相反数会全盘错",
    ),
    "use_modulus": MethodDescription(
        "利用模长",
        "用模长处理复数或绝对值关系。",
        "出现模长或绝对值",
        "转成实部虚部的平方和，或利用模长的乘法性",
        "模长丢掉了辐角信息",
    ),
    "logical_equivalence": MethodDescription(
        "命题等价验证",
        "验证两个命题公式恒同真假。",
        "出现逻辑联结词",
        "列出所有变元的真值组合，逐行比对两边",
        "变元数多时真值表规模指数增长",
    ),
    "exponential_to_trigonometric": MethodDescription(
        "指数化三角",
        "把复指数写成三角形式。",
        "出现虚数单位乘实变量的指数",
        "套用欧拉公式",
        "指数中变量不是实数时要额外小心",
    ),
    "trigonometric_to_exponential": MethodDescription(
        "三角化指数",
        "把三角函数写成复指数。",
        "需要把三角函数化成指数便于运算",
        "用一对共轭指数表示正弦或余弦",
        "转换后要记得最终回到实数形式",
    ),
    "trigonometric_identity": MethodDescription(
        "三角恒等式",
        "把同角三角组合化成常数。",
        "同一变量的几个三角函数组合成常数",
        "套用平方和为一的恒等式或其双曲对应",
        "三角与双曲的符号规则不同，不能混用",
    ),
    "angle_expansion": MethodDescription(
        "角的展开",
        "把倍角或和角拆成单角。",
        "出现二倍角、和角或差角",
        "套用倍角或和角公式展开",
        "余弦倍角有三种形式，选错会引入多余项",
    ),
    "angle_contraction": MethodDescription(
        "角的合并",
        "把单角的乘积合成倍角或和角。",
        "出现几个单角三角函数之积",
        "反向使用倍角或积化和差公式",
        "合并方向不唯一，要选对目标形式",
    ),
    "expand_power": MethodDescription(
        "展开幂式",
        "把括号的幂展开成多项式。",
        "出现和或差的整数次幂",
        "按二项式定理或完全平方公式逐项展开",
        "项数随次数增长很快",
    ),
    "expand_product": MethodDescription(
        "展开乘积",
        "把括号相乘展开成多项式。",
        "出现几个含变量的括号相乘",
        "逐项相乘再合并同类项",
        "漏项或符号写反是最常见的错误",
    ),
    "factor_expression": MethodDescription(
        "合并与因式分解",
        "把多项式合并或分解成乘积。",
        "多项对称项可以相消或提取公因式",
        "对称项相消、提取公因式或套用已知分解式",
        "分解形式不唯一，要选对目标",
    ),
}
