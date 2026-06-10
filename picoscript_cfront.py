#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_cfront.py -- C-syntax frontend for PicoScript.

A small C-like surface that lowers to PicoIL (picoscript_il.py) and therefore runs
on PicoVM or compiles to bytecode / native C exactly like every other frontend.

Supported surface
-----------------
  // line comments and /* block comments */
  int x = 5;            // declaration (single global scope)
  int y;                // default 0
  x = y + 3 * (x - 1);  // assignment, + - * / and parentheses
  if (x < 10) { ... } else { ... }
  while (x > 0) { x = x - 1; }
  for (i = 0; i < 8; i = i + 1) { ... }
  return x;             // sets retval, ends current routine
  void worker() { ... } // parameterless subroutine (OP_CALL/OP_RETURN)
  worker();             // call
  Net.Status(200); Net.Type("text/html"); Net.Body(); Net.Close();
  Storage.Load(0,3,0, x);  Storage.Save(0,3,0, x);  Storage.Pipe(0,3,0, x);
  r = Crypto.Sha256(a, b); // generic host call (<=2 reg args, optional result)

Comparisons are first-class only inside if/while/for conditions, and may also be
assigned (materialized to 0/1).  Everything is a 32-bit word (int64 in the C
backend); there is one global variable scope shared across subroutines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union, Dict

from picoscript_il import ILBuilder, VReg, Imm, COND, COND_NEGATE, canon_host
from picoscript_lang import encode_card_addr

# ── tokens ──────────────────────────────────────────────────────────────────

KEYWORDS = {"int", "var", "void", "if", "else", "while", "for", "return",
            "break", "continue", "switch", "case", "default", "do", "goto",
            "dispatch"}

_TWO = {"==", "!=", "<=", ">=", "&&", "||", "++", "--", "+=", "-=", "*=", "/=", "%="}
_ONE = set("+-*/%()<>=;,{}.!?:")


@dataclass
class Tok:
    kind: str   # 'num','id','str','op','kw','eof'
    value: str
    pos: int


def tokenize(src: str) -> List[Tok]:
    toks: List[Tok] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n and src[j] != '"':
                if src[j] == "\\" and j + 1 < n:
                    buf.append(src[j + 1]); j += 2; continue
                buf.append(src[j]); j += 1
            toks.append(Tok("str", "".join(buf), i))
            i = j + 1
            continue
        if c.isdigit() or (c == "0" and i + 1 < n and src[i + 1] in "xX"):
            j = i
            if src[j] == "0" and j + 1 < n and src[j + 1] in "xX":
                j += 2
                while j < n and src[j] in "0123456789abcdefABCDEF":
                    j += 1
            else:
                while j < n and src[j].isdigit():
                    j += 1
            toks.append(Tok("num", src[i:j], i))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            low = word.lower()
            if low in KEYWORDS:
                toks.append(Tok("kw", low, i))     # keywords: case-insensitive
            else:
                toks.append(Tok("id", word, i))    # identifiers keep case for Ns.Method
            i = j
            continue
        two = src[i:i + 2]
        if two in _TWO:
            toks.append(Tok("op", two, i)); i += 2; continue
        if c in _ONE:
            toks.append(Tok("op", c, i)); i += 1; continue
        raise SyntaxError(f"unexpected char {c!r} at {i}")
    toks.append(Tok("eof", "", n))
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
class Unary:
    op: str; operand: object
@dataclass
class IncDec:
    op: str; target: object; prefix: bool
@dataclass
class Ternary:
    cond: object; then: object; els: object
@dataclass
class Call:
    ns: Optional[str]; method: str; args: list
@dataclass
class Decl:
    name: str; init: object
@dataclass
class Assign:
    name: str; value: object
@dataclass
class If:
    cond: object; then: list; els: Optional[list]
@dataclass
class While:
    cond: object; body: list
@dataclass
class For:
    init: object; cond: object; step: object; body: list
@dataclass
class Return:
    value: Optional[object]
@dataclass
class Break: pass
@dataclass
class Continue: pass
@dataclass
class Switch:
    expr: object; cases: list; default: Optional[list]   # cases = [(value, body), ...]
@dataclass
class Dispatch:
    expr: object; cases: list; default: Optional[list]   # jump-table switch (dense int cases)
@dataclass
class DoWhile:
    cond: object; until: bool; body: list
@dataclass
class Goto:
    label: str
