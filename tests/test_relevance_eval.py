from __future__ import annotations

from pathlib import Path

from math_harness.relevance_eval import (
    MAX_FALSE_ACCEPT_RATE,
    MIN_RETENTION_RATE,
    RelevanceReport,
    gate_failures,
    run_relevance_evaluation,
)

CORPUS = Path("data/pilot/turn_relevance.jsonl")


def test_the_shipped_corpus_passes_the_gate() -> None:
    assert gate_failures(run_relevance_evaluation(CORPUS)) == []


def test_the_corpus_covers_every_kind() -> None:
    """少一类，这条门禁就有一面看不见。

    尤其是 `chitchat_subtle`：只有它能说明规则层单独顶不住，也只有它能说明配了判定
    模型到底多出来什么。
    """

    kinds = {
        line.split('"kind": "')[1].split('"')[0]
        for line in CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert kinds == {
        "solving_grounded",
        "solving_ungrounded",
        "follow_up",
        "chitchat_obvious",
        "chitchat_subtle",
    }


def test_the_gate_rejects_a_leaky_run() -> None:
    """误收一条就不算通过。"""

    leaky = RelevanceReport(
        false_accept_rate=0.05,
        retention_rate=1.0,
        rules_obvious_block_rate=1.0,
        rules_retention_rate=1.0,
        rules_subtle_leak_rate=1.0,
        model_calls=0,
        total=40,
    )
    assert any("误收率" in item for item in gate_failures(leaky))


def test_the_gate_rejects_a_gate_that_stopped_learning() -> None:
    """**这条才是重点。** 误收率完美但保留率塌了——闸门靠什么都不做显得完美。"""

    silent = RelevanceReport(
        false_accept_rate=0.0,
        retention_rate=0.5,
        rules_obvious_block_rate=1.0,
        rules_retention_rate=1.0,
        rules_subtle_leak_rate=0.0,
        model_calls=0,
        total=40,
    )
    failures = gate_failures(silent)
    assert any("保留率" in item for item in failures)


def test_a_gate_that_blocks_everything_fails_both_ways() -> None:
    everything_blocked = RelevanceReport(
        false_accept_rate=0.0,
        retention_rate=0.0,
        rules_obvious_block_rate=1.0,
        rules_retention_rate=0.0,
        rules_subtle_leak_rate=0.0,
        model_calls=0,
        total=40,
    )
    failures = gate_failures(everything_blocked)
    assert any("保留率" in item for item in failures)
    assert any("纯规则保留率" in item for item in failures)


def test_the_thresholds_are_the_ones_the_plan_committed_to() -> None:
    assert MAX_FALSE_ACCEPT_RATE == 0.0
    assert MIN_RETENTION_RATE == 0.95


def test_the_free_layers_carry_most_of_the_corpus() -> None:
    """判定调用不该等于样本数——规则层白干了的话，每一轮对话都要多花一次钱。"""

    report = run_relevance_evaluation(CORPUS)
    assert report.model_calls < report.total
