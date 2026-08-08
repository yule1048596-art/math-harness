from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from math_harness.conversation import ChatGeneration
from math_harness.models import (
    ConversationCreate,
    ConversationTurnRequest,
    ExampleReviewDecision,
    ExampleReviewRequest,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService

# 端到端闭环度量。
#
# 成长闭环是六步：提问 → 作答 → 检查 → 入库 → 提炼方法 → 下次遇到相似题检索出来。
# v0.15 到 v0.16，每一版都修好了其中一环，而**每一版的度量都只盯着那一版在改的东西**：
#
#   v0.15 量检查层的捕获率 —— 检查确实好了，但晋级还要渐进形状的目标；
#   v0.16 量检索的 Hit@1 —— 但评测用一个声明式提炼器**绕过了提炼**，测的是「有卡的
#         情况下能不能找到」，而生产里根本没有卡。
#
# 结果是同一类缺陷活过了三个版本。这条度量补的就是那个缺口：**一环都不绕**，默认离线
# 服务，教一批跨领域的题，再问结构相似的新题，看方法有没有真的被检索出来。
#
# **命中按来源例题判，不按方法键判。** 方法键会随提炼策略变化——阶段 C 之后它是从推导
# 结构生成的，预先写死期望键会让这条度量在自己修好之后就失效。要问的从来是「以前那道
# 题学到的东西，这次调出来了吗」。


class Lesson(BaseModel):
    """教一次：一道题和一段正确的、带推导的回答。"""

    id: str = Field(min_length=1, max_length=80)
    problem: str = Field(min_length=1, max_length=20_000)
    answer: str = Field(min_length=1, max_length=40_000)
    domain: str = Field(default="", max_length=60)


class LoopQuery(BaseModel):
    """学完之后问的新题，以及它该唤起哪一课。"""

    id: str = Field(min_length=1, max_length=80)
    problem: str = Field(min_length=1, max_length=20_000)
    #: 结构上对应哪一课。命中的判据是「检索到的方法卡里，有一张是从这一课学来的」。
    recalls: str = Field(min_length=1, max_length=80)


class LoopReport(BaseModel):
    lesson_count: int = 0
    query_count: int = 0
    #: 产生了知识草稿的课。
    captured: int = 0
    #: 晋级成功的课。
    promoted: int = 0
    #: 知识库里最终有多少张方法卡。
    method_cards: int = 0
    #: 检索到**正确来源**的提问数。
    recalled: int = 0
    #: 检索到了东西、但不是对应那一课的提问数。
    misrecalled: int = 0
    #: 什么都没检索到的提问数。
    empty: int = 0
    details: list[str] = Field(default_factory=list)

    @property
    def capture_rate(self) -> float:
        return round(self.captured / self.lesson_count, 6) if self.lesson_count else 0.0

    @property
    def recall_rate(self) -> float:
        return round(self.recalled / self.query_count, 6) if self.query_count else 0.0

    @property
    def cards_per_lesson(self) -> float:
        """教一课平均新增多少张方法卡。

        方法键从 v0.17 起是**生成式**的，不再是封闭集合。它可能把知识库切碎——同一个
        技法在不同形状下生成几个相近的键，之后每张卡的样本都太少，签名和反馈统计都
        失去意义。这个比值接近 1 就是碎了；健康的形状是先涨后平。
        """

        return (
            round(self.method_cards / self.lesson_count, 6)
            if self.lesson_count
            else 0.0
        )


class ScriptedResponder:
    """按剧本回答。

    闭环度量不该依赖某个模型今天心情如何——教材是固定的，量的是教材进去之后系统
    自己做了什么。
    """

    name = "scripted"
    model = "loop-eval"
    prompt_version = "loop-eval-v1"

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers

    def respond(self, context, message: str, max_output_tokens: int) -> ChatGeneration:
        del context, max_output_tokens
        return ChatGeneration(
            content=self.answers.get(message, "这道题我暂时答不了。"),
            provider=self.name,
            model=self.model,
        )


def _load_jsonl[ModelT: BaseModel](path: Path, model: type[ModelT]) -> list[ModelT]:
    records: list[ModelT] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                records.append(model.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    if not records:
        raise ValueError(f"dataset is empty: {path}")
    return records


def validate_corpus(lessons: Sequence[Lesson], queries: Sequence[LoopQuery]) -> None:
    """教材与提问要对得上，而且提问不能就是原题。"""

    lesson_ids = {lesson.id for lesson in lessons}
    missing = sorted({query.recalls for query in queries} - lesson_ids)
    if missing:
        raise ValueError(
            "queries recall lessons that are not taught: " + ", ".join(missing)
        )
    lesson_problems = {lesson.problem.strip() for lesson in lessons}
    leaked = [query.id for query in queries if query.problem.strip() in lesson_problems]
    if leaked:
        raise ValueError(
            "query repeats a taught problem verbatim, which measures nothing: "
            + ", ".join(sorted(leaked))
        )


def run_loop_evaluation(
    data_root: Path,
    lessons_path: Path,
    queries_path: Path,
    top_k: int = 3,
) -> LoopReport:
    """跑一遍完整闭环。**用默认的离线提炼器**——那正是要量的东西。"""

    lessons = _load_jsonl(lessons_path, Lesson)
    queries = _load_jsonl(queries_path, LoopQuery)
    validate_corpus(lessons, queries)

    service = MathHarnessService(
        data_root,
        conversation_responder=ScriptedResponder(
            {lesson.problem: lesson.answer for lesson in lessons}
        ),
    )
    workspace = service.create_workspace(
        WorkspaceCreate(name="闭环评测", description="教一批题，再问结构相似的新题。")
    )
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="教学")
    )

    report = LoopReport(lesson_count=len(lessons), query_count=len(queries))
    #: 例题 id → 课程 id。用来把检索到的方法卡追溯回它是从哪一课学来的。
    example_to_lesson: dict[str, str] = {}

    for lesson in lessons:
        result = service.send_conversation_turn(
            workspace.id,
            conversation.id,
            ConversationTurnRequest(message=lesson.problem),
        )
        draft = result.knowledge_draft
        if draft is None:
            report.details.append(f"{lesson.id}：没有产生知识草稿")
            continue
        report.captured += 1
        example_to_lesson[draft.id] = lesson.id
        try:
            service.review_example(
                workspace.id,
                draft.id,
                ExampleReviewRequest(
                    decision=ExampleReviewDecision.APPROVE,
                    expected_revision=draft.revision,
                ),
            )
            report.promoted += 1
        except Exception as exc:  # noqa: BLE001
            report.details.append(f"{lesson.id}：晋级失败 {str(exc)[:60]}")

    methods = service.list_methods(workspace.id)
    report.method_cards = len(methods)
    lessons_by_method = {
        method.key: {
            example_to_lesson[example_id]
            for example_id in method.example_ids
            if example_id in example_to_lesson
        }
        for method in methods
    }

    for query in queries:
        matches = service.search_methods(workspace.id, query.problem, top_k=top_k)
        if not matches:
            report.empty += 1
            report.details.append(f"{query.id}：什么都没检索到")
            continue
        sources: set[str] = set()
        for match in matches:
            sources |= lessons_by_method.get(match.method.key, set())
        if query.recalls in sources:
            report.recalled += 1
        else:
            report.misrecalled += 1
            report.details.append(
                f"{query.id}：检索到 {[m.method.key for m in matches]}，"
                f"但都不是从「{query.recalls}」学来的"
            )

    return report


