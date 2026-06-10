#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_english.py -- a controlled natural-English frontend for PicoScript.

The "piece de resistance" surface: programs read like plain imperative English,
yet compile straight through the same PicoIL -> bytecode/C/JS pipeline as every
other frontend (so it ultimately compiles to machine code via the C backend).
Like the Python-style frontend it reuses the BASIC AST nodes + `Lowerer`
unchanged; only the tokenizer/parser differ.

Compound statements use a colon + indentation block (same INDENT/DEDENT model as
the Python frontend); simple statements end at the line (an optional trailing
'.' is allowed). Grammar (case-insensitive):

  Set X to <expr>.            Let X be <expr>.        -> assignment (first use declares)
  Add <expr> to X.            Subtract <expr> from X.
  Increase X by <expr>.       Decrease X by <expr>.
  Multiply X by <expr>.       Divide X by <expr>.
  Print <expr>.               Show <expr>.   Display <expr>.    -> emit a value
  If <cond>:                  Otherwise if <cond>:    Otherwise:   -> conditional block
  While <cond>:               Repeat while <cond>:    As long as <cond>:
  Repeat <n> times with X:    -> X counts 0..n-1
  For each X from <a> to <b>: -> X counts a..b inclusive
  Define <name>:              To <name>:              -> subroutine (globals; no params)
  Do <name>.                  Call <name>.            -> invoke a subroutine
  Return.   Stop.  (break)    Skip.  (continue)
  Ns.Method(a, b).            -> host hook call statement

Expressions: numbers, variables, ( ... ), host calls Ns.Method(...), and binary
operators in words or symbols:
  plus +   minus -   times *   divided by /   modulo / mod %
Comparisons (in <cond>):
  is greater than            is less than
  is at least (>=)           is at most (<=)
  is greater than or equal to / is less than or equal to
  is / equals / is equal to (==)     is not / is not equal to (!=)
  exceeds (>)
