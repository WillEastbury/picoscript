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
from picoscript_lang import encode_card_addr, resolve_named_constant

PRINT_CARD = 0xFFFE   # scratch card used to pipe PRINT output

KEYWORDS = {
    "LET", "DIM", "IF", "THEN", "ELSEIF", "ELSE", "ENDIF", "WHILE", "ENDWHILE",
    "FOR", "TO", "STEP", "NEXT", "FOREACH", "IN", "ENDFOREACH",
    "SWITCH", "CASE", "DEFAULT", "ENDSWITCH", "GOTO", "GOSUB", "SUB",
    "DISPATCH", "ENDDISPATCH",
    "ENDSUB", "RETURN", "PRINT", "AND", "OR", "NOT",
    "DO", "LOOP", "UNTIL",
    "BREAK", "SKIP", "INC", "DEC", "IIF",
    "EQ", "NE", "LT", "GT", "LE", "GE", "MOD",
    "STORE", "GPIO", "LOAD", "SERVER", "ENDSERVER", "ASSERT",
    "PACK", "CARD", "FIFO", "DEVICE", "STREAM", "UI", "EVENT",
    "CONST", "ENUM", "ENDENUM",
    "ON", "END",
}
CMP_WORDS = {"EQ": "EQ", "NE": "NE", "LT": "LT", "GT": "GT", "LE": "LE", "GE": "GE"}

# Idiomatic aliases for the BASIC and (shared-lowerer) Python frontends -> canonical
# (ns, method). Pure frontend sugar: same IL/output on all paths. Keys are lowercase
# and cover both idioms (BASIC LEN/POKE/UCASE$ and Python len/poke/upper); a
# user-defined SUB of the same name takes precedence. Radix formatters follow each
# language's convention via BP_RADIX (Python hex/oct/bin -> 0x/0o/0b; BASIC HEX$ -> bare UPPERCASE).
BP_ALIASES = {
    "poke": ("Memory", "Set"), "peek": ("Memory", "Get"),
    "len": ("String", "Length"), "mid$": ("String", "Substring"),
    "ucase$": ("String", "ToUpper"), "lcase$": ("String", "ToLower"),
    "instr": ("String", "IndexOf"), "val": ("Number", "Parse"),
    "str$": ("Number", "ToString"), "abs": ("Number", "Abs"),
    "sqr": ("Maths", "Sqrt"), "oct$": ("Number", "ToOctal"), "bin$": ("Number", "ToBinary"),
    "span": ("Span", "Make"), "sha256": ("Crypto", "Sha256"),
    "min": ("Number", "Min"), "max": ("Number", "Max"),
    "str": ("Number", "ToString"), "int": ("Number", "Parse"),
    "pow": ("Maths", "Power"), "upper": ("String", "ToUpper"),
    "lower": ("String", "ToLower"), "find": ("String", "IndexOf"),
    "substr": ("String", "Substring"),
}
# Radix formatters needing composition: (canonical Number method, prefix or None, uppercase?)
BP_RADIX = {
    "hex": ("ToHex", "0x", False),     # Python hex(255) -> "0xff"
    "oct": ("ToOctal", "0o", False),   # Python oct(255) -> "0o377"
    "bin": ("ToBinary", "0b", False),  # Python bin(255) -> "0b11111111"
    "hex$": ("ToHex", None, True),     # BASIC HEX$(255) -> "FF" (bare uppercase)
}
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
    pos: int = -1      # INV-25: source byte offset of the token start


def tokenize(src: str) -> List[Tok]:
    toks: List[Tok] = []
    i, n, line = 0, len(src), 1
    while i < n:
        c = src[i]
        start = i                                       # INV-25: token start offset
        if c == "\n":
            toks.append(Tok("nl", "\\n", line, start)); line += 1; i += 1; continue
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
            toks.append(Tok("str", "".join(buf), line, start)); i = j + 1; continue
        if c.isdigit():
            j = i
            if c == "0" and j + 1 < n and src[j + 1] in "xX":
                j += 2
                while j < n and src[j] in "0123456789abcdefABCDEF":
                    j += 1
            else:
                while j < n and src[j].isdigit():
                    j += 1
            toks.append(Tok("num", src[i:j], line, start)); i = j; continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            if j < n and src[j] == "$":      # BASIC string-function suffix: HEX$, UCASE$, MID$, ...
                j += 1
            word = src[i:j]
            up = word.upper()
            toks.append(Tok("kw" if up in KEYWORDS else "id",
                            up if up in KEYWORDS else word, line, start))
            i = j; continue
        two = src[i:i + 2]
        if two in _TWO:
            toks.append(Tok("op", two, line, start)); i += 2; continue
        if c in _ONE:
            toks.append(Tok("op", c, line, start)); i += 1; continue
        raise SyntaxError(f"line {line}: unexpected char {c!r}")
    toks.append(Tok("nl", "\\n", line, n))
    toks.append(Tok("eof", "", line, n))
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
class Dispatch:
    expr: object; cases: list; default: Optional[list]   # jump-table switch (dense int cases)
