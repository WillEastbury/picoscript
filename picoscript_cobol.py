#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_cobol.py -- COBOL-style PicoScript frontend.
Reuses the shared BASIC AST nodes + Lowerer exactly like the English and
Python frontends. Supports DATA/PROCEDURE DIVISION, 01 VALUE items, MOVE,
COMPUTE, DISPLAY, IF/ELSE/END-IF, EVALUATE/WHEN/OTHER, PERFORM paragraphs,
PERFORM VARYING ... UNTIL ... END-PERFORM, arithmetic verbs, STOP RUN, and
Ns.Method(args). host-call statements.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set
from picoscript_basic import (
    Num, Str, Var, Bin, Cmp, Call, Let, Ternary, If, While, DoLoop, ForTo, ForEach,
    Switch, Goto, Label, Sub, Gosub, Return, Break, Skip, Print, CallStmt, Lowerer,
    Dispatch, TryExcept, Raise,
)
KEYWORDS = {
    "IDENTIFICATION", "DIVISION", "PROGRAM-ID", "DATA", "PROCEDURE",
    "WORKING-STORAGE", "SECTION", "PIC", "VALUE",
    "MOVE", "TO", "COMPUTE", "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE",
    "GIVING", "FROM", "BY", "DISPLAY",
    "IF", "ELSE", "END-IF",
    "EVALUATE", "WHEN", "OTHER", "END-EVALUATE",
    "PERFORM", "VARYING", "UNTIL", "END-PERFORM",
    "STOP", "RUN",
    "NOT", "AND", "OR", "IS",
    "GREATER", "LESS", "EQUAL", "THAN",
}
_PREC = {
    "OR": 1, "AND": 2,
    "=": 3, "==": 3, "!=": 3, "<>": 3, "<": 3, ">": 3, "<=": 3, ">=": 3,
    "+": 5, "-": 5, "*": 6, "/": 6,
}
_CMP = {"=": "EQ", "==": "EQ", "!=": "NE", "<>": "NE", "<": "LT", ">": "GT", "<=": "LE", ">=": "GE"}
_BIN = {"+": "+", "-": "-", "*": "*", "/": "/", "AND": "AND", "OR": "OR"}
_TWO = {"==", "!=", "<=", ">=", "<>"}
_ONE = set("+-*/()<>=,.:")
@dataclass
class Tok:
    kind: str          # num,id,kw,str,op,nl,eof
    value: str
    line: int
    pos: int = -1
