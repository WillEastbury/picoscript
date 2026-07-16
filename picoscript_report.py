#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_report.py -- an ABAP/4GL-style PicoScript frontend.

Period-terminated, case-insensitive REPORT syntax that reuses the BASIC AST
nodes and `Lowerer` unchanged. Supports DATA declarations, CONSTANTS blocks,
ENUM/ENDENUM, assignment/MOVE/COMPUTE, ADD/SUBTRACT/MULTIPLY/DIVIDE, WRITE,
IF/ELSEIF/ELSE/ENDIF, LOOP/DO blocks, CASE/WHEN/OTHERS, DISPATCH/WHEN/OTHERS/
ENDDISPATCH, FORM/PERFORM, TRY/CATCH/CLEANUP/ENDTRY, RAISE, ON/ENDON,
RETURN/EXIT/CONTINUE, and Ns.Method(args). `*` starts a full-line comment; `"`
starts an inline comment.
"""

from typing import List, Optional, Set

from picoscript_basic import (  # reuse AST + lowering unchanged
    Num, Str, Var, Bin, Cmp, Call, Let, IncDec, Ternary, If, While, DoLoop, ForTo, ForEach,
    Switch, Goto, Label, Sub, Gosub, Return, Break, Skip, Print, CallStmt, Lowerer,
    Dispatch, TryExcept, Raise, OnBlock, ConstDecl, EnumDecl,
)
KEYWORDS = {
    "DATA", "TYPE", "VALUE",
    "CONSTANTS", "ENUM", "ENDENUM",
    "IF", "ELSE", "ELSEIF", "ENDIF",
    "LOOP", "AT", "INTO", "WHERE", "ENDLOOP",
    "DO", "TIMES", "ENDDO",
    "CASE", "WHEN", "OTHERS", "ENDCASE",
    "DISPATCH", "ENDDISPATCH",
    "FORM", "ENDFORM", "USING", "PERFORM",
    "WRITE", "MOVE", "TO", "COMPUTE",
    "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "BY", "FROM", "GIVING",
    "TRY", "CATCH", "CLEANUP", "ENDTRY", "RAISE",
    "ON", "ENDON",
    "RETURN", "EXIT", "CONTINUE",
    "AND", "OR", "NOT",
    "EQ", "NE", "LT", "GT", "LE", "GE",
}
_CMP = {
    "=": "EQ", "==": "EQ",
    "<>": "NE", "!=": "NE",
    "<": "LT", ">": "GT", "<=": "LE", ">=": "GE",
    "EQ": "EQ", "NE": "NE", "LT": "LT", "GT": "GT", "LE": "LE", "GE": "GE",
}
_PREC = {
    "OR": 1,
    "AND": 2,
    "=": 3, "==": 3, "<>": 3, "!=": 3, "<": 3, ">": 3, "<=": 3, ">=": 3,
    "EQ": 3, "NE": 3, "LT": 3, "GT": 3, "LE": 3, "GE": 3,
    "+": 5, "-": 5,
    "*": 6, "/": 6,
}
_BINOP = {"+": "+", "-": "-", "*": "*", "/": "/", "AND": "AND", "OR": "OR"}
_TWO = {"<=", ">=", "<>", "!=", "=="}
_ONE = set("+-*/()<>=,.:")

class Tok:
    __slots__ = ("kind", "value", "line", "pos")
    def __init__(self, kind, value, line, pos=-1):
        self.kind = kind      # num,id,kw,str,op,eof
        self.value = value
        self.line = line
        self.pos = pos
    def __repr__(self):
        return f"Tok({self.kind},{self.value!r})"

def _tokenize_line(text: str, lineno: int, out: List[Tok], line_start: int = 0):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        start = line_start + i
        if c in " \t":
            i += 1
            continue
        if c == '"':
            break
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
            up = word.upper()
            out.append(Tok("kw" if up in KEYWORDS else "id",
                           up if up in KEYWORDS else word, lineno, start))
            i = j
            continue
        if c == "'":
            j = i + 1
            buf = []
            while j < n:
                ch = text[j]
                if ch == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(ch)
                j += 1
            if j >= n or text[j] != "'":
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
    raw_lines = src.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    offset = 0
    for idx, line in enumerate(raw_lines):
        lineno = idx + 1
        line_start = offset
        offset += len(line) + 1
        stripped = line.lstrip(" \t")
        if stripped == "" or stripped.startswith("*"):
            continue
        _tokenize_line(line, lineno, out, line_start)
    out.append(Tok("eof", "", len(raw_lines), offset))
    return out

class Parser:
    def __init__(self, toks: List[Tok]):
        self.toks = toks
        self.i = 0
        self.temp_index = 0
    def peek(self, k: int = 0) -> Tok:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else self.toks[-1]
    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t
    def at(self, kind, value=None) -> bool:
        t = self.peek()
        return t.kind == kind and (value is None or t.value == value)
    def at_kw(self, *names) -> bool:
        t = self.peek()
        return t.kind == "kw" and t.value in names
    def expect(self, kind, value=None) -> Tok:
        t = self.next()
        if t.kind != kind or (value is not None and t.value != value):
            want = value if value is not None else kind
            raise SyntaxError(f"line {t.line}: expected {want!r}, got {t.value!r} ({t.kind})")
        return t
    def expect_kw(self, name: str) -> Tok:
        t = self.next()
        if not (t.kind == "kw" and t.value == name):
            raise SyntaxError(f"line {t.line}: expected {name}, got {t.value!r}")
        return t
    def end_stmt(self):
        self.expect("op", ".")
    def fresh_temp(self, prefix: str = "__do__") -> str:
        self.temp_index += 1
        return f"{prefix}{self.temp_index}"
    def parse_program(self) -> List[object]:
        stmts = []
        while self.peek().kind != "eof":
            if self.at("op", "."):
                self.next()
                continue
            s = self.parse_stmt()
            if s is None:  # pragma: no cover — _parse_stmt never returns None
                continue
            if isinstance(s, list):
                stmts.extend(s)
            else:
                stmts.append(s)
        return stmts
    def parse_block_until(self, stop_words: Set[str]) -> List[object]:
        stmts = []
        while self.peek().kind != "eof":  # pragma: no branch
            if self.peek().kind == "kw" and self.peek().value in stop_words:
                break
            if self.at("op", "."):
                self.next()
                continue
            s = self.parse_stmt()
            if s is None:  # pragma: no cover — _parse_stmt never returns None
                continue
            if isinstance(s, list):
                stmts.extend(s)
            else:
                stmts.append(s)
        return stmts
    def parse_stmt(self) -> Optional[object]:
        start = self.peek().pos
        node = self._parse_stmt()
        if node is not None:  # pragma: no branch — _parse_stmt always returns a node or raises
            targets = node if isinstance(node, list) else [node]
            for item in targets:
                try:
                    item.pos = start
                except (AttributeError, TypeError):  # pragma: no cover — AST nodes always have .pos
                    pass
        return node
    def _parse_stmt(self) -> Optional[object]:
        t = self.peek()
        if t.kind == "kw":
            kw = t.value
            if kw == "DATA":
                return self.parse_data()
            if kw == "CONSTANTS":
                return self.parse_constants()
            if kw == "ENUM":
                return self.parse_enum()
            if kw == "IF":
                return self.parse_if()
            if kw == "LOOP":
                return self.parse_loop()
            if kw == "DO":
                return self.parse_do()
            if kw == "CASE":
                return self.parse_case()
            if kw == "DISPATCH":
                return self.parse_dispatch()
            if kw == "FORM":
                return self.parse_form()
            if kw == "PERFORM":
                return self.parse_perform()
            if kw == "TRY":
                return self.parse_try()
            if kw == "RAISE":
                return self.parse_raise()
            if kw == "ON":
                return self.parse_on()
            if kw == "WRITE":
                self.next()
                value = self.parse_expr()
                self.end_stmt()
                return Print(value)
            if kw == "MOVE":
                return self.parse_move()
            if kw == "COMPUTE":
                return self.parse_compute()
            if kw == "ADD":
                return self.parse_add()
            if kw == "SUBTRACT":
                return self.parse_subtract()
            if kw == "MULTIPLY":
                return self.parse_multiply()
            if kw == "DIVIDE":
                return self.parse_divide()
            if kw == "RETURN":
                self.next()
                if self.at("op", "."):
                    self.end_stmt()
                    return Return()
                value = self.parse_expr()
                self.end_stmt()
                return Return(value)
            if kw == "EXIT":
                self.next()
                self.end_stmt()
                return Break()
            if kw == "CONTINUE":
                self.next()
                self.end_stmt()
                return Skip()
            raise SyntaxError(f"line {t.line}: unexpected keyword {t.value!r}")
        if t.kind == "id":
            if self.peek(1).kind == "op" and self.peek(1).value == "=":
                name = self.next().value
                self.next()
                value = self.parse_expr()
                self.end_stmt()
                return Let(name, value)
            if (self.peek(1).kind == "op" and self.peek(1).value == "."
                    and self.peek(2).kind in ("id", "kw")
                    and self.peek(3).kind == "op" and self.peek(3).value == "("):
                call = self.parse_call_from_id()
                self.end_stmt()
                return CallStmt(call)
        raise SyntaxError(f"line {t.line}: cannot parse statement at {t.value!r}")
    def parse_data(self) -> List[Let]:
        self.expect_kw("DATA")
        if self.at("op", ":"):
            self.next()
        decls = []
        while True:
            name = self.expect("id").value
            init = Num(0)
            while self.peek().kind == "kw" and self.peek().value in ("TYPE", "VALUE"):
                if self.at_kw("TYPE"):
                    self.next()
                    t = self.next()
                    if t.kind not in ("id", "kw"):
                        raise SyntaxError(f"line {t.line}: expected type name, got {t.value!r}")
                elif self.at_kw("VALUE"):  # pragma: no branch
                    self.next()
                    init = self.parse_expr()
            decls.append(Let(name, init))
            if self.at("op", ","):
                self.next()
                continue
            self.end_stmt()
            return decls
    def parse_constants(self) -> List[ConstDecl]:
        self.expect_kw("CONSTANTS")
        if self.at("op", ":"):
            self.next()
        decls = []
        while True:
            decls.append(self.parse_constant_decl())
            if self.at("op", ","):
                self.next()
                continue
            self.end_stmt()
            return decls
    def parse_constant_decl(self) -> ConstDecl:
        name = self.expect("id").value
        value = None
        while self.peek().kind == "kw" and self.peek().value in ("TYPE", "VALUE"):
            if self.at_kw("TYPE"):
                self.next()
                t = self.next()
                if t.kind not in ("id", "kw"):
                    raise SyntaxError(f"line {t.line}: expected type name, got {t.value!r}")
            elif self.at_kw("VALUE"):  # pragma: no branch
                self.next()
                value = self.parse_expr()
        if value is None:
            raise SyntaxError(f"line {self.peek().line}: CONSTANTS declaration requires VALUE")
        return ConstDecl(name, value)
    def parse_enum(self) -> EnumDecl:
        self.expect_kw("ENUM")
        enum_name = self.expect("id").value
        self.end_stmt()
        members = []
        while not self.at_kw("ENDENUM"):
            t = self.next()
            if t.kind not in ("id", "kw"):
                raise SyntaxError(f"line {t.line}: expected enum member name, got {t.value!r}")
            member_name = t.value
            member_value = None
            if self.at_kw("VALUE"):
                self.next()
                member_value = self.parse_expr()
            self.end_stmt()
            members.append((member_name, member_value))
        self.expect_kw("ENDENUM")
        self.end_stmt()
        return EnumDecl(enum_name, members)
    def parse_if(self) -> If:
        self.expect_kw("IF")
        cond = self.parse_expr()
        self.end_stmt()
        arms = [(cond, self.parse_block_until({"ELSEIF", "ELSE", "ENDIF"}))]
        els = None
        while self.at_kw("ELSEIF"):
            self.next()
            cond = self.parse_expr()
            self.end_stmt()
            arms.append((cond, self.parse_block_until({"ELSEIF", "ELSE", "ENDIF"})))
        if self.at_kw("ELSE"):
            self.next()
            self.end_stmt()
            els = self.parse_block_until({"ENDIF"})
        self.expect_kw("ENDIF")
        self.end_stmt()
        return If(arms, els)
    def parse_loop(self) -> ForEach:
        self.expect_kw("LOOP")
        self.expect_kw("AT")
        count = self.parse_expr()
        self.expect_kw("INTO")
        var = self.expect("id").value
        where = None
        if self.at_kw("WHERE"):
            self.next()
            where = self.parse_expr()
        self.end_stmt()
        body = self.parse_block_until({"ENDLOOP"})
        self.expect_kw("ENDLOOP")
        self.end_stmt()
        if where is not None:
            body = [If([(where, body)], None)]
        return ForEach(var, count, body)
    def parse_do(self) -> ForEach:
        self.expect_kw("DO")
        count = self.parse_expr()
        self.expect_kw("TIMES")
        self.end_stmt()
        body = self.parse_block_until({"ENDDO"})
        self.expect_kw("ENDDO")
        self.end_stmt()
        return ForEach(self.fresh_temp(), count, body)
    def parse_case(self) -> Switch:
        self.expect_kw("CASE")
        expr = self.parse_expr()
        self.end_stmt()
        cases = []
        default = None
        while not self.at_kw("ENDCASE"):
            self.expect_kw("WHEN")
            if self.at_kw("OTHERS"):
                self.next()
                self.end_stmt()
                default = self.parse_block_until({"ENDCASE"})
                break
            val = self.parse_expr()
            self.end_stmt()
            body = self.parse_block_until({"WHEN", "ENDCASE"})
            cases.append((val, body))
        self.expect_kw("ENDCASE")
        self.end_stmt()
        return Switch(expr, cases, default)
    def parse_dispatch(self) -> Dispatch:
        self.expect_kw("DISPATCH")
        expr = self.parse_expr()
        self.end_stmt()
        cases = []
        default = None
        while not self.at_kw("ENDDISPATCH"):
            self.expect_kw("WHEN")
            if self.at_kw("OTHERS"):
                self.next()
                self.end_stmt()
                default = self.parse_block_until({"ENDDISPATCH"})
                break
            val = self.parse_expr()
            self.end_stmt()
            body = self.parse_block_until({"WHEN", "ENDDISPATCH"})
            cases.append((val, body))
        self.expect_kw("ENDDISPATCH")
        self.end_stmt()
        return Dispatch(expr, cases, default)
    def parse_form(self) -> Sub:
        self.expect_kw("FORM")
        name = self.expect("id").value
        params = None
        if self.at_kw("USING"):
            self.next()
            params = self.parse_param_names_until_dot()
        self.end_stmt()
        body = self.parse_block_until({"ENDFORM"})
        self.expect_kw("ENDFORM")
        self.end_stmt()
        return Sub(name, body, params if params else None)
    def parse_perform(self) -> Gosub:
        self.expect_kw("PERFORM")
        name = self.expect("id").value
        args = None
        if self.at_kw("USING"):
            self.next()
            args = self.parse_expr_list_until_dot()
        self.end_stmt()
        return Gosub(name, args if args else None)
    def parse_move(self) -> Let:
        self.expect_kw("MOVE")
        value = self.parse_expr()
        self.expect_kw("TO")
        name = self.expect("id").value
        self.end_stmt()
        return Let(name, value)
    def parse_compute(self) -> Let:
        self.expect_kw("COMPUTE")
        name = self.expect("id").value
        self.expect("op", "=")
        value = self.parse_expr()
        self.end_stmt()
        return Let(name, value)
    def parse_add(self) -> Let:
        self.expect_kw("ADD")
        value = self.parse_expr()
        self.expect_kw("TO")
        target = self.expect("id").value
        dest = target
        if self.at_kw("GIVING"):
            self.next()
            dest = self.expect("id").value
        self.end_stmt()
        if dest == target and isinstance(value, Num) and value.value == 1:
            return IncDec(target, 1)
        return Let(dest, Bin("+", Var(target), value))
    def parse_subtract(self) -> Let:
        self.expect_kw("SUBTRACT")
        value = self.parse_expr()
        self.expect_kw("FROM")
        target = self.expect("id").value
        dest = target
        if self.at_kw("GIVING"):
            self.next()
            dest = self.expect("id").value
        self.end_stmt()
        if dest == target and isinstance(value, Num) and value.value == 1:
            return IncDec(target, -1)
        return Let(dest, Bin("-", Var(target), value))
    def parse_multiply(self) -> Let:
        self.expect_kw("MULTIPLY")
        target = self.expect("id").value
        self.expect_kw("BY")
        value = self.parse_expr()
        dest = target
        if self.at_kw("GIVING"):
            self.next()
            dest = self.expect("id").value
        self.end_stmt()
        return Let(dest, Bin("*", Var(target), value))
    def parse_divide(self) -> Let:
        self.expect_kw("DIVIDE")
        target = self.expect("id").value
        self.expect_kw("BY")
        value = self.parse_expr()
        dest = target
        if self.at_kw("GIVING"):
            self.next()
            dest = self.expect("id").value
        self.end_stmt()
        return Let(dest, Bin("/", Var(target), value))
    def parse_param_names_until_dot(self) -> List[str]:
        params = []
        while not self.at("op", "."):
            if self.at("op", ","):
                self.next()
                continue
            params.append(self.expect("id").value)
        return params
    def parse_expr_list_until_dot(self) -> List[object]:
        args = []
        while not self.at("op", "."):
            if self.at("op", ","):
                self.next()
                continue
            args.append(self.parse_expr())
        return args
    def parse_call_from_id(self) -> Call:
        ns = self.expect("id").value
        self.expect("op", ".")
        method = self.next()
        if method.kind not in ("id", "kw"):
            raise SyntaxError(f"line {method.line}: expected method name, got {method.value!r}")
        args = self.parse_args()
        return Call(ns, method.value, args)
    def parse_args(self) -> list:
        self.expect("op", "(")
        args = []
        if not self.at("op", ")"):  # pragma: no branch
            args.append(self.parse_expr())
            while self.at("op", ","):
                self.next()
                args.append(self.parse_expr())
        self.expect("op", ")")
        return args
    def parse_try(self) -> TryExcept:
        self.expect_kw("TRY")
        self.end_stmt()
        try_body = self.parse_block_until({"CATCH", "CLEANUP", "ENDTRY"})
        except_body = []
        finally_body = None
        if self.at_kw("CATCH"):
            self.next()
            self.end_stmt()
            except_body = self.parse_block_until({"CLEANUP", "ENDTRY"})
        if self.at_kw("CLEANUP"):
            self.next()
            self.end_stmt()
            finally_body = self.parse_block_until({"ENDTRY"})
        self.expect_kw("ENDTRY")
        self.end_stmt()
        return TryExcept(try_body, except_body, finally_body)
    def parse_raise(self) -> Raise:
        self.expect_kw("RAISE")
        if self.at("op", "."):
            self.end_stmt()
            return Raise()
        value = self.parse_expr()
        self.end_stmt()
        return Raise(value)
    def parse_on(self) -> OnBlock:
        self.expect_kw("ON")
        ns = self.next()
        if ns.kind not in ("id", "kw"):
            raise SyntaxError(f"line {ns.line}: expected event namespace, got {ns.value!r}")
        self.expect("op", ".")
        method = self.next()
        if method.kind not in ("id", "kw"):
            raise SyntaxError(f"line {method.line}: expected event method, got {method.value!r}")
        self.end_stmt()
        body = self.parse_block_until({"ENDON"})
        self.expect_kw("ENDON")
        self.end_stmt()
        return OnBlock(ns.value, method.value, body)
    def parse_expr(self, min_prec: int = 0) -> object:
        left = self.parse_unary()
        while True:
            op = self.match_binop()
            if op is None or _PREC[op] < min_prec:
                break
            self.next()
            right = self.parse_expr(_PREC[op] + 1)
            if op in _CMP:
                left = Cmp(_CMP[op], left, right)
            else:
                left = Bin(_BINOP[op], left, right)
        return left
    def match_binop(self) -> Optional[str]:
        t = self.peek()
        if t.kind == "op" and t.value in _PREC:
            return t.value
        if t.kind == "kw" and t.value in _PREC:
            return t.value
        return None
    def parse_unary(self) -> object:
        t = self.peek()
        if t.kind == "op" and t.value == "-":
            self.next()
            return Bin("-", Num(0), self.parse_unary())
        if t.kind == "kw" and t.value == "NOT":
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
        if t.kind == "id":
            if (self.at("op", ".") and self.peek(1).kind in ("id", "kw")
                    and self.peek(2).kind == "op" and self.peek(2).value == "("):
                self.next()
                method = self.next().value
                args = self.parse_args()
                return Call(t.value, method, args)
            if self.at("op", "("):
                args = self.parse_args()
                return Call(None, t.value, args)
            return Var(t.value)
        raise SyntaxError(f"line {t.line}: unexpected token {t.value!r}")

def compile_report(source: str):
    """ABAP/4GL-style source -> PicoIL instruction list."""
    toks = tokenize(source)
    prog = Parser(toks).parse_program()
    return Lowerer().lower_program(prog)

if __name__ == "__main__":
    from picoscript_basic import compile_basic
    from picoscript_il import lower_to_bytecode_safe
    from picoscript_vm import PicoVM

    SRC_4GL = """\
