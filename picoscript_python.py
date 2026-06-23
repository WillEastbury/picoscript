#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_python.py -- a Python-style (whitespace + colon block) frontend.

A third surface syntax for PicoScript. It deliberately reuses the BASIC frontend's
AST node classes and `Lowerer` verbatim: only the tokenizer (significant
indentation -> INDENT/DEDENT) and the parser differ. Because the emitted AST is
identical to the equivalent BASIC/C program, the lowered PicoIL -- and therefore
the bytecode on every backend (Python VM, C VM, JS VM) -- is byte-for-byte the
same. "One IL, many surfaces."

Supported:
  x = 10                       assignment (first assignment declares; one global scope)
  x += 5   x -= 1   x *= 2     augmented assignment (+ - * / %)
  if c: / elif c: / else:      indentation blocks
  while c:                     pre-test loop
  for i in range(n):           0..n-1
  for i in range(a, b):        a..b-1   (for i in range(a, b, s): stepped)
  def name():                  parameterless subroutine (variables are global)
  name()                       call a subroutine
  return / break / continue / pass
  print(expr)                  emit a value
  Ns.Method(a, b)              host hook call (Memory.*, Span.*, Storage.*, Net.*, ...)
  operators  + - * / %  ==  != < > <= >=  and or not  ( ? : via  a if c else b )
  # line comments