def tokenize(src: str) -> List[Tok]:
    toks: List[Tok] = []
    i, n, line = 0, len(src), 1
    while i < n:
        c = src[i]
        start = i
        if c == "\n":
            toks.append(Tok("nl", "\\n", line, start))
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if c == "*" and i + 1 < n and src[i + 1] == ">":   # COBOL inline comment
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "*" and (i == 0 or src[i - 1] == "\n"):    # simple comment line
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == '"' or c == "'":
            quote = c
            j = i + 1
            buf = []
            while j < n and src[j] != quote:
                if src[j] == "\\" and j + 1 < n:
                    nxt = src[j + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'"}.get(nxt, nxt))
                    j += 2
                else:
                    buf.append(src[j])
                    j += 1
            if j >= n:
                raise SyntaxError(f"line {line}: unterminated string")
            toks.append(Tok("str", "".join(buf), line, start))
            i = j + 1
            continue
        if c.isdigit():
            j = i
            if c == "0" and j + 1 < n and src[j + 1] in "xX":
                j += 2
                while j < n and src[j] in "0123456789abcdefABCDEF":
                    j += 1
            else:
                while j < n and src[j].isdigit():
                    j += 1
            toks.append(Tok("num", src[i:j], line, start))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_-"):
                j += 1
            word = src[i:j].upper()
            toks.append(Tok("kw" if word in KEYWORDS else "id", word, line, start))
            i = j
            continue
        two = src[i:i + 2]
        if two in _TWO:
            toks.append(Tok("op", two, line, start))
            i += 2
            continue
        if c in _ONE:
            toks.append(Tok("op", c, line, start))
            i += 1
            continue
        raise SyntaxError(f"line {line}: unexpected char {c!r}")
    toks.append(Tok("nl", "\\n", line, n))
    toks.append(Tok("eof", "", line, n))
    return toks
class Parser:
    def __init__(self, toks: Sequence[Tok]):
        self.toks = list(toks)
        self.i = 0
    def peek(self, k: int = 0) -> Tok:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else self.toks[-1]
    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t
    def at(self, kind: str, value: Optional[str] = None) -> bool:
        t = self.peek()
        return t.kind == kind and (value is None or t.value == value)
    def at_kw(self, *names: str) -> bool:
        t = self.peek()
        return t.kind == "kw" and t.value in names
    def word_at(self, k: int) -> Optional[str]:
        t = self.peek(k)
        return t.value if t.kind in ("kw", "id") else None
    def expect(self, kind: str, value: Optional[str] = None) -> Tok:
        t = self.next()
        if t.kind != kind or (value is not None and t.value != value):
            want = value if value is not None else kind
            raise SyntaxError(f"line {t.line}: expected {want!r}, got {t.value!r} ({t.kind})")
        return t
    def expect_kw(self, name: str) -> Tok:
        t = self.next()
        if t.kind != "kw" or t.value != name: raise SyntaxError(f"line {t.line}: expected {name!r}, got {t.value!r}")
        return t
    def expect_name(self) -> str:
        t = self.next()
        if t.kind != "id": raise SyntaxError(f"line {t.line}: expected identifier, got {t.value!r}")
        return t.value
    def skip_nl(self):
        while self.at("nl"): self.next()
    def end_simple(self):
        if self.at("op", "."): self.next()
        if self.at("nl"):
            self.skip_nl()
            return
        if self.at("eof"): return
        t = self.peek()
        raise SyntaxError(f"line {t.line}: expected end of statement, got {t.value!r}")
    def end_header(self):
        if self.at("nl"):
            self.skip_nl()
            return
        if self.at("eof"): return
        t = self.peek()
        raise SyntaxError(f"line {t.line}: expected end of header line, got {t.value!r}")
    def skip_sentence(self):
        while not self.at("eof") and not self.at("op", "."): self.next()
        if self.at("op", "."): self.next()
        self.skip_nl()
    def _at_division(self, name: str) -> bool:
        return self.at_kw(name) and self.peek(1).kind == "kw" and self.peek(1).value == "DIVISION"
    def _consume_division_header(self, name: str):
        self.expect_kw(name)
        self.expect_kw("DIVISION")
        self.expect("op", ".")
        self.skip_nl()
    def _at_section_header(self) -> bool:
        return self.peek().kind in ("kw", "id") and self.peek(1).kind == "kw" and self.peek(1).value == "SECTION"
    def _at_paragraph_header(self) -> bool:
        return self.peek().kind == "id" and self.peek(1).kind == "op" and self.peek(1).value == "." and self.peek(2).kind == "nl"
    def _at_block_end(self, names: Set[str]) -> bool:
        return self.peek().kind == "kw" and self.peek().value in names
    def parse_program(self) -> List[object]:
        decls: List[object] = []
        main: List[object] = []
        subs: List[Sub] = []
        self.skip_nl()
        while not self.at("eof"):
            if self._at_division("IDENTIFICATION"):
                self._consume_division_header("IDENTIFICATION")
                continue
            if self._at_division("DATA"):
                self._consume_division_header("DATA")
                decls.extend(self.parse_data_division())
                continue
            if self._at_division("PROCEDURE"):
                self._consume_division_header("PROCEDURE")
                body, more_subs = self.parse_procedure_division()
                main.extend(body)
                subs.extend(more_subs)
                break
            self.skip_sentence()
        return decls + main + subs
    def parse_data_division(self) -> List[object]:
        out: List[object] = []
        while not self.at("eof") and not self._at_division("PROCEDURE"):
            self.skip_nl()
            if self.at("eof") or self._at_division("PROCEDURE"): break
            if self._at_section_header():
                self.skip_sentence()
                continue
            if self.peek().kind == "num":
                item = self.parse_data_item()
                if item is not None: out.append(item)
                continue
            self.skip_sentence()
        return out
    def parse_data_item(self) -> Optional[Let]:
        self.expect("num")
        name = self.expect_name()
        init: object = Num(0)
        while not self.at("eof") and not self.at("op", "."):
            if self.at_kw("VALUE"):
                self.next()
                init = self.parse_expr()
                break
            self.next()
        self.expect("op", ".")
        self.skip_nl()
        return Let(name, init)
    def parse_procedure_division(self):
        body: List[object] = []
        subs: List[Sub] = []
        self.skip_nl()
        while not self.at("eof"):
            if self._at_paragraph_header():
                subs.append(self.parse_paragraph())
            else:
                stmt = self.parse_stmt()
                if stmt is not None:
                    body.extend(stmt) if isinstance(stmt, list) else body.append(stmt)
        return body, subs
    def parse_paragraph(self) -> Sub:
        name = self.expect_name()
        self.expect("op", ".")
        self.skip_nl()
        return Sub(name, self.parse_block(set(), stop_on_paragraph=True))
    def parse_block(self, stop_names: Set[str], *, stop_on_paragraph: bool = False) -> List[object]:
        out: List[object] = []
        while True:
            self.skip_nl()
            if self.at("eof"): break
            if stop_names and self._at_block_end(stop_names): break
            if stop_on_paragraph and self._at_paragraph_header(): break
            stmt = self.parse_stmt()
            if stmt is None: continue
            out.extend(stmt) if isinstance(stmt, list) else out.append(stmt)
        return out
    def parse_stmt(self) -> Optional[object]:
        self.skip_nl()
        start = self.peek().pos
        node = self._parse_stmt()
        if node is not None:
            try: node.pos = start
            except (AttributeError, TypeError): pass
        return node
    def _parse_stmt(self) -> Optional[object]:
        t = self.peek()
        if t.kind == "kw":
            if t.value == "MOVE":
                return self.parse_move()
            if t.value == "COMPUTE":
                return self.parse_compute()
            if t.value == "DISPLAY":
                return self.parse_display()
            if t.value == "IF":
                return self.parse_if()
            if t.value == "EVALUATE":
                return self.parse_evaluate()
            if t.value == "PERFORM":
                return self.parse_perform()
            if t.value == "ADD":
                return self.parse_add()
            if t.value == "SUBTRACT":
                return self.parse_subtract()
            if t.value == "MULTIPLY":
                return self.parse_multiply()
            if t.value == "DIVIDE":
                return self.parse_divide()
            if t.value == "STOP":
                return self.parse_stop_run()
        if t.kind == "id" and self.peek(1).kind == "op" and self.peek(1).value == ".":
            if self.peek(2).kind in ("id", "kw") and self.peek(3).kind == "op" and self.peek(3).value == "(":
                call = self.parse_call_from_id()
                self.end_simple()
                return CallStmt(call)
        raise SyntaxError(f"line {t.line}: cannot parse statement at {t.value!r}")
    def parse_move(self) -> Let:
        self.expect_kw("MOVE")
        value = self.parse_expr()
        self.expect_kw("TO")
        name = self.expect_name()
        self.end_simple()
        return Let(name, value)
    def parse_compute(self) -> Let:
        self.expect_kw("COMPUTE")
        name = self.expect_name()
        self.expect("op", "=")
        value = self.parse_expr()
        self.end_simple()
        return Let(name, value)
    def parse_display(self) -> Print:
        self.expect_kw("DISPLAY")
        value = self.parse_expr()
        self.end_simple()
        return Print(value)
    def parse_if(self) -> If:
        self.expect_kw("IF")
        first_cond = self.parse_expr()
        self.end_header()
        arms = [(first_cond, self.parse_block({"ELSE", "END-IF"}))]
        els = None
        while self.at_kw("ELSE") and self.peek(1).kind == "kw" and self.peek(1).value == "IF":
            self.next()
            self.next()
            cond = self.parse_expr()
            self.end_header()
            arms.append((cond, self.parse_block({"ELSE", "END-IF"})))
        if self.at_kw("ELSE"):
            self.next()
            self.end_header()
            els = self.parse_block({"END-IF"})
        self.expect_kw("END-IF")
        self.end_simple()
        return If(arms, els)
    def parse_evaluate(self) -> Switch:
        self.expect_kw("EVALUATE")
        expr = self.parse_expr()
        self.end_header()
        cases = []
        default = None
        while True:
            self.skip_nl()
            if self.at_kw("END-EVALUATE"):
                break
            if self.at_kw("WHEN"):
                self.next()
                if self.at_kw("OTHER"):
                    self.next()
                    self.end_header()
                    default = self.parse_block({"END-EVALUATE"})
                    continue
                val = self.parse_expr()
                self.end_header()
                body = self.parse_block({"WHEN", "OTHER", "END-EVALUATE"})
                cases.append((val, body))
                continue
            if self.at_kw("OTHER"):
                self.next()
                self.end_header()
                default = self.parse_block({"END-EVALUATE"})
                continue
            t = self.peek()
            raise SyntaxError(f"line {t.line}: expected WHEN / OTHER / END-EVALUATE, got {t.value!r}")
        self.expect_kw("END-EVALUATE")
        self.end_simple()
        return Switch(expr, cases, default)
    def parse_perform(self):
        self.expect_kw("PERFORM")
        if self.at_kw("VARYING"): return self.parse_perform_varying()
        name = self.expect_name()
        self.end_simple()
        return Gosub(name)
    def parse_perform_varying(self) -> ForTo:
        self.expect_kw("VARYING")
        var = self.expect_name()
        self.expect_kw("FROM")
        start = self.parse_expr()
        step = Num(1)
        if self.at_kw("BY"):
            self.next()
            step = self.parse_expr()
        self.expect_kw("UNTIL")
        cond = self.parse_expr()
        self.end_header()
        body = self.parse_block({"END-PERFORM"})
        self.expect_kw("END-PERFORM")
        self.end_simple()
        end = self._for_end_from_until(var, cond)
        return ForTo(var, start, end, step, body)
    def parse_add(self) -> Let:
        self.expect_kw("ADD")
        value = self.parse_expr()
        self.expect_kw("TO")
        name = self.expect_name()
        self.end_simple()
        return Let(name, Bin("+", Var(name), value))
    def parse_subtract(self) -> Let:
        self.expect_kw("SUBTRACT")
        value = self.parse_expr()
        self.expect_kw("FROM")
        name = self.expect_name()
        self.end_simple()
        return Let(name, Bin("-", Var(name), value))
    def parse_multiply(self) -> Let:
        self.expect_kw("MULTIPLY")
        lhs = self.parse_expr()
        self.expect_kw("BY")
        rhs = self.parse_expr()
        if self.at_kw("GIVING"):
            self.next()
            name = self.expect_name()
            self.end_simple()
            return Let(name, Bin("*", lhs, rhs))
        if not isinstance(rhs, Var):
            raise SyntaxError("MULTIPLY without GIVING requires a variable target")
        self.end_simple()
        return Let(rhs.name, Bin("*", rhs, lhs))
    def parse_divide(self) -> Let:
        self.expect_kw("DIVIDE")
        lhs = self.parse_expr()
        self.expect_kw("BY")
        rhs = self.parse_expr()
        if self.at_kw("GIVING"):
            self.next()
            name = self.expect_name()
            self.end_simple()
            return Let(name, Bin("/", lhs, rhs))
        if not isinstance(rhs, Var):
            raise SyntaxError("DIVIDE without GIVING requires a variable target")
        self.end_simple()
        return Let(rhs.name, Bin("/", rhs, lhs))
    def parse_stop_run(self) -> Return:
        self.expect_kw("STOP")
        if self.at_kw("RUN"): self.next()
        self.end_simple()
        return Return()
    def parse_call_from_id(self) -> Call:
        ns = self.expect_name()
        self.expect("op", ".")
        meth = self.next()
        if meth.kind not in ("id", "kw"):
            raise SyntaxError(f"line {meth.line}: expected method name, got {meth.value!r}")
        args = self.parse_args()
        return Call(ns, meth.value, args)
    def parse_args(self) -> list:
        self.expect("op", "(")
        args = []
        if not self.at("op", ")"):
            args.append(self.parse_expr())
            while self.at("op", ","):
                self.next()
                args.append(self.parse_expr())
        self.expect("op", ")")
        return args
    def parse_expr(self, min_prec: int = 0) -> object:
        left = self.parse_unary()
        while True:
            match = self._match_binop()
            if match is None or match[0] < min_prec:
                break
            prec, ntoks, kind, code = match
            for _ in range(ntoks):
                self.next()
            right = self.parse_expr(prec + 1)
            left = Cmp(code, left, right) if kind == "cmp" else Bin(code, left, right)
        return left
    def parse_unary(self) -> object:
        t = self.peek()
        if t.kind == "op" and t.value == "-": self.next(); return Bin("-", Num(0), self.parse_unary())
        if t.kind == "kw" and t.value == "NOT": self.next(); return Cmp("EQ", self.parse_unary(), Num(0))
        return self.parse_atom()
    def parse_atom(self) -> object:
        t = self.next()
        if t.kind == "num":
            return Num(int(t.value, 0))
        if t.kind == "str":
            return Str(t.value)
        if t.kind == "op" and t.value == "(":
            e = self.parse_expr()
            self.expect("op", ")")
            return e
        if t.kind == "id":
            if self.at("op", ".") and self.peek(1).kind in ("id", "kw") and self.peek(2).kind == "op" and self.peek(2).value == "(":
                self.next()
                meth = self.next().value
                args = self.parse_args()
                return Call(t.value, meth, args)
            if self.at("op", "("):
                args = self.parse_args()
                return Call(None, t.value, args)
            return Var(t.value)
        raise SyntaxError(f"line {t.line}: unexpected token {t.value!r}")
    def _match_binop(self):
        t = self.peek()
        if t.kind == "op" and t.value in _PREC:
            if t.value in _CMP:
                return (_PREC[t.value], 1, "cmp", _CMP[t.value])
            return (_PREC[t.value], 1, "bin", _BIN[t.value])
        w = self.word_at(0)
        w1 = self.word_at(1)
        w2 = self.word_at(2)
        w3 = self.word_at(3)
        w4 = self.word_at(4)
        if w == "AND":
            return (2, 1, "bin", "AND")
        if w == "OR":
            return (1, 1, "bin", "OR")
        if w == "GREATER" and w1 == "THAN":
            if w2 == "OR" and w3 == "EQUAL" and w4 == "TO":
                return (3, 5, "cmp", "GE")
            return (3, 2, "cmp", "GT")
        if w == "LESS" and w1 == "THAN":
            if w2 == "OR" and w3 == "EQUAL" and w4 == "TO":
                return (3, 5, "cmp", "LE")
            return (3, 2, "cmp", "LT")
        if w == "EQUAL" and w1 == "TO":
            return (3, 2, "cmp", "EQ")
        if w == "NOT":
            if self.peek(1).kind == "op" and self.peek(1).value == "=":
                return (3, 2, "cmp", "NE")
            if w1 == "EQUAL" and w2 == "TO":
                return (3, 3, "cmp", "NE")
        if w == "IS":
            if w1 == "GREATER" and w2 == "THAN":
                if w3 == "OR" and w4 == "EQUAL":
                    if self.word_at(5) == "TO":
                        return (3, 6, "cmp", "GE")
                return (3, 3, "cmp", "GT")
            if w1 == "LESS" and w2 == "THAN":
                if w3 == "OR" and w4 == "EQUAL":
                    if self.word_at(5) == "TO":
                        return (3, 6, "cmp", "LE")
                return (3, 3, "cmp", "LT")
            if w1 == "EQUAL" and w2 == "TO":
                return (3, 3, "cmp", "EQ")
            if w1 == "NOT":
                if w2 == "EQUAL" and w3 == "TO":
                    return (3, 4, "cmp", "NE")
                return (3, 2, "cmp", "NE")
        return None
    @staticmethod
    def _minus_one(node):
        if isinstance(node, Num): return Num(node.value - 1)
        return Bin("-", node, Num(1))
    def _for_end_from_until(self, var: str, cond: object) -> object:
        if not isinstance(cond, Cmp): raise SyntaxError("PERFORM VARYING UNTIL must be a simple comparison")
        def is_var(node, name):
            return isinstance(node, Var) and node.name == name
        if is_var(cond.lhs, var):
            if cond.cond == "GT":
                return cond.rhs
            if cond.cond == "GE":
                return self._minus_one(cond.rhs)
        if is_var(cond.rhs, var):
            if cond.cond == "LT":
                return cond.lhs
            if cond.cond == "LE":
                return self._minus_one(cond.lhs)
        raise SyntaxError("PERFORM VARYING currently requires UNTIL <var> > limit (or equivalent)")
def compile_cobol(source: str):
    """COBOL-style source -> PicoIL instruction list (reuses BASIC Lowerer)."""
    toks = tokenize(source)
    prog = Parser(toks).parse_program()
    return Lowerer().lower_program(prog)
def _decode_output(vm) -> List[int]:
    def s32(v: int) -> int:
        return v - 0x100000000 if v & 0x80000000 else v
    return [s32(int.from_bytes(chunk, "big")) for chunk in vm.output]
if __name__ == "__main__":
    from picoscript_il import lower_to_bytecode_safe
    from picoscript_vm import PicoVM
    SRC = """
IDENTIFICATION DIVISION.
PROGRAM-ID. HELLO-WORLD.
DATA DIVISION.
01 X PIC 9(4) VALUE 10.
01 Y PIC 9(4) VALUE 0.
PROCEDURE DIVISION.
    MOVE 10 TO X
    COMPUTE Y = X + 32
    IF Y > 40
        DISPLAY Y
    ELSE
        DISPLAY 0
    END-IF.
    PERFORM ADD-NUMBERS.
    STOP RUN.
ADD-NUMBERS.
    COMPUTE X = X + Y.
    DISPLAY X.
"""
    words = lower_to_bytecode_safe(compile_cobol(SRC))
    vm = PicoVM().run(words)
    output = _decode_output(vm)
    print("output:", output)
    assert output == [42, 52], output
    print("self-test ok")
