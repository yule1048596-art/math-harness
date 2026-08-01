from __future__ import annotations

from dataclasses import dataclass, field

from math_harness.models import MethodCard, MethodDraft

# 每个字段的硬上限。没有上限的话，连续摄取几百道题会把方法卡撑成垃圾场，
# 检索和人工复核都会失效。超限时按首次出现顺序保留，即旧条目优先。
MAX_APPLICABLE_WHEN = 20
MAX_PROCEDURE = 15
MAX_FAILURE_MODES = 20
MAX_TAGS = 24


@dataclass(frozen=True)
class MergedMethodContent:
    """并集累积后的方法卡内容，以及本次实际新增了什么。"""

    name: str
    goal: str
    applicable_when: list[str]
    procedure: list[str]
    failure_modes: list[str]
    tags: list[str]
    changed: bool
    added: dict[str, list[str]] = field(default_factory=dict)


def _normalize(value: str) -> str:
    """去重用的规范形式：折叠空白并忽略大小写，但保留原文入库。"""

    return " ".join(value.split()).casefold()


def _union_preserving_order(
    existing: list[str],
    incoming: list[str],
    limit: int,
) -> tuple[list[str], list[str]]:
    """旧条目在前，新条目按首次出现顺序追加；超过 limit 后不再接收新条目。"""

    merged: list[str] = []
    seen: set[str] = set()
    for item in existing:
        key = _normalize(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)

    added: list[str] = []
    for item in incoming:
        key = _normalize(item)
        if not key or key in seen:
            continue
        if len(merged) >= limit:
            break
        seen.add(key)
        merged.append(item)
        added.append(item)

    return merged[:limit], added


def _prefer_existing(existing: str, incoming: str) -> str:
    """首版优先；仅当现有内容为空时才采纳新内容。"""

    return existing if existing.strip() else incoming


def merge_method_content(
    existing: MethodCard,
    draft: MethodDraft,
) -> MergedMethodContent:
    """把新抽取的方法草稿并入已有方法卡。

    `applicable_when`、`failure_modes`、`tags` 做并集累积——这是方法卡随例子
    变多而真正长出新理解的地方。`name`、`goal`、`procedure` 保留首版：procedure
    是一段有序算法，把不同例子的步骤混在一起只会得到不连贯的流程。
    """

    applicable_when, added_applicable = _union_preserving_order(
        existing.applicable_when,
        draft.applicable_when,
        MAX_APPLICABLE_WHEN,
    )
    failure_modes, added_failures = _union_preserving_order(
        existing.failure_modes,
        draft.failure_modes,
        MAX_FAILURE_MODES,
    )
    tags, added_tags = _union_preserving_order(
        existing.tags,
        draft.tags,
        MAX_TAGS,
    )

    name = _prefer_existing(existing.name, draft.name)
    goal = _prefer_existing(existing.goal, draft.goal)
    procedure = existing.procedure or draft.procedure[:MAX_PROCEDURE]

    changed = (
        name != existing.name
        or goal != existing.goal
        or procedure != existing.procedure
        or applicable_when != existing.applicable_when
        or failure_modes != existing.failure_modes
        or tags != existing.tags
    )

    added = {
        key: value
        for key, value in (
            ("applicable_when", added_applicable),
            ("failure_modes", added_failures),
            ("tags", added_tags),
        )
        if value
    }

    return MergedMethodContent(
        name=name,
        goal=goal,
        applicable_when=applicable_when,
        procedure=procedure,
        failure_modes=failure_modes,
        tags=tags,
        changed=changed,
        added=added,
    )