@dataclass
class Label:
    name: str
@dataclass
class ExprStmt:
    expr: object
@dataclass
class Func:
    name: str; body: list


# ── parser (recursive descent + Pratt expressions) ──────────────────────────

_PREC = {
    "||": 1, "&&": 2,
    "==": 3, "!=": 3, "<": 4, ">": 4, "<=": 4, ">=": 4,
    "+": 5, "-": 5, "*": 6, "/": 6, "%": 6,
}
_COMPOUND = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%"}


class Parser:
    def __init__(self, toks: List[Tok]):
        self.toks = toks
        self.i = 0

    def peek(self) -> Tok:
        return self.toks[self.i]

    def next(self) -> Tok:
        t = self.toks[self.i]; self.i += 1; return t

    def accept(self, value: str) -> bool:
        t = self.peek()
        if t.value == value and t.kind in ("op", "kw"):
            self.i += 1; return True
        return False

    def expect(self, value: str) -> Tok:
        t = self.peek()
        if t.value != value:
            raise SyntaxError(f"expected {value!r}, got {t.value!r} at {t.pos}")
        return self.next()

    # -- program ---------------------------------------------------------
    def parse_program(self) -> List[object]:
        stmts = []
        while self.peek().kind != "eof":
            stmts.append(self.parse_toplevel())
        return stmts

    def parse_toplevel(self) -> object:
        t = self.peek()
        # void name() { } subroutine
        if t.kind == "kw" and t.value == "void":
            self.next()
            name = self.next().value
            self.expect("("); self.expect(")")
            body = self.parse_block()
            return Func(name, body)
        return self.parse_stmt()

    def parse_block(self) -> List[object]:
        self.expect("{")
        stmts = []
        while not self.accept("}"):
            if self.peek().kind == "eof":
                raise SyntaxError("unterminated block")
            stmts.append(self.parse_stmt())
        return stmts

    def parse_stmt(self) -> object:
        t = self.peek()
        if t.kind == "kw":
            if t.value in ("int", "var"):
                return self.parse_decl()
            if t.value == "if":
                return self.parse_if()
            if t.value == "while":
                return self.parse_while()
            if t.value == "for":
                return self.parse_for()
            if t.value == "switch":
                return self.parse_switch()
            if t.value == "dispatch":
                return self.parse_dispatch()
            if t.value == "do":
                return self.parse_do()
            if t.value == "goto":
                self.next(); name = self.next().value; self.expect(";"); return Goto(name)
            if t.value == "return":
                self.next()
                if self.accept(";"):
                    return Return(None)
                v = self.parse_expr(); self.expect(";"); return Return(v)
            if t.value == "break":
                self.next(); self.expect(";"); return Break()
            if t.value == "continue":
                self.next(); self.expect(";"); return Continue()
        if t.value == "{":
            return ExprStmt(None) if False else self._block_stmt()
        # label:  name :
        if t.kind == "id" and self.toks[self.i + 1].value == ":":
            name = self.next().value
            self.next()  # ':'
            return Label(name)
        # assignment or expression statement
        if t.kind == "id" and self.toks[self.i + 1].value == "=":
            name = self.next().value
            self.expect("=")
            v = self.parse_expr()
            self.expect(";")
            return Assign(name, v)
        if t.kind == "id" and self.toks[self.i + 1].value in _COMPOUND:
            name = self.next().value
            op = _COMPOUND[self.next().value]
            v = self.parse_expr()
            self.expect(";")
            return Assign(name, Bin(op, Var(name), v))
        expr = self.parse_expr()
        self.expect(";")
        return ExprStmt(expr)

    def _block_stmt(self):
        body = self.parse_block()
        return If(Num(1), body, None)  # bare block == always-true if (keeps scope flat)

    def parse_decl(self) -> Decl:
        self.next()  # int / var
        name = self.next().value
        init = None
        if self.accept("="):
            init = self.parse_expr()
        self.expect(";")
        return Decl(name, init)

    def parse_if(self) -> If:
        self.next(); self.expect("(")
        cond = self.parse_expr(); self.expect(")")
        then = self.parse_block()
        els = None
        if self.accept("else"):
            els = self.parse_block() if self.peek().value == "{" else [self.parse_if()]
        return If(cond, then, els)

    def parse_while(self) -> While:
        self.next(); self.expect("(")
        cond = self.parse_expr(); self.expect(")")
        return While(cond, self.parse_block())

    def parse_for(self) -> For:
        self.next(); self.expect("(")
        init = None
        if not self.accept(";"):
            if self.peek().value in ("int", "var"):
                init = self.parse_decl_noeat_semicolon()
            else:
                name = self.next().value; self.expect("="); init = Assign(name, self.parse_expr())
                self.expect(";")
        cond = None
        if not self.accept(";"):
            cond = self.parse_expr(); self.expect(";")
        step = None
        if self.peek().value != ")":
            if self.peek().kind == "id" and self.toks[self.i + 1].value == "=":
                name = self.next().value; self.expect("="); step = Assign(name, self.parse_expr())
            elif self.peek().kind == "id" and self.toks[self.i + 1].value in _COMPOUND:
                name = self.next().value; op = _COMPOUND[self.next().value]
                step = Assign(name, Bin(op, Var(name), self.parse_expr()))
            else:
                step = ExprStmt(self.parse_expr())   # e.g. i++
        self.expect(")")
        return For(init, cond, step, self.parse_block())

    def parse_switch(self) -> Switch:
        self.next(); self.expect("(")
        expr = self.parse_expr()
        self.expect(")"); self.expect("{")
        cases = []
        default = None
        while not self.accept("}"):
            t = self.peek()
            if t.kind == "kw" and t.value == "case":
                self.next()
                val = self.parse_expr()
                self.expect(":")
                cases.append((val, self.parse_case_body()))
            elif t.kind == "kw" and t.value == "default":
                self.next(); self.expect(":")
                default = self.parse_case_body()
            else:
                raise SyntaxError(f"line {t.line}: expected case/default in switch")
        return Switch(expr, cases, default)

    def parse_dispatch(self) -> Dispatch:
        """dispatch (expr) { case N: ...; default: ... } -- a jump-table switch over
        dense non-negative integer cases (compiles to an indexed jump)."""
        self.next(); self.expect("(")
        expr = self.parse_expr()
        self.expect(")"); self.expect("{")
        cases = []
        default = None
        while not self.accept("}"):
            t = self.peek()
            if t.kind == "kw" and t.value == "case":
                self.next()
                val = self.parse_expr()
                self.expect(":")
                cases.append((val, self.parse_case_body()))
            elif t.kind == "kw" and t.value == "default":
                self.next(); self.expect(":")
                default = self.parse_case_body()
            else:
                raise SyntaxError(f"line {t.line}: expected case/default in dispatch")
        return Dispatch(expr, cases, default)

    def parse_case_body(self) -> list:
        """Statements until the next case/default/} ; a trailing `break;` is
        consumed (each case is independent -- no C fall-through)."""
        stmts = []
        while True:
            t = self.peek()
            if t.value == "}":
                break
            if t.kind == "kw" and t.value in ("case", "default"):
                break
            if t.kind == "kw" and t.value == "break":
                self.next(); self.expect(";")
                break
            stmts.append(self.parse_stmt())
        return stmts

    def parse_do(self) -> DoWhile:
        self.next()                              # do
        body = self.parse_block()
        if not (self.peek().kind == "kw" and self.peek().value == "while"):
            raise SyntaxError(f"line {self.peek().line}: expected 'while' after do block")
        self.next(); self.expect("(")
        cond = self.parse_expr()
        self.expect(")"); self.expect(";")
        return DoWhile(cond, False, body)

    def parse_decl_noeat_semicolon(self) -> Decl:
        self.next()
        name = self.next().value
        init = None
        if self.accept("="):
            init = self.parse_expr()
        self.expect(";")
        return Decl(name, init)

    # -- expressions (Pratt) ---------------------------------------------
    def parse_expr(self, min_prec: int = 0) -> object:
        return self.parse_ternary()

    def parse_ternary(self) -> object:
        cond = self.parse_binary(0)
        if self.peek().kind == "op" and self.peek().value == "?":
            self.next()
            then = self.parse_expr()
            self.expect(":")
            els = self.parse_ternary()
            return Ternary(cond, then, els)
        return cond

    def parse_binary(self, min_prec: int = 0) -> object:
        left = self.parse_unary()
        while True:
            t = self.peek()
            if t.kind != "op" or t.value not in _PREC or _PREC[t.value] < min_prec:
                break
            op = self.next().value
            right = self.parse_binary(_PREC[op] + 1)
            left = Bin(op, left, right)
        return left

    def parse_unary(self) -> object:
        t = self.peek()
        if t.kind == "op" and t.value in ("++", "--"):
            op = self.next().value
            return IncDec(op, self.parse_unary(), True)
        if t.value in ("-", "!") and t.kind == "op":
            op = self.next().value
            return Unary(op, self.parse_unary())
        return self.parse_atom()

    def parse_atom(self) -> object:
        node = self._parse_primary()
        while self.peek().kind == "op" and self.peek().value in ("++", "--"):
            op = self.next().value
            node = IncDec(op, node, False)
        return node

    def _parse_primary(self) -> object:
        t = self.next()
        if t.kind == "num":
            return Num(int(t.value, 0))
        if t.kind == "str":
            return Str(t.value)
        if t.value == "(":
            e = self.parse_expr(); self.expect(")"); return e
        if t.kind == "id":
            # Ns.Method(...) or name(...) or bare variable
            if self.peek().value == ".":
                self.next()
                method = self.next().value
                args = self.parse_args()
                return Call(t.value, method, args)
            if self.peek().value == "(":
                args = self.parse_args()
                return Call(None, t.value, args)
            return Var(t.value)
        raise SyntaxError(f"unexpected token {t.value!r} at {t.pos}")

    def parse_args(self) -> list:
        self.expect("(")
        args = []
        if not self.accept(")"):
            args.append(self.parse_expr())
            while self.accept(","):
                args.append(self.parse_expr())
            self.expect(")")
        return args