@dataclass
class Goto:
    label: str
@dataclass
class Label:
    name: str
@dataclass
class Gosub:
    name: str; args: list = None      # args for parameterised calls (None = no-args compat)
@dataclass
class Sub:
    name: str; body: list; params: list = None   # params = parameter names (None = legacy)
@dataclass
class ServerMain:
    body: list                       # transparent server-entry wrapper; lowers body inline
@dataclass
class Return:
    value: object = None              # optional return expression
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
@dataclass
class TryExcept:
    try_body: list; except_body: list; finally_body: Optional[list] = None
@dataclass
class Raise:
    value: object = None
@dataclass
class OnBlock:
    event_ns: str; event_method: str; body: list   # ON Ns.Method: body END ON
@dataclass
class ConstDecl:
    name: str; value: object
@dataclass
class EnumDecl:
    enum_name: str; members: list   # members=[(name, value_expr_or_None), ...]


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
        # INV-25: stamp every statement with its first token's source offset.
        start = self.peek().pos
        node = self._parse_stmt()
        if node is not None:
            try:
                node.pos = start
            except (AttributeError, TypeError):
                pass
        return node

    def _parse_stmt(self) -> object:
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
            if kw == "CONST":
                return self.parse_const()
            if kw == "ENUM":
                return self.parse_enum()
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
            if kw == "DISPATCH":
                return self.parse_dispatch()
            if kw == "GOTO":
                self.next(); name = self.next().value; self.end_line(); return Goto(name)
            if kw == "GOSUB":
                self.next(); name = self.next().value
                args = None
                if self.peek().kind == "op" and self.peek().value == "(":
                    args = self.parse_args()
                self.end_line(); return Gosub(name, args)
            if kw == "SUB":
                return self.parse_sub()
            if kw == "SERVER":
                return self.parse_server()
            if kw == "ON":
                return self.parse_on_block()
            if kw == "RETURN":
                self.next()
                if self.peek().kind in ("nl", "eof"):
                    self.end_line(); return Return()
                v = self.parse_expr(); self.end_line(); return Return(v)
            if kw == "BREAK":
                self.next(); self.end_line(); return Break()
            if kw == "SKIP":
                self.next(); self.end_line(); return Skip()
            if kw == "PRINT":
                self.next(); v = self.parse_expr(); self.end_line(); return Print(v)
            if kw == "STORE":
                self.next(); call = self.parse_store_body(False); self.end_line(); return CallStmt(call)
            if kw == "LOAD":
                self.next(); call = self.parse_load_body(False); self.end_line(); return CallStmt(call)
            if kw == "GPIO":
                self.next(); call = self.parse_gpio_body(False); self.end_line(); return CallStmt(call)
            if kw == "ASSERT":
                # ASSERT <condition> -- PSUnit assertion, BASIC-idiomatic (no dotted
                # Assert.True call). The condition is any BASIC expression (=, <, >,
                # AND/OR), evaluated to 0/1 then recorded by the Assert.True hook.
                self.next(); cond = self.parse_expr(); self.end_line()
                return CallStmt(Call("Assert", "True", [cond]))
            if kw in ("PACK", "CARD", "FIFO", "DEVICE", "STREAM"):
                self.next()
                call = self._parse_caps_body(kw, False)
                self.end_line(); return CallStmt(call)
            if kw in ("UI", "EVENT"):
                self.next()
                call = self._parse_uievt_body(kw, False)
                self.end_line(); return CallStmt(call)
            raise SyntaxError(f"line {t.line}: unexpected keyword {kw}")
        # assignment: id = expr / id += expr   OR bare call: Ns.Method(...) / NAME(...)
        if t.kind == "id":
            nxt = self.peek2()
            if t.value.upper() == "POKE" and not (nxt.kind == "op" and nxt.value == "("):
                self.next()                              # classic no-parens form: POKE addr, value
                a = self.parse_expr(); self.eat_op(",")
                b = self.parse_expr(); self.end_line()
                return CallStmt(Call(None, "poke", [a, b]))
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
            if nxt.kind == "op" and nxt.value == "(":
                name = self.next().value                 # bare-name call statement: NAME(args)
                args = self.parse_args()
                self.end_line()
                return CallStmt(Call(None, name, args))
        raise SyntaxError(f"line {t.line}: cannot parse statement at {t.value!r}")

    def parse_let(self, eat_let: bool) -> Let:
        if eat_let:
            self.eat_kw("LET")
        name = self.next().value
        if self._peek_word() == "NEW":
            self._eat_word(); self._expect_word("CARD"); self.end_line()
            return Let(name, Call("Storage", "AddCard", []))
        self.eat_op("=")
        v = self.parse_expr()
        self.end_line()
        return Let(name, v)

    def parse_const(self) -> ConstDecl:
        self.eat_kw("CONST")
        name = self.next().value
        self.eat_op("=")
        value = self.parse_expr()
        self.end_line()
        return ConstDecl(name, value)

    def parse_enum(self) -> EnumDecl:
        self.eat_kw("ENUM")
        enum_name = self.next().value
        self.end_line()
        members = []
        self.skip_nl()
        while not self.at_kw("ENDENUM"):
            t = self.peek()
            if t.kind == "eof":
                raise SyntaxError("unexpected EOF; expected ENDENUM")
            if t.kind not in ("id", "kw"):
                raise SyntaxError(f"line {t.line}: expected enum member name, got {t.value!r}")
            member_name = self.next().value
            member_value = None
            if self.peek().kind == "op" and self.peek().value == "=":
                self.next()
                member_value = self.parse_expr()
            self.end_line()
            members.append((member_name, member_value))
            self.skip_nl()
        self.eat_kw("ENDENUM")
        self.end_line()
        return EnumDecl(enum_name, members)

    def parse_dim(self) -> Dim:
        self.eat_kw("DIM")
        name = self.next().value
        init = None
        if self.peek().kind == "op" and self.peek().value == "=":
            self.next()
            init = self.parse_expr()
        elif self._peek_word() == "NEW":
            self._eat_word(); self._expect_word("CARD")
            init = Call("Storage", "AddCard", [])
        self.end_line()
        return Dim(name, init)

    # ── readable storage / device DSL (BASIC-idiomatic; lowers to canonical
    #    Storage.*/Gpio.* calls so bytecode is byte-identical with vm/picoc.js).
    #    STORE = writes, LOAD = reads, GPIO = pins. Sub-words (USE/PACK/CARD/DIR/
    #    UP/OUT/...) are matched contextually so they stay usable as identifiers. ─
    def _peek_word(self) -> Optional[str]:
        t = self.peek()
        if t.kind == "id":
            return t.value.upper()
        if t.kind == "kw":
            return t.value
        return None

    def _eat_word(self) -> str:
        t = self.next()
        if t.kind not in ("id", "kw"):
            raise SyntaxError(f"line {t.line}: expected a word, got {t.value!r}")
        return t.value.upper()

    def _expect_word(self, expected: str) -> None:
        w = self._eat_word()
        if w != expected:
            raise SyntaxError(f"line {self.peek().line}: expected {expected!r}, got {w!r}")

    def parse_store_body(self, want_value: bool) -> Call:
        verb = self._eat_word()
        if want_value and verb != "NEW":
            raise SyntaxError(f"line {self.peek().line}: STORE {verb} is a statement, not a value")
        if verb == "USE":
            self._expect_word("PACK")
            return Call("Storage", "UsePack", [self.parse_atom()])
        if verb == "SET":
            if self._peek_word() == "PACK":
                self._eat_word()
                return Call("Storage", "UsePack", [self.parse_atom()])
            field = self.parse_atom()
            self.eat_op("=")
            rhs = self.parse_expr()
            method = "SetFieldStr" if isinstance(rhs, Str) else "SetField"
            return Call("Storage", method, [field, rhs])
        if verb == "DELETE":
            self._expect_word("CARD")
            return Call("Storage", "DeleteCard", [self.parse_atom()])
        if verb == "NEW":
            self._expect_word("CARD")
            return Call("Storage", "AddCard", [])
        raise SyntaxError(f"line {self.peek().line}: unknown STORE verb {verb!r}")

    def parse_load_body(self, want_value: bool) -> Call:
        w = self._peek_word()
        if w == "CARD":
            self._eat_word()
            return Call("Storage", "EditCard", [self.parse_atom()])
        if w == "QUERY":
            self._eat_word()
            return Call("Storage", "QueryCard", [self.parse_atom()])
        if w == "RESULT":
            self._eat_word()
            return Call("Storage", "QueryResult", [self.parse_atom()])
        field = self.parse_atom()
        if self._peek_word() == "AS":
            self._eat_word(); self._expect_word("TEXT")
            return Call("Storage", "GetFieldStr", [field])
        return Call("Storage", "GetField", [field])

    def parse_gpio_body(self, want_value: bool) -> Call:
        verb = self._eat_word()
        if verb == "COUNT":
            return Call("Gpio", "Count", [])
        if verb == "READ":
            return Call("Gpio", "Read", [self.parse_atom()])
        if verb == "WRITE":
            if want_value:
                raise SyntaxError(f"line {self.peek().line}: GPIO WRITE is a statement, not a value")
            pin = self.parse_atom(); self.eat_op("="); val = self.parse_expr()
            return Call("Gpio", "Write", [pin, val])
        if verb in ("DIR", "PULL"):
            pin = self.parse_atom()
            if (not want_value) and self.peek().kind == "op" and self.peek().value == "=":
                self.eat_op("=")
                rhs = self._parse_dir_value() if verb == "DIR" else self._parse_pull_value()
                return Call("Gpio", "SetDir" if verb == "DIR" else "SetPull", [pin, rhs])
            return Call("Gpio", "GetDir" if verb == "DIR" else "GetPull", [pin])
        raise SyntaxError(f"line {self.peek().line}: unknown GPIO verb {verb!r}")

    def _parse_dir_value(self) -> object:
        t = self.peek()
        if t.kind == "kw" and t.value == "IN":
            self.next(); return Num(0)
        if t.kind == "id" and t.value.upper() in ("OUT", "OUTPUT"):
            self.next(); return Num(1)
        if t.kind == "id" and t.value.upper() == "INPUT":
            self.next(); return Num(0)
        return self.parse_expr()

    # ── capsule / device / stream DSL (BASIC-idiomatic; lowers to the canonical
    #    Pack.*/Card.*/Fifo.*/Device.*/Stream.* hooks). Verb-first like STORE/GPIO;
    #    sub-words (USE/READ/WRITE/OPEN/SEND/RECV/NEXT/...) stay contextual ids. ──
    def _parse_caps_body(self, head: str, want_value: bool) -> Call:
        if head == "PACK":
            self._expect_word("USE")
            return Call("Pack", "Use", [self.parse_atom()])
        if head == "CARD":
            verb = self._eat_word()
            if verb == "READ":
                return Call("Card", "Read", [self.parse_atom()])
            if verb == "ADDRESS":
                pk = self.parse_atom(); cd = self.parse_atom()
                return Call("Card", "Address", [pk, cd])
            if verb == "WRITE":
                self._need_stmt(want_value, "CARD WRITE")
                cd = self.parse_atom(); self.eat_op("="); val = self.parse_expr()
                return Call("Card", "Write", [cd, val])
            raise SyntaxError(f"line {self.peek().line}: unknown CARD verb {verb!r}")
        if head == "FIFO":
            verb = self._eat_word()
            if verb == "OPEN":
                return Call("Fifo", "Open", [self.parse_atom()])
            if verb == "RECV":
                return Call("Fifo", "Recv", [self.parse_atom()])
            if verb == "POLL":
                return Call("Fifo", "Poll", [self.parse_atom()])
            if verb == "SEND":
                self._need_stmt(want_value, "FIFO SEND")
                fh = self.parse_atom(); self.eat_op("="); val = self.parse_expr()
                return Call("Fifo", "Send", [fh, val])
            raise SyntaxError(f"line {self.peek().line}: unknown FIFO verb {verb!r}")
        if head == "DEVICE":
            verb = self._eat_word()
            if verb == "OPEN":
                ident = self.parse_atom()
                cfg = Num(0)
                if self._peek_word() == "CONFIG":
                    self._eat_word(); cfg = self.parse_atom()
                return Call("Device", "Open", [ident, cfg])
            if verb == "CAPS":
                return Call("Device", "Caps", [self.parse_atom()])
            if verb == "STATUS":
                return Call("Device", "Status", [self.parse_atom()])
            if verb == "CLOSE":
                self._need_stmt(want_value, "DEVICE CLOSE")
                return Call("Device", "Close", [self.parse_atom()])
            raise SyntaxError(f"line {self.peek().line}: unknown DEVICE verb {verb!r}")
        if head == "STREAM":
            verb = self._eat_word()
            if verb == "OPEN":
                dev = self.parse_atom(); cfg = self.parse_atom()
                return Call("Stream", "Open", [dev, cfg])
            if verb == "NEXT":
                return Call("Stream", "Next", [self.parse_atom()])
            if verb == "SPAN":
                return Call("Stream", "Span", [self.parse_atom()])
            if verb == "SETSLICE":
                self._need_stmt(want_value, "STREAM SETSLICE")
                off = self.parse_atom()
                if self.peek().kind == "op" and self.peek().value == ",":
                    self.next()
                ln = self.parse_atom()
                return Call("Stream", "SetSlice", [off, ln])
            if verb == "SLICE":
                return Call("Stream", "Slice", [self.parse_atom()])
            if verb == "SUBMIT":
                self._need_stmt(want_value, "STREAM SUBMIT")
                st = self.parse_atom(); self.eat_op("="); le = self.parse_expr()
                return Call("Stream", "Submit", [st, le])
            if verb == "RELEASE":
                self._need_stmt(want_value, "STREAM RELEASE")
                return Call("Stream", "Release", [self.parse_atom()])
            if verb == "CLOSE":
                self._need_stmt(want_value, "STREAM CLOSE")
                return Call("Stream", "Close", [self.parse_atom()])
            raise SyntaxError(f"line {self.peek().line}: unknown STREAM verb {verb!r}")
        raise SyntaxError(f"line {self.peek().line}: unknown DSL head {head!r}")

    def _need_stmt(self, want_value: bool, what: str) -> None:
        if want_value:
            raise SyntaxError(f"line {self.peek().line}: {what} is a statement, not a value")

    # ── UI / Event DSL (BASIC-idiomatic; lowers to the canonical Ui.*/Event.*
    #    hooks). Build a window + controls and pump events without dotted calls. ──
    def _parse_uievt_body(self, head: str, want_value: bool) -> Call:
        if head == "EVENT":
            verb = self._eat_word()
            if verb == "POST":
                ty = self.parse_atom(); tg = self.parse_atom()
                return Call("Event", "Post", [ty, tg])
            if verb == "NEXT":
                return Call("Event", "Next", [])
            if verb == "TYPE":
                return Call("Event", "Type", [self.parse_atom()])
            if verb == "TARGET":
                return Call("Event", "Target", [self.parse_atom()])
            if verb == "DATA":
                return Call("Event", "Data", [self.parse_atom()])
            if verb == "DATALEN":
                return Call("Event", "DataLen", [self.parse_atom()])
            if verb == "DATASLICE":
                return Call("Event", "DataSlice", [self.parse_atom()])
            if verb == "COUNT":
                return Call("Event", "Count", [])
            if verb == "SETSLICE":
                self._need_stmt(want_value, "EVENT SETSLICE")
                off = self.parse_atom()
                if self.peek().kind == "op" and self.peek().value == ",":
                    self.next()
                ln = self.parse_atom()
                return Call("Event", "SetSlice", [off, ln])
            if verb == "SETDATA":
                self._need_stmt(want_value, "EVENT SETDATA")
                ev = self.parse_atom(); self.eat_op("="); sp = self.parse_expr()
                return Call("Event", "SetData", [ev, sp])
            raise SyntaxError(f"line {self.peek().line}: unknown EVENT verb {verb!r}")
        if head == "UI":
            verb = self._eat_word()
            if verb == "WINDOW":
                return Call("Ui", "Window", [self.parse_atom()])
            if verb == "PANEL":
                return Call("Ui", "Panel", [self.parse_atom()])
            if verb in ("LABEL", "BUTTON", "TEXTBOX", "CHECKBOX"):
                parent = self.parse_atom(); text = self.parse_atom()
                method = {"LABEL": "Label", "BUTTON": "Button",
                          "TEXTBOX": "TextBox", "CHECKBOX": "Checkbox"}[verb]
                return Call("Ui", method, [parent, text])
            if verb in ("POS", "SIZE"):
                self._need_stmt(want_value, f"UI {verb}")
                node = self.parse_atom(); self.eat_op("=")
                x = self.parse_expr()
                if self.peek().kind == "op" and self.peek().value == ",":
                    self.next(); y = self.parse_expr()
                    val = Bin("+", Bin("*", x, Num(65536)), y)   # UI POS n = x, y -> (x<<16)|y
                else:
                    val = x
                return Call("Ui", "Pos" if verb == "POS" else "Size", [node, val])
            if verb in ("SETTEXT", "SETID", "SETVALUE"):
                self._need_stmt(want_value, f"UI {verb}")
                node = self.parse_atom(); self.eat_op("="); v = self.parse_expr()
                method = {"SETTEXT": "SetText", "SETID": "SetId", "SETVALUE": "SetValue"}[verb]
                return Call("Ui", method, [node, v])
            if verb == "SERIALIZE":
                return Call("Ui", "Serialize", [self.parse_atom()])
            raise SyntaxError(f"line {self.peek().line}: unknown UI verb {verb!r}")
        raise SyntaxError(f"line {self.peek().line}: unknown DSL head {head!r}")

    def _parse_pull_value(self) -> object:
        t = self.peek()
        if t.kind == "id":
            w = t.value.upper()
            if w == "NONE":
                self.next(); return Num(0)
            if w == "UP":
                self.next(); return Num(1)
            if w == "DOWN":
                self.next(); return Num(2)
        return self.parse_expr()

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

    def parse_dispatch(self) -> Dispatch:
        """DISPATCH expr / CASE n / DEFAULT / ENDDISPATCH -- a jump-table switch over
        dense non-negative integer cases (compiles to an indexed jump)."""
        self.eat_kw("DISPATCH")
        expr = self.parse_expr()
        self.end_line()
        self.skip_nl()
        cases = []
        default = None
        while not self.at_kw("ENDDISPATCH"):
            if self.at_kw("CASE"):
                self.eat_kw("CASE")
                val = self.parse_expr()
                self.end_line()
                body = self.parse_block("CASE", "DEFAULT", "ENDDISPATCH")
                cases.append((val, body))
            elif self.at_kw("DEFAULT"):
                self.eat_kw("DEFAULT"); self.end_line()
                default = self.parse_block("ENDDISPATCH")
            else:
                raise SyntaxError(f"line {self.peek().line}: expected CASE/DEFAULT/ENDDISPATCH")
        self.eat_kw("ENDDISPATCH"); self.end_line()
        return Dispatch(expr, cases, default)

    def parse_sub(self) -> Sub:
        self.eat_kw("SUB")
        name = self.next().value
        params = None
        if self.peek().kind == "op" and self.peek().value == "(":
            self.next()  # eat (
            params = []
            if not (self.peek().kind == "op" and self.peek().value == ")"):
                params.append(self.next().value)
                while self.peek().kind == "op" and self.peek().value == ",":
                    self.next(); params.append(self.next().value)
            self.eat_op(")")
        self.end_line()
        body = self.parse_block("ENDSUB")
        self.eat_kw("ENDSUB"); self.end_line()
        return Sub(name, body, params)

    def parse_server(self) -> ServerMain:
        self.eat_kw("SERVER")
        self.end_line()
        body = self.parse_block("ENDSERVER")
        self.eat_kw("ENDSERVER"); self.end_line()
        return ServerMain(body)

    def parse_on_block(self) -> OnBlock:
        """ON Ns.Method: ... END ON"""
        self.eat_kw("ON")
        ns = self.next().value
        self.eat_op(".")
        method = self.next().value
        self.end_line()
        body = self.parse_block("END")
        self.eat_kw("END"); self.eat_kw("ON"); self.end_line()
        return OnBlock(ns, method, body)

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
        if t.kind == "kw" and t.value == "STORE":
            return self.parse_store_body(True)
        if t.kind == "kw" and t.value == "LOAD":
            return self.parse_load_body(True)
        if t.kind == "kw" and t.value == "GPIO":
            return self.parse_gpio_body(True)
        if t.kind == "kw" and t.value in ("PACK", "CARD", "FIFO", "DEVICE", "STREAM"):
            return self._parse_caps_body(t.value, True)
        if t.kind == "kw" and t.value in ("UI", "EVENT"):
            return self._parse_uievt_body(t.value, True)
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
            if self.peek().kind == "op" and self.peek().value == "(":
                args = self.parse_args()                 # bare-name call: LEN(x), HEX$(n), PEEK(a)
                return Call(None, t.value, args)
            return Var(t.value)
        raise SyntaxError(f"line {t.line}: unexpected token {t.value!r}")


