# STAGE67_REVIEWER_CANARY: remediated by bounded reviewer-remediation subsystem.
# This file is intentionally unimported and disconnected from application/runtime code.
# It exists solely as the Stage 6.7 reviewer-remediation acceptance canary.
# Acceptance token: stage67-prod-acceptance-recovered-final-20260825-2227

_MAX_INPUT_LENGTH = 1000


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            tokens.append(("num", int(text[i:j])))
            i = j
            continue
        if ch == "+":
            tokens.append(("op", "+"))
            i += 1
            continue
        if ch == "-":
            tokens.append(("op", "-"))
            i += 1
            continue
        raise ValueError("invalid character: %r" % ch)
    return tokens


def evaluate_expression(text):
    """Safely evaluate an expression of integer literals and +/- operators.

    Bounded parser: no eval/exec; only non-negative integer literals and the
    '+' and '-' binary operators are supported. Anything else raises ValueError.
    """
    if not isinstance(text, str):
        raise TypeError("expression must be a string")
    if len(text) > _MAX_INPUT_LENGTH:
        raise ValueError("expression too long")

    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")

    result = None
    pending_op = None
    expect_operand = True

    for kind, value in tokens:
        if expect_operand:
            if kind == "num":
                result = value if result is None else _apply(result, pending_op, value)
                pending_op = None
                expect_operand = False
            elif kind == "op" and value == "-" and result is None:
                # Support a single leading unary minus.
                pending_op = "-"
            elif kind == "op" and value == "+" and result is None:
                pending_op = "+"
            else:
                raise ValueError("expected an integer operand")
        else:
            if kind != "op":
                raise ValueError("expected '+' or '-'")
            pending_op = value
            expect_operand = True

    if expect_operand:
        raise ValueError("expression ends with an operator")
    return result


def _apply(left, op, right):
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    raise ValueError("invalid operator")