DATA: x TYPE i VALUE 10,
      y TYPE i VALUE 32.
IF y > 40.
  WRITE 1.
ELSE.
  WRITE 0.
ENDIF.
PERFORM add_numbers USING x y.
FORM add_numbers USING a b.
  DATA: result TYPE i.
  result = a + b.
  WRITE result.
ENDFORM.
"""

    SRC_BASIC = """\
LET X = 10
LET Y = 32
IF Y > 40 THEN
    PRINT 1
ELSE
    PRINT 0
ENDIF
GOSUB ADD_NUMBERS(X, Y)
SUB ADD_NUMBERS(A, B)
LET RESULT = 0
    LET RESULT = A + B
    PRINT RESULT
ENDSUB
"""

    il_4gl = compile_report(SRC_4GL)
    il_basic = compile_basic(SRC_BASIC)
    words_4gl = lower_to_bytecode_safe(il_4gl)
    words_basic = lower_to_bytecode_safe(il_basic)
    vm = PicoVM().run(words_4gl)

    expected = (0).to_bytes(4, "big") + (42).to_bytes(4, "big")
    got = b"".join(vm.output)

    assert words_4gl == words_basic, "4GL bytecode diverged from equivalent BASIC program"
    assert got == expected, f"unexpected output: got={got!r} expected={expected!r}"
    print("PASS picoscript_4gl: compile + VM output + BASIC parity")