# ── lowering to PicoIL ──────────────────────────────────────────────────────

_ARITH = {"+": "add", "-": "sub", "*": "mul", "/": "div"}


class Lowerer:
    def __init__(self):
        self.b = ILBuilder()
        self.vars: Dict[str, VReg] = {}
        self.subs: List[Sub] = []
        self.user_constants: Dict[str, int] = {}
        # Stack of (continue_label_or_None, break_label) for BREAK/SKIP.
        # Loops push a continue label; SWITCH pushes None (breakable, not skippable).
        self.scopes: List[tuple] = []
        # String-literal constant pool (see picoscript_cfront): each distinct literal
        # interned to its own stable address growing down from 0x8000, so any number
        # can be live at once (replaces the old 2-alternating-slot scheme).
        self._strpool: Dict[bytes, int] = {}
        self._strpool_top = 0x8000

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
        self._sub_names = {s.name.lower() for s in self.subs}
        self._sub_params = {s.name.lower(): (s.params or []) for s in self.subs}
        for s in body:
            self.stmt(s)
        self.b.ret()
        for sub in self.subs:
            self.b.label(f"sub_{sub.name.upper()}")
            for i, p in enumerate(sub.params or []):
                pv = self.var(p)
                av = self.var(f"__arg{i}__")
                self.b.mov(pv, av)
            for s in sub.body:
                self.stmt(s)
            self.b.ret()
        return self.b.insts

    def stmt(self, s):
        p = getattr(s, "pos", -1)
        if p is not None and p >= 0:
            self.b.cur_pos = p           # INV-25: attribute emitted IL to this statement
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
        elif isinstance(s, ConstDecl):
            self._define_constant(s.name, s.value)
        elif isinstance(s, EnumDecl):
            self._define_enum(s.enum_name, s.members)
        elif isinstance(s, Label):
            self.b.label(f"lbl_{s.name.upper()}")
        elif isinstance(s, Goto):
            self.b.jmp(f"lbl_{s.label.upper()}")
        elif isinstance(s, Gosub):
            if s.args:
                # Evaluate each argument into a fresh temp first, then stage the
                # temps into the shared __arg slots immediately before the call.
                # The temps span any nested calls in later arguments, so the
                # allocator pins them (il spans_call) and they survive -- whereas
                # writing __arg{i}__ eagerly would be clobbered by those calls.
                tmps = []
                for arg in s.args:
                    t = self.b.vreg(); self.b.mov(t, self.eval(arg)); tmps.append(t)
                for i, t in enumerate(tmps):
                    self.b.mov(self.var(f"__arg{i}__"), t)
            self.b.call(f"sub_{s.name.upper()}")
        elif isinstance(s, Return):
            if s.value is not None:
                rv = self.eval(s.value)
                self.b.mov(self.var("__ret__"), rv)
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
        elif isinstance(s, Dispatch):
            self.lower_dispatch(s)
        elif isinstance(s, Print):
            self.lower_print(s)
        elif isinstance(s, CallStmt):
            self.lower_call(s.call, want_value=False)
        elif isinstance(s, ServerMain):
            for st in s.body:
                self.stmt(st)
        elif isinstance(s, TryExcept):
            self.lower_try(s)
        elif isinstance(s, Raise):
            if s.value is not None:
                v = self.eval(s.value)
                self.b.host("Error", "SetHandler", (v,), None)  # raise uses RAISE opcode
            self.b.raise_sw(0)
        elif isinstance(s, OnBlock):
            self.lower_on_block(s)
        else:
            raise SyntaxError(f"cannot lower {s}")

    def _resolve_constant(self, name: str):
        key = str(name).strip().upper()
        if key in self.user_constants:
            return self.user_constants[key]
        return resolve_named_constant(name)

    def _eval_const_expr(self, expr) -> int:
        if isinstance(expr, Num):
            return int(expr.value)
        if isinstance(expr, Var):
            cv = self._resolve_constant(expr.name)
            if cv is None:
                raise SyntaxError(f"unknown constant {expr.name!r} in constant expression")
            return int(cv)
        if isinstance(expr, Bin):
            a = self._eval_const_expr(expr.lhs)
            b = self._eval_const_expr(expr.rhs)
            if expr.op == "+":
                return a + b
            if expr.op == "-":
                return a - b
            if expr.op == "*":
                return a * b
            if expr.op == "/":
                if b == 0:
                    raise SyntaxError("division by zero in constant expression")
                return int(a / b)
            if expr.op == "MOD":
                if b == 0:
                    raise SyntaxError("modulo by zero in constant expression")
                return a - int(a / b) * b
        raise SyntaxError(f"unsupported constant expression {type(expr).__name__}")

    def _define_constant(self, name: str, value_expr):
        self.user_constants[str(name).strip().upper()] = int(self._eval_const_expr(value_expr))

    def _define_enum(self, enum_name: str, members):
        enum_key = str(enum_name).strip().upper()
        cur = -1
        for member_name, value_expr in members:
            if value_expr is None:
                cur += 1
            else:
                cur = int(self._eval_const_expr(value_expr))
            member_key = str(member_name).strip().upper()
            self.user_constants[member_key] = cur
            self.user_constants[f"{enum_key}_{member_key}"] = cur
            self.user_constants[f"{enum_key}.{member_key}"] = cur

    def lower_try(self, s: TryExcept):
        """try/except/finally -> Error.SetHandler + conditional check pattern.
        Phase 1: simple fault-flag checking (no label addresses needed).
        The except block runs if any host call in the try body faults."""
        handler_label = self.b.new_label("except")
        end_label = self.b.new_label("endtry")
        # try body: execute normally
        for st in s.try_body:
            self.stmt(st)
        # check if error occurred (Status.Last != 0)
        status = self.b.vreg()
        self.b.host("Error", "Code", (), status)
        self.b.cmpbr("NZ", status, status, handler_label)
        if s.finally_body:
            for st in s.finally_body:
                self.stmt(st)
        self.b.jmp(end_label)
        # except body
        self.b.label(handler_label)
        for st in s.except_body:
            self.stmt(st)
        clear = self.b.vreg()
        self.b.host("Error", "Clear", (), clear)
        if s.finally_body:
            for st in s.finally_body:
                self.stmt(st)
        self.b.label(end_label)

    def lower_on_block(self, s: OnBlock):
        """ON Ns.Method: body END ON → register handler + labelled sub."""
        handler_label = f"__on_{s.event_ns.lower()}_{s.event_method.lower()}"
        # Skip handler body during normal execution
        end_label = self.b.new_label("endon")
        self.b.jmp(end_label)
        # Emit handler as a labelled sub
        self.b.label(handler_label)
        for st in s.body:
            self.stmt(st)
        self.b.ret()
        self.b.label(end_label)
        # Register: Net.Register(event_constant, handler) — the host binds it
        self.b.host(s.event_ns, "Register", (), None)

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

    def lower_dispatch(self, s: Dispatch):
        """Lower DISPATCH to a bounds-checked jump table: guard the selector into
        [0, N), then an indexed jump (jmptab) to the matching case (or default).
        Cases do NOT fall through -- each is independent, like a state handler."""
        sel = self.eval(s.expr)
        end = self.b.new_label("enddisp")
        default_l = self.b.new_label("dispdef")
        pairs = []
        for (val, body) in s.cases:
            if not isinstance(val, Num) or val.value < 0:
                raise SyntaxError("DISPATCH case must be a constant non-negative integer")
            pairs.append((val.value, body))
        n = max((v for v, _ in pairs), default=0) + 1
        table = [default_l] * n
        bodies = []
        for v, body in pairs:
            lbl = self.b.new_label("dcase")
            table[v] = lbl
            bodies.append((lbl, body))
        nreg = self.b.vreg(); self.b.const(nreg, n)
        self.b.cmpbr("GE", sel, nreg, default_l)     # selector >= N -> default
        zreg = self.b.vreg(); self.b.const(zreg, 0)
        self.b.cmpbr("LT", sel, zreg, default_l)     # selector < 0  -> default
        self.b.jmptab(sel, tuple(table), default_l)
        self.scopes.append((None, end))              # breakable (BREAK), not skippable
        for lbl, body in bodies:
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
        if isinstance(s.value, Str):
            self.b.host("Io", "Write", (self.emit_str_span(s.value.value),), None)
            return
        v = self.eval(s.value)
        self.b.save(v, PRINT_CARD)
        self.b.pipe(v, PRINT_CARD)

    def emit_str_span(self, text: str) -> VReg:
        """Materialize a string literal as a span over its interned constant-pool
        bytes: identical literals share one stable address (dedup), distinct ones
        never overlap, so any number can be live at once (bytes are rewritten at the
        literal's fixed address before each span, correct under branches/loops)."""
        data = text.encode("utf-8")
        if data not in self._strpool:
            self._strpool_top -= len(data)
            self._strpool[data] = self._strpool_top
        base = self._strpool[data]
        areg = self.b.vreg(); vreg = self.b.vreg()
        for i, byte in enumerate(data):
            self.b.const(areg, base + i)
            self.b.const(vreg, byte)
            self.b.host("Memory", "SetConst", (areg, vreg), None)
        self.b.const(areg, base)
        self.b.const(vreg, len(data))
        span = self.b.vreg()
        self.b.host("Span", "Make", (areg, vreg), span)
        return span

    # -- expressions -----------------------------------------------------
    def eval(self, e) -> VReg:
        if isinstance(e, Num):
            v = self.b.vreg(); self.b.const(v, e.value); return v
        if isinstance(e, Var):
            cv = self._resolve_constant(e.name)
            if cv is not None:
                v = self.b.vreg(); self.b.const(v, cv); return v
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
            return self.emit_str_span(e.value)
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
        if ns is None:
            key = method.lower()
            subs = getattr(self, "_sub_names", set())
            # Local subroutine call (takes priority if name matches a SUB)
            if key in subs:
                params = self._sub_params.get(key, [])
                # Stage args through fresh temps (see Gosub note): a nested call
                # in a later argument must not clobber an earlier __arg slot.
                tmps = []
                for arg in c.args:
                    t = self.b.vreg(); self.b.mov(t, self.eval(arg)); tmps.append(t)
                for i, t in enumerate(tmps):
                    self.b.mov(self.var(f"__arg{i}__"), t)
                self.b.call(f"sub_{method.upper()}")
                if want_value:
                    # Copy the shared __ret__ slot into a fresh temp so callers
                    # can compose results (e.g. f() + g()) without the second
                    # call overwriting the first's return value.
                    rt = self.b.vreg(); self.b.mov(rt, self.var("__ret__"))
                    return rt
                return None
            if key in BP_RADIX and key not in subs:
                cm, prefix, upper = BP_RADIX[key]
                val = self.eval(c.args[0])
                d = self.b.vreg(); self.b.host("Number", cm, (val,), d)
                if upper:
                    out = self.b.vreg(); self.b.host("String", "ToUpper", (d,), out); return out
                if prefix:
                    pre = self.emit_str_span(prefix)
                    out = self.b.vreg(); self.b.host("String", "Concat", (pre, d), out); return out
                return d
            if key in BP_ALIASES and key not in subs:
                a_ns, a_m = BP_ALIASES[key]
                return self.lower_call(Call(a_ns, a_m, c.args), want_value)
        if ns is not None and ns.upper() == "NET":
            m = method.upper()
            if m == "STATUS":
                self.b.net("status", self._eval_const_expr(c.args[0]))
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
