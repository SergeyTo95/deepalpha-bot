"""Small exact numeric calculator. Never pass user input to eval/sympify."""
import ast
import math
import operator
from fractions import Fraction


class CalculationError(ValueError):
    code = "calculation_invalid_or_too_complex"
    status = 400


def calculate(expression):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 256:
        raise CalculationError()
    try:
        root = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(root)) > 64:
            raise CalculationError()

        def bounded(value):
            if value.numerator.bit_length() > 1024 or value.denominator.bit_length() > 1024:
                raise CalculationError()
            return value

        def visit(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                raw = ast.get_source_segment(expression, node)
                if len(raw) > 24 or (isinstance(node.value, float) and not math.isfinite(node.value)):
                    raise CalculationError()
                return bounded(Fraction(raw))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            if isinstance(node, ast.BinOp):
                left, right = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Pow):
                    if right.denominator != 1 or abs(right) > 12:
                        raise CalculationError()
                    return bounded(left ** int(right))
                operations = {ast.Add: operator.add, ast.Sub: operator.sub,
                              ast.Mult: operator.mul, ast.Div: operator.truediv}
                if type(node.op) in operations:
                    return bounded(operations[type(node.op)](left, right))
            raise CalculationError()

        value = visit(root.body)
        return {"expression": expression, "exact": str(value), "decimal": format(float(value), ".15g"),
                "engine": "local_exact_arithmetic", "external_api": False}
    except (SyntaxError, ValueError, TypeError, ArithmeticError, RecursionError):
        raise CalculationError() from None


def calculator_tool(user_id, arguments):
    return calculate(arguments.get("expression"))
