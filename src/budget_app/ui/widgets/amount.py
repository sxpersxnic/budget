"""An amount field that also takes a sum like 12.50+8.30 (F06)."""

from __future__ import annotations

import ast
import operator
from typing import Any

from nicegui import ui
from nicegui.events import GenericEventArguments

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def evaluate(text: str | None) -> float | None:
    """The value of a number or a simple sum (+ − × ÷ and brackets), or None while it isn't one.

    Swiss input is welcome: apostrophes as thousands separators (1'234.50), a comma as decimal
    point when there is no dot (12,50), and × ÷ − for * / -. Never evaluates anything else.
    """
    if text is None:
        return None
    s = str(text).strip().replace("'", "").replace("’", "").replace(" ", "")
    s = s.replace("×", "*").replace("÷", "/").replace("−", "-")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    if not s:
        return None
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError:
        return None
    try:
        value = _eval(tree.body)
    except (ValueError, ZeroDivisionError):
        return None
    return round(value, 2)


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    raise ValueError("not a sum")


class AmountInput(ui.number):
    """A number field that accepts sums; the result replaces the sum when the field is left."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._props["type"] = "text"  # a number input would refuse "+"
        self._props["inputmode"] = "decimal"

    def _event_args_to_value(self, e: GenericEventArguments) -> float | None:
        return evaluate(e.args)
