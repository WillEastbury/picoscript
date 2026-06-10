#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_basic.py -- BASIC-like frontend for PicoScript.

Completes the v2 vision (LANGUAGE_SPEC sec 11 L4): a case-insensitive,
block-structured surface that lowers to PicoIL and runs on PicoVM, sharing the
exact bytecode contract with the C-syntax frontend.

Surface (keywords case-insensitive; one statement per line; `name:` defines a
label):

    LET X = 5
    Y = X + 3 * 2                ' LET is optional
    IF Y GT 10 THEN              ' word comparators EQ NE LT GT LE GE (or < > <= ...)
        NET.STATUS(200)
    ELSEIF Y EQ 0 THEN
        NET.STATUS(204)
    ELSE
        NET.STATUS(404)
    ENDIF
    WHILE X GT 0
        X = X - 1
    ENDWHILE
    DO                           ' DO/LOOP: condition (WHILE or UNTIL) at
        X = X + 1                ' either end -- at DO = pre-test, at LOOP =
    LOOP UNTIL X GE 3            ' post-test (body always runs at least once)
    FOR I = 1 TO 5 STEP 1
        Y = Y + I
    NEXT
    FOREACH J IN 4               ' index loop J = 0..3
        IF J EQ 2 THEN
            SKIP                 ' continue: jump to next iteration
        ENDIF
        ACC = ACC + J
    ENDFOREACH
    SWITCH CODE
        CASE 1
            PRINT 100
        CASE 2
            PRINT 200
            BREAK                ' BREAK exits the nearest loop or SWITCH
        DEFAULT
            PRINT 0
    ENDSWITCH
    GOSUB WORKER
    GOTO DONE
    DONE:
    PRINT Y
    RETURN

    SUB WORKER
        Z = 1
    ENDSUB

PRINT lowers to SAVE+PIPE of a scratch card so output is observable in VM.output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple

from picoscript_il import ILBuilder, VReg, Imm, COND, COND_NEGATE, canon_host
from picoscript_lang import encode_card_addr

PRINT_CARD = 0xFFFE   # scratch card used to pipe PRINT output

KEYWORDS = {
    "LET", "DIM", "IF", "THEN", "ELSEIF", "ELSE", "ENDIF", "WHILE", "ENDWHILE",
    "FOR", "TO", "STEP", "NEXT", "FOREACH", "IN", "ENDFOREACH",
    "SWITCH", "CASE", "DEFAULT", "ENDSWITCH", "GOTO", "GOSUB", "SUB",
    "ENDSUB", "RETURN", "PRINT", "AND", "OR", "NOT",
    "DO", "LOOP", "UNTIL",
    "BREAK", "SKIP", "INC", "DEC", "IIF",
    "EQ", "NE", "LT", "GT", "LE", "GE", "MOD",
}
CMP_WORDS = {"EQ": "EQ", "NE": "NE", "LT": "LT", "GT": "GT", "LE": "LE", "GE": "GE"}
# Symbol comparators. `=` means equality inside an expression/test; assignment `=`
# at statement level is consumed by the statement parser before this is consulted.
CMP_SYMS = {"==": "EQ", "!=": "NE", "<>": "NE", "=": "EQ",
            "<": "LT", ">": "GT", "<=": "LE", ">=": "GE"}
COMPARATORS = {}
COMPARATORS.update(CMP_WORDS)
COMPARATORS.update(CMP_SYMS)
ASSIGN_OPS = {"+=": "+", "-=": "-", "*=": "*", "/=": "/"}

_TWO = {"==", "!=", "<=", ">=", "<>", "+=", "-=", "*=", "/="}
_ONE = set("+-*/()<>=,.:")


# ── tokenizer ───────────────────────────────────────────────────────────────

@dataclass
class Tok:
    kind: str          # num,id,kw,str,op,nl,eof
    value: str
    line: int