combined with  and / or / not.
"""
from typing import List, Optional

from picoscript_basic import (  # reuse AST + lowering unchanged
    Num, Str, Var, Bin, Cmp, Call, Let, Ternary, If, While, ForTo, ForEach,
    Sub, Gosub, Return, Break, Skip, Print, CallStmt, Lowerer,
)

_TWO = {"==", "!=", "<=", ">=", "<>"}
_ONE = set("+-*/%()<>=,.:")


# ── tokenizer (significant indentation; words stay as 'word') ─────────────────

class Tok:
    __slots__ = ("kind", "value", "line")

    def __init__(self, kind, value, line):
        self.kind = kind      # num,word,str,op,newline,indent,dedent,eof
        self.value = value
        self.line = line

    def __repr__(self):
        return f"Tok({self.kind},{self.value!r})"


def _tok_line(text: str, lineno: int, out: List[Tok]):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t":
            i += 1
            continue
        if c == "#":
            break
        if c.isdigit():
            j = i
            if c == "0" and i + 1 < n and text[i + 1] in "xX":
                j = i + 2
                while j < n and text[j] in "0123456789abcdefABCDEF":
                    j += 1
            else:
                while j < n and text[j].isdigit():
                    j += 1
            out.append(Tok("num", text[i:j], lineno))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            out.append(Tok("word", text[i:j], lineno))
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
            out.append(Tok("str", "".join(buf), lineno))
            i = j + 1
            continue
        two = text[i:i + 2]
        if two in _TWO:
            out.append(Tok("op", two, lineno))
            i += 2
            continue
        if c in _ONE:
            out.append(Tok("op", c, lineno))
            i += 1
            continue
        raise SyntaxError(f"line {lineno}: unexpected char {c!r}")


def tokenize(src: str) -> List[Tok]:
    out: List[Tok] = []
    indents = [0]
    raw = src.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for idx, line in enumerate(raw):
        lineno = idx + 1
        stripped = line.lstrip(" \t")
        if stripped == "" or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent > indents[-1]:
            indents.append(indent)
            out.append(Tok("indent", "", lineno))
        else:
            while indent < indents[-1]:
                indents.pop()
                out.append(Tok("dedent", "", lineno))
            if indent != indents[-1]:
                raise SyntaxError(f"line {lineno}: inconsistent indentation")
        before = len(out)
        _tok_line(line, lineno, out)
        if len(out) > before:
            out.append(Tok("newline", "", lineno))
    while len(indents) > 1:
        indents.pop()
        out.append(Tok("dedent", "", len(raw)))
    out.append(Tok("eof", "", len(raw)))
    return out


# ── parser ───────────────────────────────────────────────────────────────────

_PREC_SYM = {"+": 5, "-": 5, "*": 6, "/": 6, "%": 6, "<": 3, ">": 3,
             "<=": 3, ">=": 3, "==": 3, "!=": 3, "<>": 3}
_CMP_SYM = {"<": "LT", ">": "GT", "<=": "LE", ">=": "GE", "==": "EQ",
            "!=": "NE", "<>": "NE"}


class Parser:
    def __init__(self, toks: List[Tok]):
        self.toks = toks
        self.i = 0

    def peek(self, k=0) -> Tok:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else self.toks[-1]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def at(self, kind, value=None) -> bool:
        t = self.peek()
        return t.kind == kind and (value is None or t.value == value)

    def at_word(self, *words) -> bool:
        t = self.peek()
        return t.kind == "word" and t.value.lower() in words

    def word_at(self, k) -> Optional[str]:
        t = self.peek(k)
        return t.value.lower() if t.kind == "word" else None

    def expect(self, kind, value=None) -> Tok:
        t = self.next()
        if t.kind != kind or (value is not None and t.value != value):
            want = value if value is not None else kind
            raise SyntaxError(f"line {t.line}: expected {want!r}, got {t.value!r} ({t.kind})")
        return t

    def eat_word(self, *words):
        t = self.next()
        if not (t.kind == "word" and t.value.lower() in words):
            raise SyntaxError(f"line {t.line}: expected one of {words}, got {t.value!r}")
        return t.value

    def end_stmt(self):
        if self.at("op", "."):
            self.next()
        self.expect("newline")

    # -- program / suites -----------------------------------------------------
    def parse_program(self) -> List[object]:
        stmts = []
        while self.peek().kind != "eof":
            s = self.parse_stmt()
            if s is not None:
                stmts.append(s)
        return stmts

    def parse_suite(self) -> List[object]:
        self.expect("op", ":")
        self.expect("newline")
        self.expect("indent")
        stmts = []
        while not self.at("dedent"):
            if self.peek().kind == "eof":
                raise SyntaxError("unexpected EOF inside block")
            s = self.parse_stmt()
            if s is not None:
                stmts.append(s)
        self.expect("dedent")
        return stmts

    # -- statements -----------------------------------------------------------
    def parse_stmt(self) -> Optional[object]:
        t = self.peek()
        if t.kind == "word":
            w = t.value.lower()
            if w in ("set", "let"):
                return self.parse_assign(w)
            if w == "add":
                self.next(); e = self.parse_expr(); self.eat_word("to")
                name = self.expect("word").value; self.end_stmt()
                return Let(name, Bin("+", Var(name), e))
            if w == "subtract":
                self.next(); e = self.parse_expr(); self.eat_word("from")
                name = self.expect("word").value; self.end_stmt()
                return Let(name, Bin("-", Var(name), e))
            if w in ("increase", "decrease", "multiply", "divide"):
                self.next()
                name = self.expect("word").value
                self.eat_word("by")
                e = self.parse_expr(); self.end_stmt()
                op = {"increase": "+", "decrease": "-", "multiply": "*", "divide": "/"}[w]
                return Let(name, Bin(op, Var(name), e))
            if w in ("print", "show", "display"):
                self.next(); e = self.parse_expr(); self.end_stmt()
                return Print(e)
            if w == "if":
                return self.parse_if()
            if w == "while":
                self.next(); cond = self.parse_cond()
                return While(cond, self.parse_suite())
            if w == "as":                       # "As long as <cond>:"
                self.next(); self.eat_word("long"); self.eat_word("as")
                cond = self.parse_cond()
                return While(cond, self.parse_suite())
            if w == "repeat":
                return self.parse_repeat()
            if w == "for":                      # "For each X from a to b:"
                return self.parse_for()
            if w in ("define", "to"):
                self.next()
                if self.at_word("a", "an", "the"):
                    self.next()
                if self.at_word("routine", "subroutine", "procedure", "function"):
                    self.next()
                    if self.at_word("called", "named"):
                        self.next()
                name = self.expect("word").value
                return Sub(name, self.parse_suite())
            if w in ("do", "call"):
                self.next(); name = self.expect("word").value; self.end_stmt()
                return Gosub(name)
            if w == "return":
                self.next(); self.end_stmt(); return Return()
            if w in ("stop", "break"):
                self.next()
                if self.at_word("out"):
                    self.next()
                self.end_stmt(); return Break()
            if w in ("skip", "continue"):
                self.next(); self.end_stmt(); return Skip()
            # bare host-call statement:  Ns.Method(...).
            if (self.peek(1).kind == "op" and self.peek(1).value == "."
                    and self.peek(2).kind == "word"
                    and self.peek(3).kind == "op" and self.peek(3).value == "("):
                call = self.parse_call_from_word()
                self.end_stmt()
                return CallStmt(call)
        raise SyntaxError(f"line {t.line}: cannot parse statement at {t.value!r}")

    def parse_assign(self, kw) -> Let:
        self.next()                              # 'set' | 'let'
        name = self.expect("word").value
        self.eat_word("to" if kw == "set" else "be")
        v = self.parse_expr()
        self.end_stmt()
        return Let(name, v)

    def parse_if(self) -> If:
        self.eat_word("if")
        cond = self.parse_cond()
        body = self.parse_suite()
        arms = [(cond, body)]
        els = None
        while self.at_word("otherwise") and self.peek(1).kind == "word" and self.peek(1).value.lower() == "if":
            self.eat_word("otherwise"); self.eat_word("if")
            arms.append((self.parse_cond(), self.parse_suite()))
        if self.at_word("otherwise"):
            self.eat_word("otherwise")
            els = self.parse_suite()
        return If(arms, els)

    def parse_repeat(self):
        self.eat_word("repeat")
        if self.at_word("while"):                # "Repeat while <cond>:"
            self.eat_word("while")
            cond = self.parse_cond()
            return While(cond, self.parse_suite())
        count = self.parse_expr()                # "Repeat <n> times [with X]:"
        self.eat_word("times")
        var = "_i"
        if self.at_word("with"):
            self.eat_word("with")
            var = self.expect("word").value
        return ForEach(var, count, self.parse_suite())

    def parse_for(self) -> ForTo:
        self.eat_word("for")
        self.eat_word("each")
        var = self.expect("word").value
        self.eat_word("from")
        start = self.parse_expr()
        self.eat_word("to")
        end = self.parse_expr()
        step = None
        if self.at_word("by", "step"):
            self.next()
            step = self.parse_expr()
        return ForTo(var, start, end, step, self.parse_suite())

    def parse_call_from_word(self) -> Call:
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

    # -- conditions & expressions --------------------------------------------
    def parse_cond(self) -> object:
        return self.parse_expr()

    def _match_binop(self):
        """At the current position, return (prec, ntokens, kind, code) for a
        binary operator written in words or symbols, or None. kind is 'cmp',
        'bin'; code is the Cmp condition or Bin op."""
        t = self.peek()
        if t.kind == "op" and t.value in _PREC_SYM:
            if t.value in _CMP_SYM:
                return (_PREC_SYM[t.value], 1, "cmp", _CMP_SYM[t.value])
            op = "MOD" if t.value == "%" else t.value
            return (_PREC_SYM[t.value], 1, "bin", op)
        if t.kind != "word":
            return None
        w = t.value.lower()
        w1 = self.word_at(1)
        w2 = self.word_at(2)
        w3 = self.word_at(3)
        w4 = self.word_at(4)
        w5 = self.word_at(5)
        # logical
        if w == "and":
            return (2, 1, "bin", "AND")
        if w == "or":
            return (1, 1, "bin", "OR")
        # arithmetic words
        if w == "plus":
            return (5, 1, "bin", "+")
        if w == "minus":
            return (5, 1, "bin", "-")
        if w == "times":
            return (6, 1, "bin", "*")
        if w in ("modulo", "mod"):
            return (6, 1, "bin", "MOD")
        if w == "divided" and w1 == "by":
            return (6, 2, "bin", "/")
        if w == "over":
            return (6, 1, "bin", "/")
        # comparison phrases
        if w == "is":
            if w1 == "greater" and w2 == "than":
                if w3 == "or" and w4 == "equal" and w5 == "to":
                    return (3, 6, "cmp", "GE")
                return (3, 3, "cmp", "GT")
            if w1 == "less" and w2 == "than":
                if w3 == "or" and w4 == "equal" and w5 == "to":
                    return (3, 6, "cmp", "LE")
                return (3, 3, "cmp", "LT")
            if w1 == "at" and w2 == "least":
                return (3, 3, "cmp", "GE")
            if w1 == "at" and w2 == "most":
                return (3, 3, "cmp", "LE")
            if w1 == "not":
                if w2 == "equal" and w3 == "to":
                    return (3, 4, "cmp", "NE")
                return (3, 2, "cmp", "NE")
            if w1 == "equal" and w2 == "to":
                return (3, 3, "cmp", "EQ")
            return (3, 1, "cmp", "EQ")            # "X is Y"
        if w == "equals":
            return (3, 1, "cmp", "EQ")
        if w == "exceeds":
            return (3, 1, "cmp", "GT")
        return None

    def parse_expr(self, min_prec: int = 0) -> object:
        left = self.parse_unary()
        while True:
            m = self._match_binop()
            if m is None or m[0] < min_prec:
                break
            prec, ntoks, kind, code = m
            for _ in range(ntoks):
                self.next()
            right = self.parse_expr(prec + 1)
            left = Cmp(code, left, right) if kind == "cmp" else Bin(code, left, right)
        return left

    def parse_unary(self) -> object:
        t = self.peek()
        if t.kind == "op" and t.value == "-":
            self.next()
            return Bin("-", Num(0), self.parse_unary())
        if t.kind == "word" and t.value.lower() == "not":
            self.next()
            return Cmp("EQ", self.parse_unary(), Num(0))
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
        if t.kind == "word":
            lw = t.value.lower()
            if lw == "true":
                return Num(1)
            if lw == "false":
                return Num(0)
            if (self.at("op", ".") and self.peek(1).kind == "word"
                    and self.peek(2).kind == "op" and self.peek(2).value == "("):
                self.next()                       # host call Ns.Method(...)
                method = self.next().value
                args = self.parse_args()
                return Call(t.value, method, args)
            return Var(t.value)
        raise SyntaxError(f"line {t.line}: unexpected token {t.value!r}")


# ── public API ────────────────────────────────────────────────────────────────

def compile_english(source: str):
    """Controlled-English source -> PicoIL instruction list (reuses BASIC Lowerer)."""
    toks = tokenize(source)
    prog = Parser(toks).parse_program()
    return Lowerer().lower_program(prog)
