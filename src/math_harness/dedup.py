from __future__ import annotations

import os
from dataclasses import dataclass

from math_harness.models import KnowledgeStatus, MethodCard, MethodDraft
from math_harness.retrieval import jaccard, tokens
from math_harness.structure import MethodSignature, signature_similarity

# LLM 提取器给同一个方法起不同 slug 时会各建一张卡（`upsert_method` 按
# `(workspace_id, method_key)` 精确匹配）。语料一大，知识库就开始碎片化：检索在
# 重复卡片里挑，成功计数也被摊薄。
#
# 这里只负责「发现可能重复」，不自动合并。误合并会永久丢失知识，而合并的收益
# 只是整洁——这个不对称决定了默认必须是人工确认。

DEFAULT_THRESHOLD = 0.75


@dataclass(frozen=True)
class MergeCandidate:
    """一对疑似重复的方法卡。primary 是建议保留的那张。"""

    primary_id: str
    primary_key: str
    duplicate_id: str
    duplicate_key: str
    score: float
    signature_similarity: float
    text_similarity: float
    reasons: list[str]


def load_threshold() -> float:
    raw = os.getenv("MATH_HARNESS_DEDUP_THRESHOLD", "").strip()
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_THRESHOLD
    return min(max(value, 0.0), 1.0)


def card_to_draft(card: MethodCard) -> MethodDraft:
    """把方法卡还原成草稿，好复用已有的并集累积逻辑做合并。"""

    return MethodDraft(
        key=card.key,
        name=card.name,
        goal=card.goal,
        applicable_when=list(card.applicable_when),
        procedure=list(card.procedure),
        failure_modes=list(card.failure_modes),
        tags=list(card.tags),
    )


def _card_text(card: MethodCard) -> str:
    return "\n".join(
        [
            card.name,
            card.goal,
            *card.applicable_when,
            *card.procedure,
            *card.failure_modes,
        ]
    )


def _order(left: MethodCard, right: MethodCard) -> tuple[MethodCard, MethodCard]:
    """主卡选择必须确定性：样本多者为主，并列取更早创建的那张。"""

    left_samples = MethodSignature.model_validate(left.signature or {}).sample_count
    right_samples = MethodSignature.model_validate(right.signature or {}).sample_count
    left_rank = (-left_samples, left.created_at, left.id)
    right_rank = (-right_samples, right.created_at, right.id)
    return (left, right) if left_rank <= right_rank else (right, left)


def find_merge_candidates(
    methods: list[MethodCard],
    threshold: float | None = None,
) -> list[MergeCandidate]:
    """两两比较，返回超过阈值的疑似重复对，按相似度降序。"""

    cutoff = load_threshold() if threshold is None else threshold
    active = [
        method for method in methods if method.status is not KnowledgeStatus.DEPRECATED
    ]
    signatures = {
        method.id: MethodSignature.model_validate(method.signature or {})
        for method in active
    }
    texts = {method.id: tokens(_card_text(method)) for method in active}

    candidates: list[MergeCandidate] = []
    for index, left in enumerate(active):
        for right in active[index + 1 :]:
            if left.key == right.key:
                continue
            structure = signature_similarity(signatures[left.id], signatures[right.id])
            text = jaccard(texts[left.id], texts[right.id])
            score = round(structure * 0.5 + text * 0.5, 6)
            if score < cutoff:
                continue

            primary, duplicate = _order(left, right)
            reasons = [f"综合相似度 {score:.2f}"]
            if structure > 0:
                reasons.append(f"结构签名重合 {structure:.2f}")
            if text > 0:
                reasons.append(f"文本重合 {text:.2f}")
            candidates.append(
                MergeCandidate(
                    primary_id=primary.id,
                    primary_key=primary.key,
                    duplicate_id=duplicate.id,
                    duplicate_key=duplicate.key,
                    score=score,
                    signature_similarity=structure,
                    text_similarity=text,
                    reasons=reasons,
                )
            )

    candidates.sort(
        key=lambda item: (-item.score, item.primary_key, item.duplicate_key)
    )
    return candidates
