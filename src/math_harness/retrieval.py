from __future__ import annotations

import re

from math_harness.classifier import infer_query_tags
from math_harness.models import MethodCard, MethodMatch
from math_harness.structure import (
    MethodSignature,
    StructuralFeatures,
    path_idf,
    signature_score,
)

_TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*|[\u4e00-\u9fff]")


def tokens(text: str) -> set[str]:
    normalized = text.lower()
    atomic = set(_TOKEN_PATTERN.findall(normalized))
    chinese = "".join(char for char in normalized if "\u4e00" <= char <= "\u9fff")
    bigrams = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    return atomic | bigrams


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class MethodRetriever:
    def search(
        self,
        methods: list[MethodCard],
        query: str,
        tags: list[str] | None = None,
        top_k: int = 5,
        features: StructuralFeatures | None = None,
    ) -> list[MethodMatch]:
        query_tokens = tokens(query)
        query_tags = {tag.lower() for tag in (tags or [])}
        query_tags.update(infer_query_tags(query))
        # 没有结构信息时完全走原有词面打分，行为与 v0.3.x 一致。
        use_structure = features is not None and not features.is_empty
        matches: list[MethodMatch] = []

        signatures = {
            method.id: MethodSignature.model_validate(method.signature or {})
            for method in methods
        }
        # 路径 IDF 只对本次候选集有意义，现算一次给所有方法共用。
        idf = path_idf(list(signatures.values())) if use_structure else {}

        for method in methods:
            method_text = "\n".join(
                [
                    method.name,
                    method.goal,
                    *method.applicable_when,
                    *method.procedure,
                    *method.failure_modes,
                ]
            )
            text_score = jaccard(query_tokens, tokens(method_text))
            method_tags = {tag.lower() for tag in method.tags}
            shared_tags = query_tags & method_tags
            tag_score = len(shared_tags) / max(1, len(query_tags))
            history_volume = min(method.success_count / 10, 1.0)
            reliability = (method.success_count + 1) / (
                method.success_count + method.failure_count + 1
            )
            history_score = history_volume * reliability
            structure_score = (
                signature_score(signatures[method.id], features, idf)
                if use_structure
                else 0.0
            )
            if use_structure:
                score = min(
                    1.0,
                    structure_score * 0.45
                    + text_score * 0.20
                    + tag_score * 0.25
                    + history_score * 0.10,
                )
            else:
                score = min(
                    1.0, text_score * 0.45 + tag_score * 0.45 + history_score * 0.10
                )

            reasons = []
            if structure_score > 0:
                reasons.append(f"数学结构契合度 {structure_score:.2f}")
            if shared_tags:
                reasons.append(f"标签匹配：{', '.join(sorted(shared_tags))}")
            if text_score > 0:
                reasons.append("题目结构词与方法说明相似")
            if method.success_count:
                reasons.append(f"已有 {method.success_count} 个验证通过的来源案例")
            if method.failure_count:
                reasons.append(f"已有 {method.failure_count} 次失败反馈")

            if score > 0:
                matches.append(
                    MethodMatch(
                        method=method,
                        score=round(score, 4),
                        reasons=reasons,
                    )
                )

        matches.sort(
            key=lambda match: (
                match.score,
                match.method.success_count - match.method.failure_count,
                -match.method.failure_count,
                match.method.updated_at,
            ),
            reverse=True,
        )
        return matches[:top_k]
