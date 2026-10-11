"""A small, safe evaluator for the subtotal formulas found in financial-statement exports.

Supported: numbers, same-sheet cell references (A1, $A$1), ranges inside SUM(),
+ - * /, unary minus and parentheses. Anything else (other functions, cross-sheet
references, text) raises FormulaError so the caller can flag it rather than guess.
Blank and text cells count as 0, matching Excel's behaviour for these formulas.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from openpyxl.utils.cell import column_index_from_string

_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<func>[A-Za-z]+)\s*\("
    r"|(?P<ref>\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)"
    r"|(?P<op>[-+*/(),])"
    r")"
)
_REF = re.compile(r"\$?([A-Za-z]{1,3})\$?(\d+)")


class FormulaError(ValueError):
    pass


CellGetter = Callable[[int, int], object]  # (column, row) -> raw cell content (number, '=formula', text or None)


def _to_ref(text: str) -> tuple[int, int]:
    m = _REF.fullmatch(text)
    if not m:
        raise FormulaError(f"Bad cell reference {text!r}")
    return column_index_from_string(m.group(1).upper()), int(m.group(2))


def tokenize(formula: str) -> list[tuple[str, str]]:
    body = formula[1:] if formula.startswith("=") else formula
    tokens, pos = [], 0
    while pos < len(body):
        if body[pos:].strip() == "":
            break
        m = _TOKEN.match(body, pos)
        if not m or m.end() == pos:
            raise FormulaError(f"Unsupported syntax at {body[pos:]!r} in {formula!r}")
        kind = m.lastgroup
        tokens.append((kind, m.group(kind)))
        pos = m.end()
    return tokens


def references_cells(formula: str) -> bool:
    try:
        return any(kind == "ref" for kind, _ in tokenize(formula))
    except FormulaError:
        return True  # unknown syntax: treat as a calculated row, never as raw input


class FormulaEvaluator:
    def __init__(self, get_cell: CellGetter):
        self._get_cell = get_cell
        self._cache: dict[tuple[int, int], Decimal] = {}
        self._active: set[tuple[int, int]] = set()

    def cell_value(self, col: int, row: int) -> Decimal:
        key = (col, row)
        if key in self._cache:
            return self._cache[key]
        if key in self._active:
            raise FormulaError(f"Circular reference at column {col}, row {row}")
        raw = self._get_cell(col, row)
        self._active.add(key)
        try:
            if isinstance(raw, str) and raw.startswith("="):
                value = self.evaluate(raw)
            elif isinstance(raw, bool) or raw is None or isinstance(raw, str):
                value = Decimal(0)
            elif isinstance(raw, (int, float)):
                value = Decimal(str(raw))
            else:
                value = Decimal(0)
        finally:
            self._active.discard(key)
        self._cache[key] = value
        return value

    def evaluate(self, formula: str) -> Decimal:
        parser = _Parser(tokenize(formula), self, formula)
        value = parser.expr()
        if parser.pos != len(parser.tokens):
            raise FormulaError(f"Trailing tokens in {formula!r}")
        return value


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], ev: FormulaEvaluator, source: str):
        self.tokens, self.ev, self.source, self.pos = tokens, ev, source, 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self, value: str | None = None) -> tuple[str, str]:
        tok = self._peek()
        if tok is None or (value is not None and tok[1] != value):
            raise FormulaError(f"Expected {value or 'token'} in {self.source!r}")
        self.pos += 1
        return tok

    def expr(self) -> Decimal:
        value = self.term()
        while (tok := self._peek()) and tok[1] in "+-" and tok[0] == "op":
            self.pos += 1
            rhs = self.term()
            value = value + rhs if tok[1] == "+" else value - rhs
        return value

    def term(self) -> Decimal:
        value = self.unary()
        while (tok := self._peek()) and tok[0] == "op" and tok[1] in "*/":
            self.pos += 1
            rhs = self.unary()
            if tok[1] == "*":
                value *= rhs
            else:
                if rhs == 0:
                    raise FormulaError(f"Division by zero in {self.source!r}")
                value /= rhs
        return value

    def unary(self) -> Decimal:
        tok = self._peek()
        if tok and tok[0] == "op" and tok[1] in "+-":
            self.pos += 1
            value = self.unary()
            return -value if tok[1] == "-" else value
        return self.primary()

    def primary(self) -> Decimal:
        kind, text = self._take()
        if kind == "num":
            try:
                return Decimal(text)
            except InvalidOperation as exc:  # pragma: no cover - regex guarantees a number
                raise FormulaError(str(exc)) from exc
        if kind == "ref":
            if ":" in text:
                raise FormulaError(f"Range {text} outside SUM() in {self.source!r}")
            return self.ev.cell_value(*_to_ref(text))
        if kind == "func":
            if text.upper() != "SUM":
                raise FormulaError(f"Unsupported function {text}() in {self.source!r}")
            return self._sum_args()
        if kind == "op" and text == "(":
            value = self.expr()
            self._take(")")
            return value
        raise FormulaError(f"Unexpected {text!r} in {self.source!r}")

    def _sum_args(self) -> Decimal:
        total = Decimal(0)
        if self._peek() == ("op", ")"):
            self.pos += 1
            return total
        while True:
            tok = self._peek()
            if tok and tok[0] == "ref" and ":" in tok[1]:
                self.pos += 1
                start, end = tok[1].split(":")
                (c1, r1), (c2, r2) = _to_ref(start), _to_ref(end)
                for col in range(min(c1, c2), max(c1, c2) + 1):
                    for row in range(min(r1, r2), max(r1, r2) + 1):
                        total += self.ev.cell_value(col, row)
            else:
                total += self.expr()
            kind, text = self._take()
            if text == ")":
                return total
            if text != ",":
                raise FormulaError(f"Expected ',' or ')' in {self.source!r}")
