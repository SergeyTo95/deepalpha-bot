"""Stage 6.7 production acceptance canary (remediated).

This file is standalone and unimported. It exists only to verify that the
independent Senior Reviewer blocks a high-severity finding and that the
bounded reviewer-remediation loop repairs this exact file head.

Acceptance token: stage67-prod-acceptance-20260825-1629
"""

import re

_TOKEN_PATTERN = re.compile(r"\s*(\d+|[+-])")


def evaluate_expression(text):
    """Evaluate an arithmetic expression string.

    Supports only non-negative integer literals combined with '+' and '-'
    operators, with optional surrounding whitespace. Leading '+'/'-' is
    treated as a unary sign applied to the first literal. Any other input
    raises ValueError. No eval/exec or arbitrary code execution is used.
    """
    if not isinstance(text, str):
        raise ValueError("expression must be a string")

    tokens = []
    position = 0
    while position < len(text):
        match = _TOKEN_PATTERN.match(text, position)
        if match is None:
            raise ValueError("invalid token in expression: %r" % text)
        tokens.append(match.group(1))
        position = match.end()
        while position < len(text) and text[position].isspace():
            position += 1

    if not tokens:
        raise ValueError("empty expression")

    total = 0
    sign = 1
    expect_operand = True
    for token in tokens:
        if expect_operand:
            if token == "+":
                sign = 1
            elif token == "-":
                sign = -sign
            elif token.isdigit():
                total += sign * int(token)
                sign = 1
                expect_operand = False
            else:
                raise ValueError("invalid token in expression: %r" % text)
        else:
            if token == "+":
                sign = 1
            elif token == "-":
                sign = -1
            else:
                raise ValueError("missing operator in expression: %r" % text)
            expect_operand = True

    if expect_operand:
        raise ValueError("expression ends with an operator: %r" % text)

    return total