"""
from typing import List, Optional

from picoscript_basic import (  # reuse AST + lowering unchanged
    Num, Str, Var, Bin, Cmp, Call, Let, Ternary, If, While, DoLoop, ForTo, ForEach,
    Switch, Goto, Label, Sub, Gosub, Return, Break, Skip, Print, CallStmt, Lowerer,
    Dispatch, TryExcept, Raise, OnBlock, ConstDecl, EnumDecl,
)

KEYWORDS = {
    "if", "elif", "else", "while", "for", "in", "range", "def", "return",
    "break", "continue", "pass", "and", "or", "not", "print", "true", "false",
    "match", "case", "do", "until", "goto", "label", "dispatch", "const", "enum",
    "try", "except", "finally", "raise", "on",
}

# comparator symbols -> Cmp condition codes (matches picoscript_basic CMP codes)
_CMP = {"==": "EQ", "!=": "NE", "<": "LT", ">": "GT", "<=": "LE", ">=": "GE"}
# augmented assignment -> Bin op
_AUG = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "MOD"}
# binary operator precedence (higher binds tighter); comparators sit at 3
_PREC = {"or": 1, "and": 2,
         "==": 3, "!=": 3, "<": 3, ">": 3, "<=": 3, ">=": 3,
         "+": 5, "-": 5, "*": 6, "/": 6, "%": 6}
# map an operator token to a Bin op name the Lowerer understands
_BINOP = {"+": "+", "-": "-", "*": "*", "/": "/", "%": "MOD", "and": "AND", "or": "OR"}

_TWO = {"==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%="}
_ONE = set("+-*/%()<>=,.:")


# ── tokenizer (significant indentation) ──────────────────────────────────────

class Tok:
    __slots__ = ("kind", "value", "line", "pos")

    def __init__(self, kind, value, line, pos=-1):
        self.kind = kind      # num,id,kw,str,op,newline,indent,dedent,eof
        self.value = value
        self.line = line
        self.pos = pos        # INV-25: source byte offset of the token start

    def __repr__(self):
        return f"Tok({self.kind},{self.value!r})"


def _tokenize_line(text: str, lineno: int, out: List[Tok], line_start: int = 0):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        start = line_start + i        # INV-25: absolute source offset of this token
        if c in " \t":
            i += 1
            continue
        if c == "#":
            break  # rest of line is a comment
        if c.isdigit() or (c == "0" and i + 1 < n and text[i + 1] in "xX"):
            j = i
            if c == "0" and i + 1 < n and text[i + 1] in "xX":
                j = i + 2
                while j < n and text[j] in "0123456789abcdefABCDEF":
                    j += 1
            else:
                while j < n and text[j].isdigit():
                    j += 1
            out.append(Tok("num", text[i:j], lineno, start))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            out.append(Tok("kw" if word.lower() in KEYWORDS else "id", word, lineno, start))
            i = j
            continue
        if c == '"' or c == "'":
            quote = c
            j = i + 1
            buf = []
            while j < n and text[j] != quote:
                if text[j] == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'"}.get(nxt, nxt))
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            if j >= n:
                raise SyntaxError(f"line {lineno}: unterminated string")
            out.append(Tok("str", "".join(buf), lineno, start))
            i = j + 1
            continue
        two = text[i:i + 2]
        if two in _TWO:
            out.append(Tok("op", two, lineno, start))
            i += 2
            continue
        if c in _ONE:
            out.append(Tok("op", c, lineno, start))
            i += 1
            continue
        raise SyntaxError(f"line {lineno}: unexpected char {c!r}")


def tokenize(src: str) -> List[Tok]:
    out: List[Tok] = []
    indents = [0]
    raw_lines = src.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    offset = 0
    for idx, line in enumerate(raw_lines):
        lineno = idx + 1
        line_start = offset
        offset += len(line) + 1            # +1 for the '\n' that split() removed
        stripped = line.lstrip(" \t")
        if stripped == "" or stripped.startswith("#"):
            continue  # blank / comment-only line: no indentation effect
        indent = len(line) - len(stripped)
        if indent > indents[-1]:
            indents.append(indent)
            out.append(Tok("indent", "", lineno, line_start))
        else:
            while indent < indents[-1]:
                indents.pop()
                out.append(Tok("dedent", "", lineno, line_start))
            if indent != indents[-1]:
                raise SyntaxError(f"line {lineno}: inconsistent indentation")
        before = len(out)
        _tokenize_line(line, lineno, out, line_start)
        if len(out) > before:
            out.append(Tok("newline", "", lineno, line_start))
    while len(indents) > 1:
        indents.pop()
        out.append(Tok("dedent", "", len(raw_lines), offset))
    out.append(Tok("eof", "", len(raw_lines), offset))
    return out


# ── parser ───────────────────────────────────────────────────────────────────

class Parser:
    def __init__(self, toks: List[Tok]):
        self.toks = toks
        self.i = 0

    def peek(self) -> Tok:
        return self.toks[self.i]

    def peek2(self) -> Tok:
        return self.toks[self.i + 1]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def at(self, kind, value=None) -> bool:
        t = self.peek()
        return t.kind == kind and (value is None or t.value == value)

    def at_kw(self, *names) -> bool:
        t = self.peek()
        return t.kind == "kw" and t.value.lower() in names

    def expect(self, kind, value=None) -> Tok:
        t = self.next()
        if t.kind != kind or (value is not None and t.value != value):
            want = value if value is not None else kind
            raise SyntaxError(f"line {t.line}: expected {want!r}, got {t.value!r} ({t.kind})")
        return t

    def expect_kw(self, name: str):
        t = self.next()
        if not (t.kind == "kw" and t.value.lower() == name):
            raise SyntaxError(f"line {t.line}: expected {name}, got {t.value!r}")

    # -- program / suites -----------------------------------------------------
    def parse_program(self) -> List[object]:
        stmts = []
        while self.peek().kind != "eof":
            s = self.parse_stmt()
            if s is not None:
                if isinstance(s, list):
                    stmts.extend(s)
                else:
                    stmts.append(s)
        return stmts

    def parse_suite(self) -> List[object]:
        """':' NEWLINE INDENT stmt+ DEDENT"""
        self.expect("op", ":")
        self.expect("newline")
        self.expect("indent")
        stmts = []
        while not self.at("dedent"):
            if self.peek().kind == "eof":
                raise SyntaxError("unexpected EOF inside block")
            s = self.parse_stmt()
            if s is not None:
                if isinstance(s, list):
                    stmts.extend(s)
                else:
                    stmts.append(s)
        self.expect("dedent")
        return stmts

    # -- statements -----------------------------------------------------------
    def parse_stmt(self) -> Optional[object]:
        # INV-25: stamp each statement with its first token's source offset.
        start = self.peek().pos
        node = self._parse_stmt()
        if node is not None:
            try:
                node.pos = start
            except (AttributeError, TypeError):
                pass
        return node

    def _parse_stmt(self) -> Optional[object]:
        t = self.peek()
        if t.kind == "kw":
            kw = t.value.lower()
            if kw == "if":
                return self.parse_if()
            if kw == "while":
                return self.parse_while()
            if kw == "for":
                return self.parse_for()
            if kw == "match":
                return self.parse_match()
            if kw == "dispatch":
                return self.parse_dispatch()
            if kw == "do":
                return self.parse_do()
            if kw == "goto":
                self.next(); name = self.expect("id").value; self.expect("newline"); return Goto(name)
            if kw == "label":
                self.next(); name = self.expect("id").value; self.expect("newline"); return Label(name)
            if kw == "def":
                return self.parse_def()
            if kw == "try":
                return self.parse_try()
            if kw == "on":
                return self.parse_on()
            if kw == "const":
                self.next()
                name = self.expect("id").value
                self.expect("op", "=")
                value = self.parse_expr()
                self.expect("newline")
                return ConstDecl(name, value)
            if kw == "enum":
                self.next()
                enum_name = self.expect("id").value
                self.expect("op", ":")
                self.expect("newline")
                self.expect("indent")
                members = []
                while not self.at("dedent"):
                    m = self.expect("id").value
                    mv = None
                    if self.at("op", "="):
                        self.next()
                        mv = self.parse_expr()
                    self.expect("newline")
                    members.append((m, mv))
                self.expect("dedent")
                return EnumDecl(enum_name, members)
            if kw == "raise":
                self.next()
                if self.at("newline"):
                    self.next(); return Raise()
                v = self.parse_expr(); self.expect("newline"); return Raise(v)
            if kw == "return":
                self.next()
                if self.at("newline"):
                    self.next(); return Return()
                v = self.parse_expr(); self.expect("newline"); return Return(v)
            if kw == "break":
                self.next(); self.expect("newline"); return Break()
            if kw == "continue":
                self.next(); self.expect("newline"); return Skip()
            if kw == "pass":
                self.next(); self.expect("newline"); return None
            if kw == "print":
                self.next()
                self.expect("op", "(")
                v = self.parse_expr()
                self.expect("op", ")")
                self.expect("newline")
                return Print(v)
            raise SyntaxError(f"line {t.line}: unexpected keyword {t.value!r}")
        if t.kind == "id":
            nxt = self.peek2()
            # augmented / plain assignment
            if nxt.kind == "op" and nxt.value == "=":
                name = self.next().value
                self.next()  # '='
                v = self.parse_expr()
                self.expect("newline")
                return Let(name, v)
            if nxt.kind == "op" and nxt.value in _AUG:
                name = self.next().value
                op = _AUG[self.next().value]
                rhs = self.parse_expr()
                self.expect("newline")
                return Let(name, Bin(op, Var(name), rhs))
            # host call:  Ns.Method(...)
            if nxt.kind == "op" and nxt.value == ".":
                call = self.parse_call_from_id()
                self.expect("newline")
                return CallStmt(call)
            # bare call:  name(args...) -> subroutine call (Gosub with args)
            if nxt.kind == "op" and nxt.value == "(":
                name = self.next().value
                args = self.parse_args()
                self.expect("newline")
                return Gosub(name, args if args else None)
        raise SyntaxError(f"line {t.line}: cannot parse statement at {t.value!r}")

    def parse_if(self) -> If:
        self.expect_kw("if")
        cond = self.parse_expr()
        body = self.parse_suite()
        arms = [(cond, body)]
        els = None
        while self.at_kw("elif"):
            self.expect_kw("elif")
            c2 = self.parse_expr()
            b2 = self.parse_suite()
            arms.append((c2, b2))
        if self.at_kw("else"):
            self.expect_kw("else")
            els = self.parse_suite()
        return If(arms, els)

    def parse_while(self) -> While:
        self.expect_kw("while")
        cond = self.parse_expr()
        body = self.parse_suite()
        return While(cond, body)

    def parse_match(self) -> Switch:
        self.expect_kw("match")
        expr = self.parse_expr()
        self.expect("op", ":")
        self.expect("newline")
        self.expect("indent")
        cases = []
        default = None
        while not self.at("dedent"):
            self.expect_kw("case")
            if self.peek().kind == "id" and self.peek().value == "_":
                self.next()
                default = self.parse_suite()
            else:
                val = self.parse_expr()
                cases.append((val, self.parse_suite()))
        self.expect("dedent")
        return Switch(expr, cases, default)

    def parse_dispatch(self) -> Dispatch:
        """dispatch x: / case N: / case _:  -- a jump-table switch over dense
        non-negative integer cases (compiles to an indexed jump)."""
        self.expect_kw("dispatch")
        expr = self.parse_expr()
        self.expect("op", ":")
        self.expect("newline")
        self.expect("indent")
        cases = []
        default = None
        while not self.at("dedent"):
            self.expect_kw("case")
            if self.peek().kind == "id" and self.peek().value == "_":
                self.next()
                default = self.parse_suite()
            else:
                val = self.parse_expr()
                cases.append((val, self.parse_suite()))
        self.expect("dedent")
        return Dispatch(expr, cases, default)

    def parse_do(self) -> DoLoop:
        """Post-test loop: ``do:`` <block> then ``while cond`` / ``until cond``."""
        self.expect_kw("do")
        body = self.parse_suite()
        if self.at_kw("while"):
            self.expect_kw("while"); cond = self.parse_expr(); until = False
        elif self.at_kw("until"):
            self.expect_kw("until"); cond = self.parse_expr(); until = True
        else:
            raise SyntaxError(f"line {self.peek().line}: 'do:' block must be followed by 'while' or 'until'")
        self.expect("newline")
        return DoLoop(None, False, cond, until, body)

    def parse_for(self):
        self.expect_kw("for")
        var = self.expect("id").value
        self.expect_kw("in")
        # for x in range(...): counted loop
        if self.at("kw") and self.peek().value == "range":
            self.next()
            self.expect("op", "(")
            a = self.parse_expr()
            args = [a]
            while self.at("op", ","):
                self.next()
                args.append(self.parse_expr())
            self.expect("op", ")")
            body = self.parse_suite()
            if len(args) == 1:
                return ForEach(var, args[0], body)
            end = self._minus_one(args[1])
            step = args[2] if len(args) >= 3 else None
            return ForTo(var, args[0], end, step, body)
        # for x in collection: desugar to counted Span.Len + Span.Get loop
        coll = self.parse_expr()
        body = self.parse_suite()
        # Desugar: _coll = coll; for _i in range(Span.Len(_coll)): x = Span.Get(_coll, _i); body
        coll_var = "__coll__"
        idx_var = "__idx__"
        inner = [Let(var, Call("Span", "Get", [Var(coll_var), Var(idx_var)]))] + body
        return [
            Let(coll_var, coll),
            ForEach(idx_var, Call("Span", "Len", [Var(coll_var)]), inner),
        ]

    @staticmethod
    def _minus_one(node):
        if isinstance(node, Num):
            return Num(node.value - 1)
        return Bin("-", node, Num(1))

    def parse_def(self) -> Sub:
        self.expect_kw("def")
        name = self.expect("id").value
        self.expect("op", "(")
        params = []
        if not self.at("op", ")"):
            params.append(self.expect("id").value)
            while self.at("op", ","):
                self.next()
                params.append(self.expect("id").value)
        self.expect("op", ")")
        body = self.parse_suite()
        return Sub(name, body, params if params else None)

    def parse_try(self):
        self.expect_kw("try")
        try_body = self.parse_suite()
        except_body = []
        finally_body = None
        if self.at("kw") and self.peek().value == "except":
            self.next()
            except_body = self.parse_suite()
        if self.at("kw") and self.peek().value == "finally":
            self.next()
            finally_body = self.parse_suite()
        return TryExcept(try_body, except_body, finally_body)

    def parse_on(self):
        """ON Ns.Method: body (indented block)"""
        self.expect_kw("on")
        ns = self.expect("id").value
        self.expect("op", ".")
        method = self.expect("id").value
        body = self.parse_suite()
        return OnBlock(ns, method, body)

    def parse_call_from_id(self) -> Call:
        ns = self.next().value
        self.expect("op", ".")
        method = self.next().value
        args = self.parse_args()
        return Call(ns, method, args)

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

    # -- expressions ----------------------------------------------------------
    def parse_expr(self, min_prec: int = 0) -> object:
        left = self.parse_unary()
        # python conditional expression:  X if C else Y
        if min_prec == 0 and self.at_kw("if"):
            self.expect_kw("if")
            cond = self.parse_expr()
            self.expect_kw("else")
            els = self.parse_expr()
            return Ternary(cond, left, els)
        while True:
            t = self.peek()
            opval = None
            if t.kind == "op" and t.value in _PREC:
                opval = t.value
            elif t.kind == "kw" and t.value.lower() in _PREC:
                opval = t.value.lower()
            if opval is None or _PREC[opval] < min_prec:
                break
            self.next()
            right = self.parse_expr(_PREC[opval] + 1)
            if opval in _CMP:
                left = Cmp(_CMP[opval], left, right)
            else:
                left = Bin(_BINOP[opval], left, right)
        return left

    def parse_unary(self) -> object:
        t = self.peek()
        if t.kind == "op" and t.value == "-":
            self.next()
            return Bin("-", Num(0), self.parse_unary())
        if t.kind == "kw" and t.value.lower() == "not":
            self.next()
            return Cmp("EQ", self.parse_unary(), Num(0))
        return self.parse_atom()

    def parse_atom(self) -> object:
        t = self.next()
        if t.kind == "num":
            return Num(int(t.value, 0))
        if t.kind == "str":
            return Str(t.value)
        if t.kind == "kw" and t.value.lower() in ("true", "false"):
            return Num(1 if t.value.lower() == "true" else 0)
        if t.kind == "op" and t.value == "(":
            e = self.parse_expr()
            self.expect("op", ")")
            return e
        if t.kind == "id":
            if self.at("op", "."):
                self.next()
                method = self.next().value
                args = self.parse_args()
                return Call(t.value, method, args)
            if self.at("op", "("):
                args = self.parse_args()                 # bare-name call: len(x), hex(n), abs(n)
                return Call(None, t.value, args)
            return Var(t.value)
        raise SyntaxError(f"line {t.line}: unexpected token {t.value!r}")


# ── public API ────────────────────────────────────────────────────────────────

def compile_python(source: str):
    """Python-style source -> PicoIL instruction list (reuses the BASIC Lowerer)."""
    toks = tokenize(source)
    prog = Parser(toks).parse_program()
    return Lowerer().lower_program(prog)
