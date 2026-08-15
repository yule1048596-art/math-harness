from __future__ import annotations

import re

from math_harness.checks.confidence import retrieval_weight
from math_harness.classifier import infer_query_tags
from math_harness.models import MethodCard, MethodMatch
from math_harness.structure import (
    MethodSignature,
    StructuralFeatures,
    path_background,
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


#: 从提问文本推断出的结构在打分里占的权重。
#
# 用户确认过的目标拿 0.45，推断出来的只拿这一档，因为它明显更弱：抽片段可能抓错东西，
# 而且没有「主变量」可言（全部符号一视同仁），表示本身就更粗。当个**打平时的裁决者**
# 是它该有的位置。
#
# 跨领域评测上扫过一遍：[0.02, 0.10] 这一整段结果完全相同（整体 0.596），0.12 起
# cross_family 开始掉——被表面形状骗走。取平台中部而不是边缘，也不取扫描出来的最大值，
# 47 道题上 0.596 与 0.553 只差两道，那点差别不足以支撑一个精确的选择。
_INFERRED_STRUCTURE_WEIGHT = 0.08


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
        inferred_structure: bool = False,
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
        # 版本对不上的签名一律不参与结构那一路。
        #
        # 它是**过去某个提取器**算出来的，和现在算出来的查询特征不在同一个空间里。
        # 拿它们比不会报错，只会给出一个没有意义的相似度——安静出错比不出结果糟得多。
        # IDF 和背景分布也只能在同版本的签名之间统计，否则整个候选集的分布都是歪的。
        current = {
            method_id: signature
            for method_id, signature in signatures.items()
            if signature.is_current
        }
        idf = path_idf(list(current.values())) if use_structure else {}
        # 背景分布把打分从似然折算成后验：一条路径若在所有方法里都常见，命中它
        # 不构成证据。
        background = path_background(list(current.values())) if use_structure else {}

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
            # 这张卡这一轮能不能用结构那一路。签名版本过期时它单独退回词面公式——
            # 不是给它一个 0 分的结构项。给 0 分等于让过期的卡片系统性地排在后面，
            # 哪怕它就是正确答案；退回词面是让它在同一套词面权重下公平竞争。
            method_uses_structure = use_structure and signatures[method.id].is_current
            structure_score = (
                signature_score(signatures[method.id], features, idf, background)
                if method_uses_structure
                else 0.0
            )
            if method_uses_structure and inferred_structure:
                # 从提问文本猜出来的结构，权重要比用户确认过的目标低。
                #
                # 两个理由，都是实测出来的：抽片段可能抓错东西；而提问的词面本身携带
                # 着结构给不出的信息——裸的 2x2 矩阵判不出是求行列式还是求特征值，
                # 「行列式」「特征值」这几个字才是判据。按 0.45 的原权重，cross_family
                # 从 0.500 掉到 0.333，正是被表面形状骗走。
                score = min(
                    1.0,
                    structure_score * _INFERRED_STRUCTURE_WEIGHT
                    + text_score * 0.30
                    + tag_score * 0.35
                    + history_score * 0.10,
                )
            elif method_uses_structure:
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

            # 按来源可信度加权。
            #
            # 未验证的内容进了知识库就会被检索到——那是用户要的。但它不该盖过验证过的
            # 内容：「越用越强」的前提是强的那部分排在前面。旧卡没有这个字段，按
            # `verified` 折算（权重 1.0），所以既有排序一位不动。
            score *= retrieval_weight(method.confidence)

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
