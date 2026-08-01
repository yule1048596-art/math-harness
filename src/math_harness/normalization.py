from __future__ import annotations

import re
from dataclasses import dataclass

from math_harness.models import CandidateSolution, SolveMathTarget, VerificationMode

_CARET_PATTERN = re.compile(r"(?<!\*)\^(?!\*)")
_LOWERCASE_E_PATTERN = re.compile(r"(?<![A-Za-z0-9_])e(?![A-Za-z0-9_])")
_LN_PATTERN = re.compile(r"(?<![A-Za-z0-9_])ln(?=\s*\()")


@dataclass(frozen=True)
class CandidateNormalization:
    candidate: CandidateSolution
    actions: tuple[str, ...]


class CandidateSolutionNormalizer:
    """Normalize common model notation into the safe parser's expression grammar."""

    def normalize(
        self,
        candidate: CandidateSolution,
        math_target: SolveMathTarget | None,
    ) -> CandidateNormalization:
        expression = candidate.answer_expression
        if expression is None:
            return CandidateNormalization(candidate=candidate, actions=())

        normalized = expression.strip()
        actions: list[str] = []

        normalized, changed = self._strip_math_delimiters(normalized)
        if changed:
            actions.append("strip_math_delimiters")

        character_replacements = {
            "−": "-",
            "–": "-",
            "×": "*",
            "·": "*",
            "÷": "/",
            "π": "pi",
            "ℯ": "E",
        }
        replaced = normalized.translate(str.maketrans(character_replacements))
        if replaced != normalized:
            normalized = replaced
            actions.append("normalize_unicode_math")

        replaced = _CARET_PATTERN.sub("**", normalized)
        if replaced != normalized:
            normalized = replaced
            actions.append("normalize_power_operator")

        replaced = _LN_PATTERN.sub("log", normalized)
        if replaced != normalized:
            normalized = replaced
            actions.append("normalize_log_function")

        declared_symbols = (
            {math_target.variable, *math_target.parameters} if math_target else set()
        )
        if "e" not in declared_symbols:
            replaced = _LOWERCASE_E_PATTERN.sub("E", normalized)
            if replaced != normalized:
                normalized = replaced
                actions.append("normalize_e_constant")

        if (
            math_target is not None
            and math_target.mode is VerificationMode.ASYMPTOTIC_EXPANSION
            and math_target.remainder_power is not None
        ):
            normalized, changed = self._strip_trailing_order_term(normalized)
            if changed:
                actions.append("strip_trailing_order_term")

        normalized = normalized.strip()
        if not normalized or normalized == expression:
            return CandidateNormalization(candidate=candidate, actions=tuple(actions))
        return CandidateNormalization(
            candidate=candidate.model_copy(
                update={"answer_expression": normalized},
            ),
            actions=tuple(actions),
        )

    @staticmethod
    def _strip_math_delimiters(expression: str) -> tuple[str, bool]:
        pairs = (("$", "$"), (r"\(", r"\)"), (r"\[", r"\]"))
        for prefix, suffix in pairs:
            if expression.startswith(prefix) and expression.endswith(suffix):
                return expression[len(prefix) : -len(suffix)].strip(), True
        return expression, False

    @staticmethod
    def _strip_trailing_order_term(expression: str) -> tuple[str, bool]:
        candidates: list[int] = []
        for marker in ("O(", "o("):
            start = expression.rfind(marker)
            if start >= 0:
                candidates.append(start)

        for start in sorted(candidates, reverse=True):
            if start > 0 and (
                expression[start - 1].isalnum() or expression[start - 1] == "_"
            ):
                continue

            depth = 0
            closing_index: int | None = None
            for index in range(start + 1, len(expression)):
                character = expression[index]
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                    if depth == 0:
                        closing_index = index
                        break
            if closing_index is None or expression[closing_index + 1 :].strip():
                continue

            prefix = expression[:start].rstrip()
            if prefix.endswith(("+", "-")):
                prefix = prefix[:-1].rstrip()
            if prefix:
                return prefix, True
        return expression, False