def format_report(report: LoopReport) -> str:
    lines = [
        f"教学 {report.lesson_count} 课 · 提问 {report.query_count} 条",
        (
            f"入库 {report.captured} · 晋级 {report.promoted} · "
            f"方法卡 {report.method_cards} 张"
        ),
        (
            f"检索到正确来源 {report.recalled} · 检索错 {report.misrecalled} · "
            f"零结果 {report.empty}"
        ),
        "",
        (
            f"闭环命中率 {report.recall_rate:.3f} · "
            f"每课新增 {report.cards_per_lesson:.3f} 张卡"
        ),
    ]
    if report.details:
        lines.append("")
        lines.extend(f"  {detail}" for detail in report.details[:12])
    return "\n".join(lines)


#: 闭环命中率的下限。
#
# v0.17 之前是 0.000——不是检索差，是离线提炼器只有 7 个渐进模板，跨领域一张方法卡都
# 产不出来，库里恒空。这个门限守的是「闭环真的转起来了」。
MIN_RECALL_RATE = 0.60

#: 入库率下限。
#
# 不要求 100%：抽断言有已知的覆盖上限（回答里没有等式就抽不出断言），那是限制不是
# 回归。但掉太多就说明知识根本进不了库，后面全是空的。
MIN_CAPTURE_RATE = 0.85

#: 每课新增卡数的上限。
#
# 实测 18 课得到 11 张，比值 0.611，曲线明显先涨后平（有七课没有新增，复用了已有的
# 卡）。接近 1 就说明每课都在造新卡，知识库在碎。
MAX_CARDS_PER_LESSON = 0.80


def gate_failures(report: LoopReport) -> list[str]:
    failures: list[str] = []
    if report.recall_rate < MIN_RECALL_RATE:
        failures.append(f"闭环命中率 {report.recall_rate:.3f} < {MIN_RECALL_RATE:.3f}")
    if report.capture_rate < MIN_CAPTURE_RATE:
        failures.append(
            f"入库率 {report.capture_rate:.3f} < {MIN_CAPTURE_RATE:.3f}——"
            "知识进不了库，后面全是空的"
        )
    if report.cards_per_lesson > MAX_CARDS_PER_LESSON:
        failures.append(
            f"每课新增 {report.cards_per_lesson:.3f} 张卡 > "
            f"{MAX_CARDS_PER_LESSON:.3f}——知识库在碎，每张卡的样本会少到没有意义"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="端到端闭环度量：教一批跨领域的题，再问结构相似的新题。"
        "一环都不绕——这正是它能发现「库里根本没东西」的原因。"
    )
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument(
        "--lessons", type=Path, default=Path("data/pilot/loop_lessons.jsonl")
    )
    parser.add_argument(
        "--queries", type=Path, default=Path("data/pilot/loop_queries.jsonl")
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    report = run_loop_evaluation(args.data_root, args.lessons, args.queries, args.top_k)
    if args.json:
        print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    if args.check:
        failures = gate_failures(report)
        if failures:
            print()
            for failure in failures:
                print(f"未达门限：{failure}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