def tokenize(src: str) -> List[Tok]:
    toks: List[Tok] = []
    i, n, line = 0, len(src), 1
    while i < n:
        c = src[i]
        if c == "\n":
            toks.append(Tok("nl", "\\n", line)); line += 1; i += 1; continue
        if c in " \t\r":
            i += 1; continue
        if c == "'" or (c == "/" and i + 1 < n and src[i + 1] == "/"):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == '"':
            j = i + 1; buf = []
            while j < n and src[j] != '"':
                buf.append(src[j]); j += 1
            toks.append(Tok("str", "".join(buf), line)); i = j + 1; continue
        if c.isdigit():
            j = i
            if c == "0" and j + 1 < n and src[j + 1] in "xX":
                j += 2
                while j < n and src[j] in "0123456789abcdefABCDEF":
                    j += 1
            else:
                while j < n and src[j].isdigit():
                    j += 1
            toks.append(Tok("num", src[i:j], line)); i = j; continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            up = word.upper()
            toks.append(Tok("kw" if up in KEYWORDS else "id",
                            up if up in KEYWORDS else word, line))
            i = j; continue
        two = src[i:i + 2]
        if two in _TWO:
            toks.append(Tok("op", two, line)); i += 2; continue
        if c in _ONE:
            toks.append(Tok("op", c, line)); i += 1; continue
        raise SyntaxError(f"line {line}: unexpected char {c!r}")
    toks.append(Tok("nl", "\\n", line))
    toks.append(Tok("eof", "", line))
    return toks


# ── AST ─────────────────────────────────────────────────────────────────────

@dataclass
class Num: value: int
@dataclass
class Str: value: str
@dataclass
class Var: name: str
@dataclass
class Bin:
    op: str; lhs: object; rhs: object
@dataclass
class Cmp:
    cond: str; lhs: object; rhs: object
@dataclass
class Call:
    ns: Optional[str]; method: str; args: list
@dataclass
class Let:
    name: str; value: object
@dataclass
class Dim:
    name: str; init: object          # init may be None
@dataclass
class IncDec:
    name: str; delta: int            # +1 for INC, -1 for DEC
@dataclass
class Ternary:
    cond: object; then: object; els: object
@dataclass
class If:
    arms: list; els: Optional[list]          # arms = [(cond, body), ...]
@dataclass
class While:
    cond: object; body: list
@dataclass
class DoLoop:
    top_cond: object; top_until: bool; bottom_cond: object; bottom_until: bool; body: list
@dataclass
class ForTo:
    var: str; start: object; end: object; step: object; body: list
@dataclass
class ForEach:
    var: str; count: object; body: list
@dataclass
class Switch:
    expr: object; cases: list; default: Optional[list]   # cases=[(value,body),...]
@dataclass
class Goto:
    label: str
@dataclass
class Label:
    name: str
@dataclass
class Gosub:
    name: str
@dataclass
class Sub:
    name: str; body: list
@dataclass
class Return: pass
@dataclass
class Break: pass
@dataclass
class Skip: pass
@dataclass
class Print:
    value: object
@dataclass
class CallStmt:
    call: Call


# ── parser ──────────────────────────────────────────────────────────────────

_PREC = {"OR": 1, "AND": 2, "+": 5, "-": 5, "*": 6, "/": 6, "MOD": 6}
for _c in COMPARATORS:                 # comparators bind looser than arithmetic, tighter than AND/OR
    _PREC[_c] = 3


