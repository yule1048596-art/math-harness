from __future__ import annotations

import re

from math_harness.classifier import infer_query_tags
from math_harness.models import MethodCard, MethodMatch

_TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*|[\u4e00-\u9fff]")


def _tokens(text: str) -> set[str]:
    normalized = text.lower()
    atomic = set(_TOKEN_PATTERN.findall(normalized))
    chinese = "".join(char for char in normalized if "\u4e00" <= char <= "\u9fff")
    bigrams = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    return atomic | bigrams


def _jaccard(left: set[str], right: set[str]) -> float:
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
    ) -> list[MethodMatch]:
        query_tokens = _tokens(query)
        query_tags = {tag.lower() for tag in (tags or [])}
        query_tags.update(infer_query_tags(query))
        matches: list[MethodMatch] = []

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
            text_score = _jaccard(query_tokens, _tokens(method_text))
            method_tags = {tag.lower() for tag in method.tags}
            shared_tags = query_tags & method_tags
            tag_score = len(shared_tags) / max(1, len(query_tags))
            history_score = min(method.success_count / 10, 1.0)
            score = min(
                1.0, text_score * 0.45 + tag_score * 0.45 + history_score * 0.10
            )

            reasons = []
            if shared_tags:
                reasons.append(f"标签匹配：{', '.join(sorted(shared_tags))}")
            if text_score > 0:
                reasons.append("题目结构词与方法说明相似")
            if method.success_count:
                reasons.append(f"已有 {method.success_count} 个验证通过的来源案例")

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
                match.method.success_count,
                match.method.updated_at,
            ),
            reverse=True,
        )
        return matches[:top_k]
