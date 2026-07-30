from __future__ import annotations

from dataclasses import dataclass

from math_harness.models import (
    ExtractionStatus,
    MethodDraft,
    MethodExtractionResult,
    MethodExtractionTrace,
    MethodKind,
)


@dataclass(frozen=True)
class MethodTemplate:
    key: MethodKind
    name: str
    goal: str
    applicable_when: tuple[str, ...]
    procedure: tuple[str, ...]
    failure_modes: tuple[str, ...]
    tags: tuple[str, ...]
    markers: tuple[str, ...]

    def to_draft(self) -> MethodDraft:
        return MethodDraft(
            key=self.key,
            name=self.name,
            goal=self.goal,
            applicable_when=list(self.applicable_when),
            procedure=list(self.procedure),
            failure_modes=list(self.failure_modes),
            tags=list(self.tags),
        )


METHOD_TEMPLATES = (
    MethodTemplate(
        key=MethodKind.RATIONALIZATION,
        name="共轭有理化后分析抵消",
        goal="处理两个接近根式相减造成的主项抵消。",
        applicable_when=("表达式含根式之差", "直接代入出现 ∞-∞ 型抵消"),
        procedure=(
            "乘以共轭表达式，将差改写为商。",
            "先分析分子和分母各自的主导阶。",
            "再决定是否需要继续做级数展开。",
        ),
        failure_modes=("遗漏共轭分母的定义域", "有理化后过早截断导致丢失下一阶"),
        tags=("asymptotic", "radical", "cancellation"),
        markers=("有理化", "共轭", "rationaliz", "conjugate"),
    ),
    MethodTemplate(
        key=MethodKind.VARIABLE_INVERSION,
        name="令 t=1/x 将无穷远化为零点",
        goal="把无穷远处的渐进问题转换成零点附近的局部展开。",
        applicable_when=("自变量趋于正负无穷", "表达式可用 1/x 统一尺度"),
        procedure=(
            "令 t=1/x，并记录趋近方向。",
            "将所有项改写成 t 的函数。",
            "在 t=0 附近展开后再代回 x。",
        ),
        failure_modes=("忽略 x→-∞ 时的符号", "变量替换后未同步余项阶数"),
        tags=("asymptotic", "infinity", "change-of-variable"),
        markers=("t=1/x", "t = 1/x", "令t", "令 t", "1/x代换", "变量倒换"),
    ),
    MethodTemplate(
        key=MethodKind.TAYLOR_EXPANSION,
        name="按目标精度做泰勒展开",
        goal="在指定点附近获得函数的有限阶渐进展开。",
        applicable_when=("函数在展开点附近足够光滑或解析", "目标要求有限阶余项"),
        procedure=(
            "先估计最终需要的最高阶。",
            "对各子表达式采用一致精度展开。",
            "合并后检查主项是否抵消，必要时增加展开阶数。",
        ),
        failure_modes=("主项抵消后展开阶数不足", "越过奇点或分支点套用普通泰勒公式"),
        tags=("asymptotic", "series", "taylor", "cancellation"),
        markers=("泰勒", "taylor", "级数展开", "幂级数", "series expansion"),
    ),
    MethodTemplate(
        key=MethodKind.DOMINANT_BALANCE,
        name="主导平衡与尺度判定",
        goal="识别相互竞争的主导项并确定正确尺度。",
        applicable_when=("多个项的大小随参数变化", "直接展开无法判断保留哪些项"),
        procedure=(
            "为各项写出候选数量级。",
            "令可能竞争的项处于同一数量级。",
            "分参数区域检查主导关系是否改变。",
        ),
        failure_modes=("只看单项而忽略抵消", "参数临界值处未重新分区"),
        tags=("asymptotic", "dominant-balance", "parameter"),
        markers=("主导平衡", "主导项", "dominant balance", "数量级", "尺度"),
    ),
    MethodTemplate(
        key=MethodKind.LOG_TRANSFORM,
        name="取对数后展开",
        goal="把乘积、幂或指数型渐近问题转化为加法结构。",
        applicable_when=("表达式包含大幂、连乘或指数", "直接展开数值尺度差异过大"),
        procedure=(
            "确认表达式符号和对数定义域。",
            "对对数表达式展开并收集各阶。",
            "指数还原时重新检查余项传播。",
        ),
        failure_modes=("忽略符号导致对数无定义", "指数还原时低估误差"),
        tags=("asymptotic", "logarithm", "exponential"),
        markers=("取对数", "两边取log", "log transform", "对数化"),
    ),
    MethodTemplate(
        key=MethodKind.STIRLING,
        name="Stirling 公式处理阶乘与 Gamma",
        goal="估计大参数下的阶乘、Gamma 函数及其比值。",
        applicable_when=("n→∞ 且含 n! 或 Gamma", "组合数需要对数级或乘法级精度"),
        procedure=(
            "确定需要使用 Stirling 公式的阶数。",
            "优先在对数域合并大乘积。",
            "还原并检查相对误差是否满足目标。",
        ),
        failure_modes=("只保留主项却声称更高精度", "多个阶乘比值中的误差处理不一致"),
        tags=("asymptotic", "factorial", "gamma", "stirling"),
        markers=("stirling", "斯特林", "阶乘公式"),
    ),
    MethodTemplate(
        key=MethodKind.LAPLACE_METHOD,
        name="Laplace 方法定位积分主贡献",
        goal="估计含大参数积分的主贡献区域。",
        applicable_when=("积分含大参数指数项", "贡献集中在极值点或端点附近"),
        procedure=(
            "寻找指数相位的主导极值点。",
            "在贡献点附近做局部二次或更高阶展开。",
            "控制贡献区外的余项并合并系数。",
        ),
        failure_modes=("存在多个同阶驻点但只保留一个", "退化驻点仍套用非退化高斯近似"),
        tags=("asymptotic", "integral", "laplace"),
        markers=("laplace", "拉普拉斯方法", "鞍点", "驻点"),
    ),
)


class MethodExtractor:
    name = "rules"
    prompt_version = "rules-v1"

    def extract(
        self, problem: str, solution: str, hint: str | None = None
    ) -> MethodExtractionResult:
        combined = "\n".join(
            part for part in (problem, solution, hint or "") if part
        ).lower()
        drafts = [
            template.to_draft()
            for template in METHOD_TEMPLATES
            if any(marker.lower() in combined for marker in template.markers)
        ]
        if not drafts:
            drafts = [
                MethodDraft(
                    key=MethodKind.GENERIC_EXAMPLE,
                    name=hint or "待归纳的案例方法",
                    goal="保留当前案例中的解题步骤，等待更多样本后归纳。",
                    applicable_when=["出现与来源题目相近的结构"],
                    procedure=["检索来源案例", "比较适用条件", "经验证后复用"],
                    failure_modes=["样本过少，尚不能可靠泛化"],
                    tags=["candidate", "example-derived"],
                )
            ]

        return MethodExtractionResult(
            methods=drafts,
            trace=MethodExtractionTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=ExtractionStatus.SUCCESS,
                extracted_method_keys=[draft.key for draft in drafts],
            ),
        )
