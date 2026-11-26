"""Platzhalter wie {sensor.pv_power + sensor.x|power} auswerten – sicher, ohne eval()."""

from __future__ import annotations

import ast
import operator
import re

PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne}
DASH = "–"


class Missing(Exception):
    """Eine Entität fehlt oder ist nicht verfügbar."""


def _entity(node) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def entities_in(expr: str) -> set[str]:
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        return set()
    return {e for n in ast.walk(tree) if (e := _entity(n))}


def entities_in_template(text: str) -> set[str]:
    found: set[str] = set()
    for m in PLACEHOLDER.finditer(text or ""):
        found |= entities_in(m.group(1).split("|", 1)[0])
    return found


def evaluate(expr: str, states: dict[str, str]):
    """Rechnet einen Ausdruck aus. Einzelne Entität ohne Rechnung darf Text sein."""
    tree = ast.parse(expr.strip(), mode="eval").body

    def value(eid):
        raw = states.get(eid)
        if raw is None or raw in ("unavailable", "unknown", ""):
            raise Missing(eid)
        return raw

    if (eid := _entity(tree)) is not None:
        raw = value(eid)
        try:
            return float(raw)
        except ValueError:
            return raw

    def ev(node):
        if (eid := _entity(node)) is not None:
            try:
                return float(value(eid))
            except ValueError as exc:
                raise Missing(eid) from exc
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _OPS:
            return _OPS[type(node.ops[0])](ev(node.left), ev(node.comparators[0]))
        raise ValueError(f"Nicht erlaubter Ausdruck: {ast.dump(node)[:60]}")

    return ev(tree)


def de_number(x: float, decimals: int = 0) -> str:
    text = f"{x:,.{decimals}f}"
    return text.replace(",", " ").replace(".", ",")


def fmt_power(w: float) -> str:
    if abs(w) >= 1000:
        return f"{de_number(w / 1000, 1 if abs(w) < 100000 else 0)} kW"
    return f"{de_number(w)} W"


def apply_filter(value, flt: str) -> str:
    name, _, args = flt.partition(":")
    name = name.strip()
    if name == "power":
        return fmt_power(float(value))
    if name == "num":
        return de_number(float(value), int(args or 0))
    if name == "flow":
        pos, _, neg = args.partition(":")
        v = float(value)
        if abs(v) < 1:
            return f"0 W"
        return f"{pos if v > 0 else neg} {fmt_power(abs(v))}" if pos or neg else fmt_power(v)
    if name == "abspower":
        return fmt_power(abs(float(value)))
    if name == "sign":
        pos, _, neg = args.partition(":")
        v = float(value)
        return "" if abs(v) < 1 else (pos if v > 0 else neg)
    if name == "map":
        table = dict(item.split("=", 1) for item in args.split(",") if "=" in item)
        return table.get(str(value), str(value))
    if name == "text":
        return str(value)
    raise ValueError(f"Unbekannter Filter: {name}")


def render(text: str, states: dict[str, str]) -> str:
    def repl(m):
        expr, _, flt = m.group(1).partition("|")
        try:
            v = evaluate(expr, states)
        except Missing:
            return DASH
        except (ValueError, ZeroDivisionError, SyntaxError):
            return "?"
        if flt:
            try:
                return apply_filter(v, flt)
            except (ValueError, TypeError):
                return str(v)
        if isinstance(v, float):
            return de_number(v, 0 if v.is_integer() else 1)
        return str(v)

    return PLACEHOLDER.sub(repl, text or "")


def condition(expr: str, states: dict[str, str]) -> bool:
    try:
        return bool(evaluate(expr, states))
    except (Missing, ValueError, ZeroDivisionError, SyntaxError, TypeError):
        return False
