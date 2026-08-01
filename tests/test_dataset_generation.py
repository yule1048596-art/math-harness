from __future__ import annotations

from pathlib import Path

from math_harness.dataset import (
    FAMILIES,
    FamilyInstance,
    ProblemFamily,
    _serialize,
    generate_examples,
    load_holdout_expressions,
)
from math_harness.models import VerificationMode, VerificationStatus
from math_harness.verifier import SolutionVerifier

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED = PROJECT_ROOT / "data/pilot/asymptotic_train_generated.jsonl"


def test_every_generated_example_passes_the_verifier():
    examples, report = generate_examples()
    verifier = SolutionVerifier()

    assert report.emitted > 0
    assert report.rejected == 0
    for example in examples:
        # 生成语料不因为「是我们自己生成的」就被信任：它走和人工数据完全相同的
        # 那条 SymPy 裁决线。这条断言是生成器唯一的安全保证。
        assert example.math_payload is not None
        report_ = verifier.verify(example.math_payload)
        assert report_.status is VerificationStatus.VERIFIED, example.problem


def test_generation_is_deterministic():
    first, _ = generate_examples()
    second, _ = generate_examples()

    assert list(_serialize(first)) == list(_serialize(second))


def test_generated_examples_are_marked_reviewed_and_carry_payloads():
    examples, _ = generate_examples()

    assert all(example.reviewed for example in examples)
    assert all(example.math_payload is not None for example in examples)
    assert all(example.tags for example in examples)


def test_no_duplicate_problems():
    examples, _ = generate_examples()
    problems = [example.problem for example in examples]

    assert len(problems) == len(set(problems))


def test_families_cover_the_six_verifiable_methods():
    _examples, report = generate_examples()

    assert set(report.by_method) == {
        "rationalization",
        "variable_inversion",
        "taylor_expansion",
        "dominant_balance",
        "log_transform",
        "stirling",
    }
    # 任何一个家族被写空或全军覆没都应当立刻失败，而不是静默地稀释语料。
    assert all(count >= 5 for count in report.by_method.values())


def test_family_solutions_trigger_their_own_method_extraction():
    from math_harness.methods import MethodExtractor

    extractor = MethodExtractor()
    for family in FAMILIES:
        if not family.instances:
            continue
        keys = [
            draft.key
            for draft in extractor.extract(
                family.instances[0].problem, family.solution
            ).methods
        ]
        # 解答文本必须命中自己方法模板的 marker，否则这条语料学不到目标方法。
        assert family.method_key in keys, family.key


def test_unverifiable_instance_is_dropped_not_downgraded():
    bogus = ProblemFamily(
        key="bogus",
        method_key="rationalization",
        mode=VerificationMode.ASYMPTOTIC_EQUIVALENCE,
        point="oo",
        tags=("测试",),
        solution="先做共轭有理化。",
        instances=(
            FamilyInstance(
                problem="错误答案应当被丢弃。",
                expression="sqrt(x**2 + x) - x",
                expected="999",
            ),
        ),
    )
    examples, report = generate_examples(families=(bogus,))

    assert examples == []
    assert report.rejected == 1


def test_generated_corpus_never_contains_a_holdout_expression():
    """生成器必须主动排除留出题。

    只「不生成留出集」是不够的：参数网格会顺手覆盖掉留出题，那样评测测的是记忆
    而不是检索。这条断言把排除逻辑钉死，而不是靠写生成器的人小心。
    """

    examples, report = generate_examples()
    holdout = load_holdout_expressions()

    assert report.excluded > 0, "参数网格与留出集完全不相交是可疑的，请确认排除逻辑生效"
    for example in examples:
        expression = "".join(example.math_payload.expression.split())
        assert expression not in holdout, example.problem


def test_checked_in_corpus_matches_the_generator():
    """仓库里的语料必须与生成器当前输出一致，否则评测结果不可复现。"""

    examples, _ = generate_examples()
    expected = [line for line in _serialize(examples)]
    actual = GENERATED.read_text(encoding="utf-8").splitlines()

    assert actual == expected