class Parser:
    def __init__(self, toks: List[Tok]):
        self.toks = toks
        self.i = 0

    def peek(self) -> Tok:
        return self.toks[self.i]

    def peek2(self) -> Tok:
        return self.toks[self.i + 1]

    def next(self) -> Tok:
        t = self.toks[self.i]; self.i += 1; return t

    def skip_nl(self):
        while self.peek().kind == "nl":
            self.i += 1

    def at_kw(self, *names) -> bool:
        t = self.peek()
        return t.kind == "kw" and t.value in names

    def eat_kw(self, name: str):
        t = self.next()
        if not (t.kind == "kw" and t.value == name):
            raise SyntaxError(f"line {t.line}: expected {name}, got {t.value!r}")

    def eat_op(self, val: str):
        t = self.next()
        if not (t.kind == "op" and t.value == val):
            raise SyntaxError(f"line {t.line}: expected {val!r}, got {t.value!r}")

    def end_line(self):
        t = self.peek()
        if t.kind in ("nl", "eof"):
            self.skip_nl(); return
        raise SyntaxError(f"line {t.line}: expected end of line, got {t.value!r}")

    # -- program ---------------------------------------------------------
    def parse_program(self) -> List[object]:
        stmts = []
        self.skip_nl()
        while self.peek().kind != "eof":
            stmts.append(self.parse_stmt())
            self.skip_nl()
        return stmts

    def parse_block(self, *terminators) -> List[object]:
        stmts = []
        self.skip_nl()
        while not self.at_kw(*terminators):
            if self.peek().kind == "eof":
                raise SyntaxError(f"unexpected EOF; expected one of {terminators}")
            stmts.append(self.parse_stmt())
            self.skip_nl()
        return stmts

    def parse_stmt(self) -> object:
        t = self.peek()
        # label:  (id followed by ':')
        if t.kind == "id" and self.peek2().kind == "op" and self.peek2().value == ":":
            name = self.next().value; self.next(); self.end_line()
            return Label(name)
        if t.kind == "kw":
            kw = t.value
            if kw == "LET":
                return self.parse_let(eat_let=True)
            if kw == "DIM":
                return self.parse_dim()
            if kw == "INC":
                self.next(); name = self.next().value; self.end_line(); return IncDec(name, 1)
            if kw == "DEC":
                self.next(); name = self.next().value; self.end_line(); return IncDec(name, -1)
            if kw == "IF":
                return self.parse_if()
            if kw == "WHILE":
                return self.parse_while()
            if kw == "DO":
                return self.parse_do()
            if kw == "FOR":
                return self.parse_for()
            if kw == "FOREACH":
                return self.parse_foreach()
            if kw == "SWITCH":
                return self.parse_switch()
            if kw == "GOTO":
                self.next(); name = self.next().value; self.end_line(); return Goto(name)
            if kw == "GOSUB":
                self.next(); name = self.next().value; self.end_line(); return Gosub(name)
            if kw == "SUB":
                return self.parse_sub()
            if kw == "RETURN":
                self.next(); self.end_line(); return Return()
            if kw == "BREAK":
                self.next(); self.end_line(); return Break()
            if kw == "SKIP":
                self.next(); self.end_line(); return Skip()
            if kw == "PRINT":
                self.next(); v = self.parse_expr(); self.end_line(); return Print(v)
            raise SyntaxError(f"line {t.line}: unexpected keyword {kw}")
        # assignment: id = expr / id += expr   OR bare call: Ns.Method(...)
        if t.kind == "id":
            nxt = self.peek2()
            if nxt.kind == "op" and nxt.value == "=":
                return self.parse_let(eat_let=False)
            if nxt.kind == "op" and nxt.value in ASSIGN_OPS:
                name = self.next().value
                op = ASSIGN_OPS[self.next().value]
                rhs = self.parse_expr(); self.end_line()
                return Let(name, Bin(op, Var(name), rhs))
            if nxt.kind == "op" and nxt.value == ".":
                call = self.parse_call_from_id()
                self.end_line()
                return CallStmt(call)
        raise SyntaxError(f"line {t.line}: cannot parse statement at {t.value!r}")

    def parse_let(self, eat_let: bool) -> Let:
        if eat_let:
            self.eat_kw("LET")
        name = self.next().value
        self.eat_op("=")
        v = self.parse_expr()
        self.end_line()
        return Let(name, v)

    def parse_dim(self) -> Dim:
        self.eat_kw("DIM")
        name = self.next().value
        init = None
        if self.peek().kind == "op" and self.peek().value == "=":
            self.next()
            init = self.parse_expr()
        self.end_line()
        return Dim(name, init)

    def parse_if(self) -> If:
        self.eat_kw("IF")
        cond = self.parse_condition()
        self.eat_kw("THEN")
        self.end_line()
        body = self.parse_block("ELSEIF", "ELSE", "ENDIF")
        arms = [(cond, body)]
        els = None
        while self.at_kw("ELSEIF"):
            self.eat_kw("ELSEIF")
            c2 = self.parse_condition()
            self.eat_kw("THEN")
            self.end_line()
            b2 = self.parse_block("ELSEIF", "ELSE", "ENDIF")
            arms.append((c2, b2))
        if self.at_kw("ELSE"):
            self.eat_kw("ELSE"); self.end_line()
            els = self.parse_block("ENDIF")
        self.eat_kw("ENDIF"); self.end_line()
        return If(arms, els)

    def parse_while(self) -> While:
        self.eat_kw("WHILE")
        cond = self.parse_condition()
        self.end_line()
        body = self.parse_block("ENDWHILE")
        self.eat_kw("ENDWHILE"); self.end_line()
        return While(cond, body)

    def parse_do(self) -> DoLoop:
        """DO [WHILE c | UNTIL c]  <body>  LOOP [WHILE c | UNTIL c].

        A WHILE/UNTIL guard may sit at the DO (pre-test) or the LOOP (post-test),
        but exactly one -- not both (which would be ambiguous) and not neither
        (which has no exit, since BASIC has no break keyword)."""
        self.eat_kw("DO")
        top_cond, top_until = None, False
        if self.at_kw("WHILE"):
            self.eat_kw("WHILE"); top_cond = self.parse_condition()
        elif self.at_kw("UNTIL"):
            self.eat_kw("UNTIL"); top_cond = self.parse_condition(); top_until = True
        self.end_line()
        body = self.parse_block("LOOP")
        self.eat_kw("LOOP")
        bottom_cond, bottom_until = None, False
        if self.at_kw("WHILE"):
            self.eat_kw("WHILE"); bottom_cond = self.parse_condition()
        elif self.at_kw("UNTIL"):
            self.eat_kw("UNTIL"); bottom_cond = self.parse_condition(); bottom_until = True
        self.end_line()
        if (top_cond is None) == (bottom_cond is None):
            raise SyntaxError("DO/LOOP needs a WHILE or UNTIL condition at exactly one of DO or LOOP")
        return DoLoop(top_cond, top_until, bottom_cond, bottom_until, body)

    def parse_for(self) -> ForTo:
        self.eat_kw("FOR")
        var = self.next().value
        self.eat_op("=")
        start = self.parse_expr()
        self.eat_kw("TO")
        end = self.parse_expr()
        step = None
        if self.at_kw("STEP"):
            self.eat_kw("STEP"); step = self.parse_expr()
        self.end_line()
        body = self.parse_block("NEXT")
        self.eat_kw("NEXT"); self.end_line()
        return ForTo(var, start, end, step, body)

    def parse_foreach(self) -> ForEach:
        self.eat_kw("FOREACH")
        var = self.next().value
        self.eat_kw("IN")
        count = self.parse_expr()
        self.end_line()
        body = self.parse_block("ENDFOREACH")
        self.eat_kw("ENDFOREACH"); self.end_line()
        return ForEach(var, count, body)

    def parse_switch(self) -> Switch:
        self.eat_kw("SWITCH")
        expr = self.parse_expr()
        self.end_line()
        self.skip_nl()
        cases = []
        default = None
        while not self.at_kw("ENDSWITCH"):
            if self.at_kw("CASE"):
                self.eat_kw("CASE")
                val = self.parse_expr()
                self.end_line()
                body = self.parse_block("CASE", "DEFAULT", "ENDSWITCH")
                cases.append((val, body))
            elif self.at_kw("DEFAULT"):
                self.eat_kw("DEFAULT"); self.end_line()
                default = self.parse_block("ENDSWITCH")
            else:
                raise SyntaxError(f"line {self.peek().line}: expected CASE/DEFAULT/ENDSWITCH")
        self.eat_kw("ENDSWITCH"); self.end_line()
        return Switch(expr, cases, default)

    def parse_sub(self) -> Sub:
        self.eat_kw("SUB")
        name = self.next().value
        self.end_line()
        body = self.parse_block("ENDSUB")
        self.eat_kw("ENDSUB"); self.end_line()
        return Sub(name, body)

    def parse_call_from_id(self) -> Call:
        ns = self.next().value
        self.eat_op(".")
        method = self.next().value
        args = self.parse_args()
        return Call(ns, method, args)

    def parse_args(self) -> list:
        self.eat_op("(")
        args = []
        if not (self.peek().kind == "op" and self.peek().value == ")"):
            args.append(self.parse_expr())
            while self.peek().kind == "op" and self.peek().value == ",":
                self.next(); args.append(self.parse_expr())
        self.eat_op(")")
        return args

    # -- conditions & expressions ----------------------------------------
    def parse_condition(self) -> object:
        # Conditions are ordinary expressions; comparisons (=, <, GT, ...) and
        # AND/OR/NOT are part of the expression grammar.
        return self.parse_expr()

    def parse_expr(self, min_prec: int = 0) -> object:
        left = self.parse_unary()
        while True:
            t = self.peek()
            opval = None
            if t.kind == "op" and t.value in _PREC:
                opval = t.value
            elif t.kind == "kw" and t.value in _PREC:
                opval = t.value
            if opval is None or _PREC[opval] < min_prec:
                break
            self.next()
            right = self.parse_expr(_PREC[opval] + 1)
            if opval in COMPARATORS:
                left = Cmp(COMPARATORS[opval], left, right)
            else:
                left = Bin(opval, left, right)
        return left

    def parse_unary(self) -> object:
        t = self.peek()
        if t.kind == "op" and t.value == "-":
            self.next(); return Bin("-", Num(0), self.parse_unary())
        if t.kind == "kw" and t.value == "NOT":
            self.next(); return Cmp("EQ", self.parse_unary(), Num(0))
        return self.parse_atom()

    def parse_atom(self) -> object:
        t = self.next()
        if t.kind == "num":
            return Num(int(t.value, 0))
        if t.kind == "str":
            return Str(t.value)
        if t.kind == "kw" and t.value == "IIF":
            self.eat_op("(")
            cond = self.parse_expr(); self.eat_op(",")
            then = self.parse_expr(); self.eat_op(",")
            els = self.parse_expr(); self.eat_op(")")
            return Ternary(cond, then, els)
        if t.kind == "op" and t.value == "(":
            e = self.parse_expr(); self.eat_op(")"); return e
        if t.kind == "id":
            if self.peek().kind == "op" and self.peek().value == ".":
                self.next()
                method = self.next().value
                args = self.parse_args()
                return Call(t.value, method, args)
            return Var(t.value)
        raise SyntaxError(f"line {t.line}: unexpected token {t.value!r}")


