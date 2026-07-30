from __future__ import annotations

import pytest

from math_harness.errors import UnsafeExpression
from math_harness.math_parser import SafeMathParser


def test_safe_parser_accepts_allowlisted_math():
    parser = SafeMathParser()
    expression = parser.parse("sqrt(x**2 + x) - x", {"x"})
    assert str(expression) == "-x + sqrt(x**2 + x)"


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os').system('echo unsafe')",
        "x.__class__",
        "[x for x in (1, 2)]",
        "open('/tmp/unsafe')",
    ],
)
def test_safe_parser_rejects_python_execution(source):
    parser = SafeMathParser()
    with pytest.raises(UnsafeExpression):
        parser.parse(source, {"x"})
