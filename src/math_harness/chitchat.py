from __future__ import annotations

import re

# 「整条消息就是一句寒暄」的判据。
#
# 单独成模块是为了没有依赖：入库门禁（`relevance`）和知识库列表（`models`）都要用它，
# 而 `relevance` 依赖 `claim_drafting`，`claim_drafting` 又依赖 `models`——放在任何一边
# 都会绕出一个循环。

#: 整条消息就是这么一句时，它不可能是在解题。
#:
#: 这张表**刻意只收最明显的**，而且必须**整条匹配**——「你好，帮我求 x^3 的导数」不算。
#: 它的作用不是判断「什么是数学」（那个判不准，也不该用关键词判），而是在没配判定模型
#: 时守住最常见的那一类。宁可漏掉九成闲聊，也不能误伤一条解题。
OBVIOUS_CHITCHAT = frozenset(
    {
        "你好", "您好", "嗨", "哈喽", "hi", "hello", "hey",
        "早上好", "中午好", "晚上好", "晚安", "在吗", "在么",
        "谢谢", "谢谢你", "多谢", "感谢", "thanks", "thankyou", "thx",
        "再见", "拜拜", "bye", "ok", "好的", "好", "嗯", "哈哈", "哈哈哈",
        "你是谁", "你叫什么", "你能干什么", "你会什么", "你能做什么",
    }
)  # fmt: skip

#: 匹配前要剥掉的东西：空白、标点、以及表情符号那一段。
_STRIP_FOR_MATCH = re.compile(r"[\s\W_]+", re.UNICODE)


def is_obvious_chitchat(text: str) -> bool:
    """整条消息完全等于表里的一句时为真。差一个字就不算。"""

    normalized = _STRIP_FOR_MATCH.sub("", text).lower()
    return bool(normalized) and normalized in OBVIOUS_CHITCHAT