# ── lowering to PicoIL ──────────────────────────────────────────────────────

_ARITH = {"+": "add", "-": "sub", "*": "mul", "/": "div"}


class Lowerer:
    def __init__(self):
        self.b = ILBuilder()
        self.vars: Dict[str, VReg] = {}
        self.subs: List[Sub] = []
        # Stack of (continue_label_or_None, break_label) for BREAK/SKIP.
        # Loops push a continue label; SWITCH pushes None (breakable, not skippable).
        self.scopes: List[tuple] = []

    def var(self, name: str) -> VReg:
        key = name.upper()
        v = self.vars.get(key)
        if v is None:
            v = VReg(name, pinned=True)
            self.vars[key] = v
        return v

    def lower_program(self, prog: List[object]) -> List:
        body = [s for s in prog if not isinstance(s, Sub)]
        self.subs = [s for s in prog if isinstance(s, Sub)]
        for s in body:
            self.stmt(s)
        self.b.ret()
        for sub in self.subs:
            self.b.label(f"sub_{sub.name.upper()}")
            for s in sub.body:
                self.stmt(s)
            self.b.ret()
        return self.b.insts

    def stmt(self, s):
        if isinstance(s, Let):
            self.assign_to(self.var(s.name), s.value)
        elif isinstance(s, Dim):
            v = self.var(s.name)
            if s.init is None:
                self.b.const(v, 0)
            else:
                self.assign_to(v, s.init)
        elif isinstance(s, IncDec):
            v = self.var(s.name)
            if s.delta == 1:
                self.b.inc(v)
            else:
                self.b.arith("sub", v, v, Imm(1))
        elif isinstance(s, Label):
            self.b.label(f"lbl_{s.name.upper()}")
        elif isinstance(s, Goto):
            self.b.jmp(f"lbl_{s.label.upper()}")
        elif isinstance(s, Gosub):
            self.b.call(f"sub_{s.name.upper()}")
        elif isinstance(s, Return):
            self.b.ret()
        elif isinstance(s, Break):
            self.lower_break()
        elif isinstance(s, Skip):
            self.lower_skip()
        elif isinstance(s, If):
            self.lower_if(s)
        elif isinstance(s, While):
            self.lower_while(s)
        elif isinstance(s, DoLoop):
            self.lower_do(s)
        elif isinstance(s, ForTo):
            self.lower_for(s)
        elif isinstance(s, ForEach):
            self.lower_foreach(s)
        elif isinstance(s, Switch):
            self.lower_switch(s)
        elif isinstance(s, Print):
            self.lower_print(s)
        elif isinstance(s, CallStmt):
            self.lower_call(s.call, want_value=False)
        else:
            raise SyntaxError(f"cannot lower {s}")

    def assign_to(self, dst: VReg, expr):
        if isinstance(expr, Bin) and expr.op in _ARITH:
            a = self.eval(expr.lhs)
            if isinstance(expr.rhs, Num) and -32768 <= expr.rhs.value <= 65535:
                self.b.arith(_ARITH[expr.op], dst, a, Imm(expr.rhs.value))
                return
            bb = self.eval(expr.rhs)
            self.b.arith(_ARITH[expr.op], dst, a, bb)
            return
        self.b.mov(dst, self.eval(expr))

    def branch_false(self, cond, false_label: str):
        if isinstance(cond, Cmp):
            a = self.eval(cond.lhs); b = self.eval(cond.rhs)
            self.b.cmpbr(COND_NEGATE[cond.cond], a, b, false_label)
            return
        v = self.eval(cond)
        self.b.cmpbr("Z", v, v, false_label)

    def branch_true(self, cond, true_label: str):
        """Emit a branch to true_label when `cond` is true (used by DO/LOOP)."""
        if isinstance(cond, Cmp):
            a = self.eval(cond.lhs); b = self.eval(cond.rhs)
            self.b.cmpbr(cond.cond, a, b, true_label)
            return
        v = self.eval(cond)
        self.b.cmpbr("NZ", v, v, true_label)

    def lower_break(self):
        """BREAK -> innermost breakable scope (loop or SWITCH)."""
        if not self.scopes:
            raise SyntaxError("BREAK outside a loop or SWITCH")
        self.b.jmp(self.scopes[-1][1])

    def lower_skip(self):
        """SKIP -> innermost loop's continue point (skips enclosing SWITCHes)."""
        for cont, _brk in reversed(self.scopes):
            if cont is not None:
                self.b.jmp(cont)
                return
        raise SyntaxError("SKIP outside a loop")

    def lower_if(self, s: If):
        end = self.b.new_label("endif")
        for (cond, body) in s.arms:
            nxt = self.b.new_label("arm")
            self.branch_false(cond, nxt)
            for st in body:
                self.stmt(st)
            self.b.jmp(end)
            self.b.label(nxt)
        if s.els:
            for st in s.els:
                self.stmt(st)
        self.b.label(end)

    def lower_while(self, s: While):
        top = self.b.new_label("while"); end = self.b.new_label("endwhile")
        self.b.label(top)
        self.branch_false(s.cond, end)
        self.scopes.append((top, end))   # SKIP re-tests the condition at top
        for st in s.body:
            self.stmt(st)
        self.scopes.pop()
        self.b.jmp(top)
        self.b.label(end)

    def lower_do(self, s: DoLoop):
        top = self.b.new_label("do"); cont = self.b.new_label("docont")
        end = self.b.new_label("enddo")
        self.b.label(top)
        if s.top_cond is not None:
            # pre-test: exit before the body runs
            if s.top_until:
                self.branch_true(s.top_cond, end)    # DO UNTIL c -> exit when c true
            else:
                self.branch_false(s.top_cond, end)   # DO WHILE c -> exit when c false
        self.scopes.append((cont, end))
        for st in s.body:
            self.stmt(st)
        self.scopes.pop()
        self.b.label(cont)                           # SKIP -> the loop test
        if s.bottom_cond is not None:
            # post-test: body has already run at least once
            if s.bottom_until:
                self.branch_false(s.bottom_cond, top)  # LOOP UNTIL c -> repeat while c false
            else:
                self.branch_true(s.bottom_cond, top)   # LOOP WHILE c -> repeat while c true
        else:
            self.b.jmp(top)                            # pre-test form loops unconditionally
        self.b.label(end)

    def lower_for(self, s: ForTo):
        var = self.var(s.var)
        self.assign_to(var, s.start)
        endv = self.b.vreg("__for_end__")
        self.b.mov(endv, self.eval(s.end))
        top = self.b.new_label("for"); cont = self.b.new_label("forcont")
        end = self.b.new_label("endfor")
        self.b.label(top)
        self.b.cmpbr("GT", var, endv, end)   # exit when var > end (ascending)
        self.scopes.append((cont, end))
        for st in s.body:
            self.stmt(st)
        self.scopes.pop()
        self.b.label(cont)                   # SKIP -> advance the loop variable
        if s.step is not None and isinstance(s.step, Num):
            self.b.arith("add", var, var, Imm(s.step.value))
        elif s.step is not None:
            self.b.arith("add", var, var, self.eval(s.step))
        else:
            self.b.inc(var)
        self.b.jmp(top)
        self.b.label(end)

    def lower_foreach(self, s: ForEach):
        var = self.var(s.var)
        cnt = self.b.vreg("__fe_count__")
        self.b.mov(cnt, self.eval(s.count))
        self.b.const(var, 0)
        top = self.b.new_label("foreach"); cont = self.b.new_label("fecont")
        end = self.b.new_label("endforeach")
        self.b.label(top)
        self.b.cmpbr("GE", var, cnt, end)    # exit when index >= count
        self.scopes.append((cont, end))
        for st in s.body:
            self.stmt(st)
        self.scopes.pop()
        self.b.label(cont)                   # SKIP -> advance the index
        self.b.inc(var)
        self.b.jmp(top)
        self.b.label(end)

    def lower_switch(self, s: Switch):
        sel = self.eval(s.expr)
        end = self.b.new_label("endswitch")
        case_labels = [self.b.new_label("case") for _ in s.cases]
        default_l = self.b.new_label("default")
        for (val, _), lbl in zip(s.cases, case_labels):
            cv = self.eval(val)
            self.b.cmpbr("EQ", sel, cv, lbl)
        self.b.jmp(default_l)
        self.scopes.append((None, end))      # breakable (BREAK), not skippable
        for (_, body), lbl in zip(s.cases, case_labels):
            self.b.label(lbl)
            for st in body:
                self.stmt(st)
            self.b.jmp(end)
        self.b.label(default_l)
        if s.default:
            for st in s.default:
                self.stmt(st)
        self.scopes.pop()
        self.b.label(end)

    def lower_print(self, s: Print):
        v = self.eval(s.value)
        self.b.save(v, PRINT_CARD)
        self.b.pipe(v, PRINT_CARD)

    # -- expressions -----------------------------------------------------
    def eval(self, e) -> VReg:
        if isinstance(e, Num):
            v = self.b.vreg(); self.b.const(v, e.value); return v
        if isinstance(e, Var):
            return self.var(e.name)
        if isinstance(e, Bin):
            if e.op in ("AND", "OR"):
                return self.eval_logical(e)
            if e.op == "MOD":
                return self.eval_mod(e.lhs, e.rhs)
            a = self.eval(e.lhs)
            dst = self.b.vreg()
            if isinstance(e.rhs, Num) and -32768 <= e.rhs.value <= 65535:
                self.b.arith(_ARITH[e.op], dst, a, Imm(e.rhs.value))
            else:
                b = self.eval(e.rhs)
                self.b.arith(_ARITH[e.op], dst, a, b)
            return dst
        if isinstance(e, Cmp):
            return self.eval_bool(e)
        if isinstance(e, Ternary):
            return self.eval_ternary(e)
        if isinstance(e, Call):
            r = self.lower_call(e, want_value=True)
            if r is None:
                raise SyntaxError(f"{e.ns}.{e.method} does not return a value")
            return r
        if isinstance(e, Str):
            raise SyntaxError("string literal only valid as a Net/host argument")
        raise SyntaxError(f"cannot evaluate {e}")

    def eval_mod(self, lhs, rhs) -> VReg:
        # a MOD b  ->  a - (a / b) * b   (no MOD opcode in the ISA)
        a = self.eval(lhs); b = self.eval(rhs)
        q = self.b.vreg(); self.b.arith("div", q, a, b)
        m = self.b.vreg(); self.b.arith("mul", m, q, b)
        dst = self.b.vreg(); self.b.arith("sub", dst, a, m)
        return dst

    def eval_logical(self, e: Bin) -> VReg:
        dst = self.b.vreg()
        a = self.eval(e.lhs)
        end_l = self.b.new_label("lend")
        if e.op == "AND":
            false_l = self.b.new_label("land0")
            self.b.cmpbr("Z", a, a, false_l)
            b = self.eval(e.rhs)
            self.b.cmpbr("Z", b, b, false_l)
            self.b.const(dst, 1); self.b.jmp(end_l)
            self.b.label(false_l); self.b.const(dst, 0)
        else:  # OR
            true_l = self.b.new_label("lor1")
            self.b.cmpbr("NZ", a, a, true_l)
            b = self.eval(e.rhs)
            self.b.cmpbr("NZ", b, b, true_l)
            self.b.const(dst, 0); self.b.jmp(end_l)
            self.b.label(true_l); self.b.const(dst, 1)
        self.b.label(end_l)
        return dst

    def eval_ternary(self, e: Ternary) -> VReg:
        dst = self.b.vreg()
        else_l = self.b.new_label("telse"); end_l = self.b.new_label("tend")
        self.branch_false(e.cond, else_l)
        tv = self.eval(e.then); self.b.mov(dst, tv); self.b.jmp(end_l)
        self.b.label(else_l)
        ev = self.eval(e.els); self.b.mov(dst, ev)
        self.b.label(end_l)
        return dst

    def eval_bool(self, e: Cmp) -> VReg:
        a = self.eval(e.lhs); b = self.eval(e.rhs)
        dst = self.b.vreg()
        true_l = self.b.new_label("bt"); end_l = self.b.new_label("be")
        self.b.cmpbr(e.cond, a, b, true_l)
        self.b.const(dst, 0); self.b.jmp(end_l)
        self.b.label(true_l); self.b.const(dst, 1)
        self.b.label(end_l)
        return dst

    def lower_call(self, c: Call, want_value: bool) -> Optional[VReg]:
        ns, method = c.ns, c.method
        if ns is not None and ns.upper() == "NET":
            m = method.upper()
            if m == "STATUS":
                self.b.net("status", _intlit(c.args[0]))
            elif m == "TYPE":
                self.b.net("type", _strlit(c.args[0]))
            elif m == "BODY":
                self.b.net("body")
            elif m == "CLOSE":
                self.b.net("close")
            elif m == "HEADER":
                self.b.net("header")
            else:
                raise SyntaxError(f"unknown Net.{method}")
            return None
        if ns is not None and ns.upper() == "STORAGE" and method.upper() in ("LOAD", "SAVE", "PIPE"):
            addr = encode_card_addr(_intlit(c.args[0]), _intlit(c.args[1]), _intlit(c.args[2]))
            reg = self.eval(c.args[3])
            mm = method.upper()
            if mm == "LOAD":
                self.b.load(reg, addr)
            elif mm == "SAVE":
                self.b.save(reg, addr)
            else:
                self.b.pipe(reg, addr)
            return reg
        argregs = [self.eval(a) for a in c.args[:2]]
        dst = self.b.vreg() if want_value else None
        ns, method = canon_host(ns, method)
        self.b.host(ns, method, tuple(argregs), dst)
        return dst


def _intlit(node) -> int:
    if isinstance(node, Num):
        return node.value
    raise SyntaxError("expected integer literal")


def _strlit(node) -> str:
    if isinstance(node, Str):
        return node.value
    raise SyntaxError("expected string literal")


# ── public API ──────────────────────────────────────────────────────────────

def compile_basic(source: str):
    """BASIC-like source -> PicoIL instruction list."""
    toks = tokenize(source)
    prog = Parser(toks).parse_program()
    return Lowerer().lower_program(prog)
