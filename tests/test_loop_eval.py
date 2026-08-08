from __future__ import annotations

from pathlib import Path

import pytest

from math_harness.loop_eval import (
    Lesson,
    LoopQuery,
    LoopReport,
    _load_jsonl,
    gate_failures,
    validate_corpus,
)

LESSONS = Path("data/pilot/loop_lessons.jsonl")
QUERIES = Path("data/pilot/loop_queries.jsonl")

# 端到端闭环度量的守卫。
#
# 这条度量存在的理由是：v0.15 到 v0.16 每一版的度量都只盯着那一版在改的东西，
# 于是同一类缺陷活过了三个版本。它一环都不绕，所以它自己的语料和判据必须站得住。


@pytest.fixture(scope="module")
def lessons() -> list[Lesson]:
    return _load_jsonl(LESSONS, Lesson)


@pytest.fixture(scope="module")
def queries() -> list[LoopQuery]:
    return _load_jsonl(QUERIES, LoopQuery)


def test_the_corpus_passes_its_own_guard(lessons, queries):
    validate_corpus(lessons, queries)


def test_every_query_recalls_a_lesson_that_is_taught(lessons, queries):
    """唤起一课没教过的东西，那道题**不可能**命中——指标会掉，但掉的原因是语料残缺。"""

    taught = {lesson.id for lesson in lessons}

    assert {query.recalls for query in queries} <= taught


def test_no_query_repeats_a_taught_problem(lessons, queries):
    """提问就是原题的话，量的是记忆而不是泛化。"""

    problems = {lesson.problem.strip() for lesson in lessons}

    assert [q.id for q in queries if q.problem.strip() in problems] == []


def test_the_guard_rejects_a_query_that_repeats_a_lesson(lessons, queries):
    """拒不动的守卫不是守卫。"""

    leaky = [
        *queries,
        LoopQuery(id="leak", problem=lessons[0].problem, recalls=lessons[0].id),
    ]

    with pytest.raises(ValueError, match="repeats a taught problem"):
        validate_corpus(lessons, leaky)


def test_the_guard_rejects_an_unteachable_query(lessons, queries):
    orphan = [*queries, LoopQuery(id="orphan", problem="新题", recalls="从没教过")]

    with pytest.raises(ValueError, match="not taught"):
        validate_corpus(lessons, orphan)


def test_the_corpus_covers_the_named_domains(lessons):
    domains = {lesson.domain for lesson in lessons}

    for domain in ("微积分", "组合数学", "线性代数", "代数", "复变函数", "数论"):
        assert domain in domains, f"语料里没有{domain}"


def test_lesson_ids_are_unique(lessons):
    ids = [lesson.id for lesson in lessons]

    assert len(ids) == len(set(ids))


# --- 门禁本身 ---------------------------------------------------------


def test_the_gate_rejects_an_empty_knowledge_base():
    """v0.17 之前的实测：18 课全部入库晋级，方法卡 1 张，命中率 0.056。

    不是检索差，是离线提炼器只有 7 个渐进模板，跨领域一张卡都产不出来。
    """

    report = LoopReport(
        lesson_count=18, query_count=18, captured=17, promoted=17, recalled=1
    )

    assert gate_failures(report)


def test_the_gate_rejects_knowledge_that_never_enters_the_base():
    report = LoopReport(
        lesson_count=18, query_count=18, captured=2, promoted=2, recalled=18
    )

    assert gate_failures(report)


def test_the_gate_passes_a_working_loop():
    report = LoopReport(
        lesson_count=18, query_count=18, captured=17, promoted=17, recalled=14
    )

    assert gate_failures(report) == []