# ── lowering: AST -> PicoIL ─────────────────────────────────────────────────

_CMP_OPS = {"<": "LT", ">": "GT", "<=": "LE", ">=": "GE", "==": "EQ", "!=": "NE"}


class Lowerer:
    def __init__(self):
        self.b = ILBuilder()
        self.vars: Dict[str, VReg] = {}
        self.funcs: List[Func] = []
        self.loop_stack: List[Tuple[str, str]] = []   # (continue_label, break_label)
        self._strlit_n = 0          # alternating scratch region per string literal

    def lower_program(self, prog: List[object]) -> List:
        body = [s for s in prog if not isinstance(s, Func)]
        self.funcs = [s for s in prog if isinstance(s, Func)]
        for s in body:
            self.stmt(s)
        self.b.ret()
        for f in self.funcs:
            self.b.label(f"fn_{f.name.lower()}")
            for s in f.body:
                self.stmt(s)
            self.b.ret()
        return self.b.insts

    # -- variables -------------------------------------------------------
    def var(self, name: str) -> VReg:
        key = name.lower()                          # variables: case-insensitive
        v = self.vars.get(key)
        if v is None:
            v = VReg(name, pinned=True)
            self.vars[key] = v
        return v

    # -- statements ------------------------------------------------------
    def stmt(self, s):
        if isinstance(s, Decl):
            v = self.var(s.name)
            if s.init is not None:
                self.assign_to(v, s.init)
            else:
                self.b.const(v, 0)
        elif isinstance(s, Assign):
            self.assign_to(self.var(s.name), s.value)
        elif isinstance(s, If):
            self.lower_if(s)
        elif isinstance(s, While):
            self.lower_while(s)
        elif isinstance(s, For):
            self.lower_for(s)
        elif isinstance(s, Switch):
            self.lower_switch(s)
        elif isinstance(s, Dispatch):
            self.lower_dispatch(s)
        elif isinstance(s, DoWhile):
            self.lower_dowhile(s)
        elif isinstance(s, Goto):
            self.b.jmp(f"lbl_{s.label.lower()}")
        elif isinstance(s, Label):
            self.b.label(f"lbl_{s.name.lower()}")
        elif isinstance(s, Return):
            if s.value is not None:
                rv = self.eval(s.value)
                # convention: retval lives in the routine's value; mirror to VReg ret
                self.b.mov(self.var("__ret__"), rv)
            self.b.ret()
        elif isinstance(s, ExprStmt):
            if s.expr is not None:
                self.eval(s.expr, want_value=False)
        elif isinstance(s, Break):
            if not self.loop_stack:
                raise SyntaxError("break outside loop")
            self.b.jmp(self.loop_stack[-1][1])
        elif isinstance(s, Continue):
            if not self.loop_stack:
                raise SyntaxError("continue outside loop")
            self.b.jmp(self.loop_stack[-1][0])
        else:
            raise SyntaxError(f"cannot lower statement {s}")

    def assign_to(self, dst: VReg, expr):
        # Fast path: dst = a OP b  with immediate RHS -> single arith op.
        if isinstance(expr, Bin) and expr.op in ("+", "-", "*", "/"):
            a = self.eval(expr.lhs)
            if isinstance(expr.rhs, Num) and -32768 <= expr.rhs.value <= 65535:
                self.b.arith({"+": "add", "-": "sub", "*": "mul", "/": "div"}[expr.op],
                             dst, a, Imm(expr.rhs.value))
                return
            bb = self.eval(expr.rhs)
            self.b.arith({"+": "add", "-": "sub", "*": "mul", "/": "div"}[expr.op], dst, a, bb)
            return
        val = self.eval(expr)
        self.b.mov(dst, val)

    def lower_if(self, s: If):
        else_l = self.b.new_label("else")
        end_l = self.b.new_label("endif")
        self.branch_false(s.cond, else_l)
        for st in s.then:
            self.stmt(st)
        if s.els:
            self.b.jmp(end_l)
            self.b.label(else_l)
            for st in s.els:
                self.stmt(st)
            self.b.label(end_l)
        else:
            self.b.label(else_l)

    def lower_while(self, s: While):
        top = self.b.new_label("while")
        end = self.b.new_label("endwhile")
        self.b.label(top)
        self.branch_false(s.cond, end)
        self.loop_stack.append((top, end))
        for st in s.body:
            self.stmt(st)
        self.loop_stack.pop()
        self.b.jmp(top)
        self.b.label(end)

    def lower_for(self, s: For):
        if s.init:
            self.stmt(s.init)
        top = self.b.new_label("for")
        cont = self.b.new_label("forcont")
        end = self.b.new_label("endfor")
        self.b.label(top)
        if s.cond:
            self.branch_false(s.cond, end)
        self.loop_stack.append((cont, end))
        for st in s.body:
            self.stmt(st)
        self.loop_stack.pop()
        self.b.label(cont)
        if s.step:
            self.stmt(s.step)
        self.b.jmp(top)
        self.b.label(end)

    def lower_switch(self, s: Switch):
        val = self.eval(s.expr)
        end = self.b.new_label("endsw")
        prev_cont = self.loop_stack[-1][0] if self.loop_stack else end
        self.loop_stack.append((prev_cont, end))     # break -> end; continue -> enclosing loop
        for (cv, body) in s.cases:
            nxt = self.b.new_label("case")
            self.branch_false(Bin("==", _RawVReg(val), cv), nxt)
            for st in body:
                self.stmt(st)
            self.b.jmp(end)
            self.b.label(nxt)
        if s.default:
            for st in s.default:
                self.stmt(st)
        self.loop_stack.pop()
        self.b.label(end)

    def lower_dispatch(self, s: Dispatch):
        """Lower a dispatch to a bounds-checked jump table: guard the selector into
        [0, N), then an indexed jump (jmptab) to the matching case (or default).
        Cases do NOT fall through -- each is independent, like a state handler."""
        sel = self.eval(s.expr)
        end = self.b.new_label("enddisp")
        default_lbl = self.b.new_label("dispdef")
        prev_cont = self.loop_stack[-1][0] if self.loop_stack else end
        self.loop_stack.append((prev_cont, end))     # break -> end; continue -> enclosing loop
        pairs = []
        for (cv, body) in s.cases:
            if not isinstance(cv, Num) or cv.value < 0:
                raise SyntaxError("dispatch case must be a constant non-negative integer")
            pairs.append((cv.value, body))
        n = max((v for v, _ in pairs), default=0) + 1
        table = [default_lbl] * n
        bodies = []
        for v, body in pairs:
            lbl = self.b.new_label("dcase")
            table[v] = lbl
            bodies.append((lbl, body))
        nreg = self.b.vreg(); self.b.const(nreg, n)
        self.b.cmpbr("GE", sel, nreg, default_lbl)   # selector >= N -> default
        zreg = self.b.vreg(); self.b.const(zreg, 0)
        self.b.cmpbr("LT", sel, zreg, default_lbl)   # selector < 0  -> default
        self.b.jmptab(sel, tuple(table), default_lbl)
        for lbl, body in bodies:
            self.b.label(lbl)
            for st in body:
                self.stmt(st)
            self.b.jmp(end)
        self.b.label(default_lbl)
        if s.default:
            for st in s.default:
                self.stmt(st)
        self.loop_stack.pop()
        self.b.label(end)

    def lower_dowhile(self, s: DoWhile):
        top = self.b.new_label("do")
        cont = self.b.new_label("docont")
        end = self.b.new_label("enddo")
        self.b.label(top)
        self.loop_stack.append((cont, end))
        for st in s.body:
            self.stmt(st)
        self.loop_stack.pop()
        self.b.label(cont)
        if s.until:
            self.branch_false(s.cond, top)           # until: loop while cond false
        else:
            self.branch_false(s.cond, end)           # while: exit when cond false
            self.b.jmp(top)
        self.b.label(end)

    def branch_false(self, cond, false_label: str):
        """Emit a branch to false_label when `cond` is false (fall through if true)."""
        if isinstance(cond, Bin) and cond.op in _CMP_OPS:
            a = self.eval(cond.lhs)
            b = self.eval(cond.rhs)
            self.b.cmpbr(COND_NEGATE[_CMP_OPS[cond.op]], a, b, false_label)
            return
        v = self.eval(cond)
        self.b.cmpbr("Z", v, v, false_label)   # if v == 0 -> false

    # -- expressions -----------------------------------------------------
    def eval(self, e, want_value: bool = True) -> Optional[VReg]:
        if isinstance(e, Num):
            v = self.b.vreg(); self.b.const(v, e.value); return v
        if isinstance(e, Var):
            return self.var(e.name)
        if isinstance(e, Bin):
            if e.op in _CMP_OPS:
                return self.eval_bool(e)
            if e.op in ("&&", "||"):
                return self.eval_logical(e)
            if e.op == "%":
                return self.eval_mod(e.lhs, e.rhs)
            a = self.eval(e.lhs)
            dst = self.b.vreg()
            if isinstance(e.rhs, Num) and -32768 <= e.rhs.value <= 65535:
                self.b.arith({"+": "add", "-": "sub", "*": "mul", "/": "div"}[e.op],
                             dst, a, Imm(e.rhs.value))
            else:
                b = self.eval(e.rhs)
                self.b.arith({"+": "add", "-": "sub", "*": "mul", "/": "div"}[e.op], dst, a, b)
            return dst
        if isinstance(e, IncDec):
            return self.eval_incdec(e)
        if isinstance(e, Ternary):
            return self.eval_ternary(e)
        if isinstance(e, Unary):
            if e.op == "-":
                z = self.b.vreg(); self.b.const(z, 0)
                inner = self.eval(e.operand)
                dst = self.b.vreg(); self.b.arith("sub", dst, z, inner); return dst
            if e.op == "!":
                inner = self.eval(e.operand)
                return self.eval_bool(Bin("==", _RawVReg(inner), Num(0)))
        if isinstance(e, Call):
            return self.lower_call(e, want_value)
        if isinstance(e, Str):
            return self.emit_str_span(e.value)
        if isinstance(e, _RawVReg):
            return e.v
        raise SyntaxError(f"cannot evaluate {e}")

    def eval_mod(self, lhs, rhs) -> VReg:
        a = self.eval(lhs); b = self.eval(rhs)
        q = self.b.vreg(); self.b.arith("div", q, a, b)
        m = self.b.vreg(); self.b.arith("mul", m, q, b)
        dst = self.b.vreg(); self.b.arith("sub", dst, a, m)
        return dst

    def eval_logical(self, e: Bin) -> VReg:
        dst = self.b.vreg()
        a = self.eval(e.lhs)
        end_l = self.b.new_label("lend")
        if e.op == "&&":
            false_l = self.b.new_label("land0")
            self.b.cmpbr("Z", a, a, false_l)
            b = self.eval(e.rhs)
            self.b.cmpbr("Z", b, b, false_l)
            self.b.const(dst, 1); self.b.jmp(end_l)
            self.b.label(false_l); self.b.const(dst, 0)
        else:
            true_l = self.b.new_label("lor1")
            self.b.cmpbr("NZ", a, a, true_l)
            b = self.eval(e.rhs)
            self.b.cmpbr("NZ", b, b, true_l)
            self.b.const(dst, 0); self.b.jmp(end_l)
            self.b.label(true_l); self.b.const(dst, 1)
        self.b.label(end_l)
        return dst

    def eval_incdec(self, e: IncDec) -> VReg:
        if not isinstance(e.target, Var):
            raise SyntaxError("++/-- requires a variable")
        v = self.var(e.target.name)
        if e.prefix:
            if e.op == "++":
                self.b.inc(v)
            else:
                self.b.arith("sub", v, v, Imm(1))
            return v
        old = self.b.vreg(); self.b.mov(old, v)
        if e.op == "++":
            self.b.inc(v)
        else:
            self.b.arith("sub", v, v, Imm(1))
        return old

    def eval_ternary(self, e: Ternary) -> VReg:
        dst = self.b.vreg()
        else_l = self.b.new_label("telse"); end_l = self.b.new_label("tend")
        self.branch_false(e.cond, else_l)
        tv = self.eval(e.then); self.b.mov(dst, tv); self.b.jmp(end_l)
        self.b.label(else_l)
        ev = self.eval(e.els); self.b.mov(dst, ev)
        self.b.label(end_l)
        return dst

    def eval_bool(self, e: Bin) -> VReg:
        """Materialize a comparison into a 0/1 result register."""
        a = self.eval(e.lhs)
        b = self.eval(e.rhs)
        dst = self.b.vreg()
        true_l = self.b.new_label("bt")
        end_l = self.b.new_label("be")
        self.b.cmpbr(_CMP_OPS[e.op], a, b, true_l)
        self.b.const(dst, 0)
        self.b.jmp(end_l)
        self.b.label(true_l)
        self.b.const(dst, 1)
        self.b.label(end_l)
        return dst

    def lower_call(self, c: Call, want_value: bool) -> Optional[VReg]:
        ns, method = c.ns, c.method
        # local subroutine call (case-insensitive); print(x) is a built-in
        if ns is None:
            if method.lower() == "print":
                if isinstance(c.args[0], Str):
                    self.b.host("Io", "Write", (self.emit_str_span(c.args[0].value),), None)
                    return None
                v = self.eval(c.args[0])
                self.b.save(v, 0xFFFE)
                self.b.pipe(v, 0xFFFE)
                return None
            self.b.call(f"fn_{method.lower()}")
            return None
        # Net.*  (namespace + method case-insensitive)
        if ns.upper() == "NET":
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
        # Storage.Load/Save/Pipe(tenant, pack, card, reg)
        if ns.upper() == "STORAGE" and method.upper() in ("LOAD", "SAVE", "PIPE"):
            tenant, pack, card = (_intlit(c.args[0]), _intlit(c.args[1]), _intlit(c.args[2]))
            addr = encode_card_addr(tenant, pack, card)
            reg = self.eval(c.args[3])
            mm = method.upper()
            if mm == "LOAD":
                self.b.load(reg, addr)
            elif mm == "SAVE":
                self.b.save(reg, addr)
            else:
                self.b.pipe(reg, addr)
            return reg
        # generic host hook: resolve to canonical ABI spelling (case-insensitive)
        ns, method = canon_host(ns, method)
        argregs = [self.eval(a) for a in c.args[:2]]
        dst = self.b.vreg() if want_value else None
        self.b.host(ns, method, tuple(argregs), dst)
        return dst

    def emit_str_span(self, text: str):
        """Stage a string literal's UTF-8 bytes in a scratch arena region and
        return a span over them (two alternating slots; mirrors picoscript_basic)."""
        data = text.encode("utf-8")
        base = 0x7E00 + (self._strlit_n & 1) * 0x100
        self._strlit_n += 1
        areg = self.b.vreg(); vreg = self.b.vreg()
        for i, byte in enumerate(data):
            self.b.const(areg, base + i)
            self.b.const(vreg, byte)
            self.b.host("Memory", "Set", (areg, vreg), None)
        self.b.const(areg, base)
        self.b.const(vreg, len(data))
        span = self.b.vreg()
        self.b.host("Span", "Make", (areg, vreg), span)
        return span


@dataclass
class _RawVReg:
    v: VReg


def _intlit(node) -> int:
    if isinstance(node, Num):
        return node.value
    raise SyntaxError("expected integer literal")


def _strlit(node) -> str:
    if isinstance(node, Str):
        return node.value
    raise SyntaxError("expected string literal")


# ── public API ──────────────────────────────────────────────────────────────

def compile_c(source: str):
    """C-syntax source -> PicoIL instruction list."""
    toks = tokenize(source)
    prog = Parser(toks).parse_program()
    return Lowerer().lower_program(prog)
