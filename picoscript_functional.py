#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F#/ML-style PicoScript frontend.

Like picoscript_python.py and picoscript_english.py, this file only changes the
surface tokenizer/parser: it reuses the shared AST nodes and BASIC Lowerer
unchanged, so equivalent programs lower to byte-identical PicoIL / bytecode.

Core syntax includes ``let`` bindings, ``let name args = body`` functions,
juxtaposition application (``f x y``), ``|>`` pipe calls, ``match ... with``,
``if ... then ... else ...``, ``for i in a..b do``, ``while ... do``, and
``printfn`` / ``printf`` output.
"""

from __future__ import annotations

from typing import List, Optional

from picoscript_basic import (
    Num, Str, Var, Bin, Cmp, Call, Let, Ternary, If, While, DoLoop, ForTo, ForEach,
    Switch, Goto, Label, Sub, Gosub, Return, Break, Skip, Print, CallStmt, Lowerer,
    Dispatch, TryExcept, Raise,
)

KEYWORDS = {
    "let", "in", "if", "then", "else", "elif", "match", "with", "for", "do", "while",
    "fun", "rec", "mutable", "printfn", "printf", "not", "true", "false", "and", "or",
    "return", "break", "continue", "skip", "goto", "label",
}

_CMP = {
    "=": "EQ", "==": "EQ", "!=": "NE", "<>": "NE",
    "<": "LT", ">": "GT", "<=": "LE", ">=": "GE",
}
_PREC = {
    "or": 1, "||": 1,
    "and": 2, "&&": 2,
    "=": 3, "==": 3, "!=": 3, "<>": 3, "<": 3, ">": 3, "<=": 3, ">=": 3,
    "+": 5, "-": 5,
    "*": 6, "/": 6, "%": 6,
}
_BINOP = {
    "+": "+", "-": "-", "*": "*", "/": "/", "%": "MOD",
    "and": "AND", "or": "OR", "&&": "AND", "||": "OR",
}

_TWO = {"==", "!=", "<>", "<=", ">=", "|>", "->", "..", "::", "&&", "||"}
_ONE = set("+-*/%()<>=,.:|")


# ── tokenizer (significant indentation; // and (* ... *) comments) ────────────

class Tok:
    __slots__ = ("kind", "value", "line", "pos")

    def __init__(self, kind, value, line, pos=-1):
        self.kind = kind      # num,id,kw,str,op,newline,indent,dedent,eof
        self.value = value
        self.line = line
        self.pos = pos        # absolute source byte offset of the token start

    def __repr__(self):
        return f"Tok({self.kind},{self.value!r})"


def _strip_block_comments(src: str) -> str:
    """Remove OCaml-style block comments while preserving line/offset geometry."""
    out: List[str] = []
    i, n = 0, len(src)
    depth = 0
    while i < n:
        two = src[i:i + 2]
        if depth == 0 and two == "(*":
            out.extend("  ")
            depth = 1
            i += 2
            continue
        if depth:
            if two == "(*":
                out.extend("  ")
                depth += 1
                i += 2
                continue
            if two == "*)":
                out.extend("  ")
                depth -= 1
                i += 2
                continue
            out.append("\n" if src[i] == "\n" else " ")
            i += 1
            continue
        out.append(src[i])
        i += 1
    if depth:
        raise SyntaxError("unterminated block comment")
    return "".join(out)


def _tokenize_line(text: str, lineno: int, out: List[Tok], line_start: int = 0):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        start = line_start + i
        if c in " \t":
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
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
            out.append(Tok("kw" if word.lower() in KEYWORDS else "id", word, lineno, start))
            i = j
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(nxt, nxt))
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
    cleaned = _strip_block_comments(src)
    raw_lines = cleaned.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    offset = 0
    for idx, line in enumerate(raw_lines):
        lineno = idx + 1
        line_start = offset
        offset += len(line) + 1
        stripped = line.lstrip(" \t")
        if stripped == "" or stripped.startswith("//"):
            continue
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


# ── parser ─────────────────────────────────────────────────────────────────────

class _CallTarget:
    __slots__ = ("ns", "method")

    def __init__(self, ns: Optional[str], method: str):
        self.ns = ns
        self.method = method


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

    def at_kw(self, *names) -> bool:
        t = self.peek()
        return t.kind == "kw" and t.value.lower() in names

    def expect(self, kind, value=None) -> Tok:
        t = self.next()
        if t.kind != kind or (value is not None and t.value != value):
            want = value if value is not None else kind
            raise SyntaxError(f"line {t.line}: expected {want!r}, got {t.value!r} ({t.kind})")
        return t

    def expect_kw(self, name: str) -> Tok:
        t = self.next()
        if not (t.kind == "kw" and t.value.lower() == name):
            raise SyntaxError(f"line {t.line}: expected {name}, got {t.value!r}")
        return t

    # -- program / suites -----------------------------------------------------
    def parse_program(self) -> List[object]:
        stmts = []
        while self.peek().kind != "eof":
            s = self.parse_stmt(allow_func=True)
            if s is not None:
                if isinstance(s, list):
                    stmts.extend(s)
                else:
                    stmts.append(s)
        return stmts

    def parse_suite(self, allow_func: bool = False) -> List[object]:
        self.expect("newline")
        self.expect("indent")
        stmts = []
        while not self.at("dedent"):
            if self.at("eof"):
                raise SyntaxError("unexpected EOF inside block")
            s = self.parse_stmt(allow_func=allow_func)
            if s is not None:
                if isinstance(s, list):
                    stmts.extend(s)
                else:
                    stmts.append(s)
        self.expect("dedent")
        return stmts

    def parse_stmt_body(self) -> List[object]:
        if self.at("newline"):
            return self.parse_suite(allow_func=False)
        stmt = self.parse_stmt(allow_func=False)
        if stmt is None:
            return []
        return stmt if isinstance(stmt, list) else [stmt]

    def parse_function_body(self) -> List[object]:
        if not self.at("newline"):
            expr = self.parse_expr()
            self.expect("newline")
            return [Return(expr)]
        self.expect("newline")
        self.expect("indent")
        body = []
        while not self.at("dedent"):
            if self._line_starts_expr():
                expr = self.parse_expr()
                self.expect("newline")
                body.append(Return(expr))
                if not self.at("dedent"):
                    raise SyntaxError(f"line {self.peek().line}: expression result must be the final line of a function body")
                break
            stmt = self.parse_stmt(allow_func=False)
            if stmt is not None:
                if isinstance(stmt, list):
                    body.extend(stmt)
                else:
                    body.append(stmt)
        self.expect("dedent")
        if not body or not isinstance(body[-1], Return):
            body.append(Return())
        return body

    # -- statements -----------------------------------------------------------
    def parse_stmt(self, allow_func: bool = False) -> Optional[object]:
        if self.at("newline"):
            self.next()
            return None
        start = self.peek().pos
        node = self._parse_stmt(allow_func=allow_func)
        if node is not None:
            if isinstance(node, list):
                for item in node:
                    try:
                        item.pos = start
                    except (AttributeError, TypeError):
                        pass
            else:
                try:
                    node.pos = start
                except (AttributeError, TypeError):
                    pass
        return node

    def _parse_stmt(self, allow_func: bool = False) -> Optional[object]:
        t = self.peek()
        if t.kind == "kw":
            kw = t.value.lower()
            if kw == "let":
                return self.parse_let_stmt(allow_func=allow_func)
            if kw in ("printfn", "printf"):
                self.next()
                v = self.parse_expr()
                self.expect("newline")
                return Print(v)
            if kw == "if":
                return self.parse_if_stmt()
            if kw == "while":
                return self.parse_while_stmt()
            if kw == "for":
                return self.parse_for_stmt()
            if kw == "match":
                return self.parse_match_stmt()
            if kw == "return":
                self.next()
                if self.at("newline"):
                    self.next()
                    return Return()
                v = self.parse_expr()
                self.expect("newline")
                return Return(v)
            if kw == "break":
                self.next()
                self.expect("newline")
                return Break()
            if kw in ("continue", "skip"):
                self.next()
                self.expect("newline")
                return Skip()
            if kw == "goto":
                self.next()
                name = self.expect("id").value
                self.expect("newline")
                return Goto(name)
            if kw == "label":
                self.next()
                name = self.expect("id").value
                self.expect("newline")
                return Label(name)
        expr = self.parse_expr()
        self.expect("newline")
        if isinstance(expr, Call):
            # Statement-position function application: lower through the generic call
            # path so user-defined subs and host aliases behave like the other frontends.
            return CallStmt(expr)
        raise SyntaxError(f"line {t.line}: expression statement must be a call, got {t.value!r}")

    def parse_let_stmt(self, allow_func: bool) -> object:
        self.expect_kw("let")
        if self.at_kw("rec"):
            self.next()
        if self.at_kw("mutable"):
            self.next()
        name = self.expect("id").value
        params = []
        while self.at("id"):
            params.append(self.next().value)
        self.expect("op", "=")
        if params:
            if not allow_func:
                raise SyntaxError(f"line {self.peek().line}: function definitions are only allowed at top level")
            return Sub(name, self.parse_function_body(), params)
        value = self.parse_binding_expr()
        self.expect("newline")
        return Let(name, value)

    def parse_binding_expr(self) -> object:
        if self.at("newline"):
            self.expect("newline")
            self.expect("indent")
            expr = self.parse_expr()
            self.expect("newline")
            self.expect("dedent")
            return expr
        return self.parse_expr()

    def parse_if_stmt(self) -> If:
        self.expect_kw("if")
        cond = self.parse_expr()
        self.expect_kw("then")
        body = self.parse_stmt_body()
        arms = [(cond, body)]
        els = None
        while self.at_kw("elif"):
            self.next()
            c2 = self.parse_expr()
            self.expect_kw("then")
            arms.append((c2, self.parse_stmt_body()))
        if self.at_kw("else"):
            self.next()
            els = self.parse_stmt_body()
        return If(arms, els)

    def parse_while_stmt(self) -> While:
        self.expect_kw("while")
        cond = self.parse_expr()
        self.expect_kw("do")
        return While(cond, self.parse_stmt_body())

    def parse_for_stmt(self) -> ForTo:
        self.expect_kw("for")
        var = self.expect("id").value
        self.expect_kw("in")
        start = self.parse_expr()
        self.expect("op", "..")
        end = self.parse_expr()
        self.expect_kw("do")
        return ForTo(var, start, end, None, self.parse_stmt_body())

    def parse_match_stmt(self) -> Switch:
        self.expect_kw("match")
        expr = self.parse_expr()
        self.expect_kw("with")
        self.expect("newline")
        cases = []
        default = None
        while self.at("op", "|"):
            self.next()
            if self.at("id", "_"):
                self.next()
                self.expect("op", "->")
                default = self.parse_stmt_body()
                continue
            val = self.parse_expr()
            self.expect("op", "->")
            cases.append((val, self.parse_stmt_body()))
        if not cases and default is None:
            raise SyntaxError(f"line {self.peek().line}: expected at least one '| case -> ...' after 'match ... with'")
        return Switch(expr, cases, default)

    # -- expressions ----------------------------------------------------------
    def parse_expr(self) -> object:
        if self.at_kw("if"):
            return self.parse_if_expr()
        return self.parse_pipe()

    def parse_if_expr(self) -> Ternary:
        self.expect_kw("if")
        cond = self.parse_pipe()
        self.expect_kw("then")
        then_expr = self.parse_expr()
        self.expect_kw("else")
        else_expr = self.parse_expr()
        return Ternary(cond, then_expr, else_expr)

    def parse_pipe(self) -> object:
        left = self.parse_binary()
        while self.at("op", "|>"):
            self.next()
            right = self.parse_application()
            left = self.apply_pipe(left, right)
        return left

    def apply_pipe(self, lhs, rhs):
        if isinstance(rhs, Var):
            return Call(None, rhs.name, [lhs])
        if isinstance(rhs, Call):
            return Call(rhs.ns, rhs.method, [lhs] + list(rhs.args))
        raise SyntaxError(f"line {self.peek().line}: right-hand side of '|>' must be a function or host call target")

    def parse_binary(self, min_prec: int = 0) -> object:
        left = self.parse_application()
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
            right = self.parse_binary(_PREC[opval] + 1)
            if opval in _CMP:
                left = Cmp(_CMP[opval], left, right)
            else:
                left = Bin(_BINOP[opval], left, right)
        return left

    def parse_application(self) -> object:
        left = self.parse_unary()
        while self._is_app_arg_start(self.peek()):
            if not self._callable_expr(left):
                break
            arg = self.parse_unary()
            if isinstance(left, Var):
                left = Call(None, left.name, [arg])
            elif isinstance(left, _CallTarget):
                left = Call(left.ns, left.method, [arg])
            elif isinstance(left, Call):
                left.args.append(arg)
            else:
                raise SyntaxError(f"line {self.peek().line}: cannot apply arguments to {left!r}")
        if isinstance(left, _CallTarget):
            return Call(left.ns, left.method, [])
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
                method_tok = self.next()
                if method_tok.kind not in ("id", "kw"):
                    raise SyntaxError(f"line {method_tok.line}: expected method name after '.', got {method_tok.value!r}")
                if self.at("op", "("):
                    return Call(t.value, method_tok.value, self.parse_paren_args())
                return _CallTarget(t.value, method_tok.value)
            if self.at("op", "("):
                return Call(None, t.value, self.parse_paren_args())
            return Var(t.value)
        raise SyntaxError(f"line {t.line}: unexpected token {t.value!r}")

    def parse_paren_args(self) -> list:
        self.expect("op", "(")
        args = []
        if not self.at("op", ")"):
            args.append(self.parse_expr())
            while self.at("op", ","):
                self.next()
                args.append(self.parse_expr())
        self.expect("op", ")")
        return args

    def _callable_expr(self, node) -> bool:
        return isinstance(node, (Var, Call, _CallTarget))

    def _is_app_arg_start(self, t: Tok) -> bool:
        if t.kind in ("num", "str", "id"):
            return True
        if t.kind == "op" and t.value in ("(", "-"):
            return True
        return t.kind == "kw" and t.value.lower() in ("true", "false", "not")

    def _line_starts_expr(self) -> bool:
        t = self.peek()
        if t.kind in ("num", "str", "id"):
            return True
        if t.kind == "op" and t.value in ("(", "-"):
            return True
        return t.kind == "kw" and t.value.lower() in ("if", "true", "false", "not")


# ── public API ────────────────────────────────────────────────────────────────

def compile_functional(source: str):
    """F#/ML-style source -> PicoIL instruction list (reuses the BASIC Lowerer)."""
    toks = tokenize(source)
    prog = Parser(toks).parse_program()
    return Lowerer().lower_program(prog)


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from picoscript_il import lower_to_bytecode_safe
    from picoscript_python import compile_python
    from picoscript_vm import PicoVM

    FUNCTIONAL_DEMO = """\
// let bindings
let x = 10
let y = x + 32

// functions with let ... = syntax
let add a b = a + b

// pipe operator for host calls
let piped = 255 |> Number.ToString |> String.Length

// pattern matching
match y with
| 42 -> printfn y
| _ -> printfn 0

// if expression
let z = if y > 40 then y else 0
printfn z

// immutable iteration (compiles to ForTo)
for i in 0..4 do
    printfn (add i x)

// function application
let r = add 10 32
printfn r
printfn piped
"""

    PYTHON_EQUIV = """\
x = 10
y = x + 32

def add(a, b):
    return a + b

piped = String.Length(Number.ToString(255))

match y:
    case 42:
        print(y)
    case _:
        print(0)

z = y if y > 40 else 0
print(z)

for i in range(0, 5):
    print(add(i, x))

r = add(10, 32)
print(r)
print(piped)
"""

    il_func = compile_functional(FUNCTIONAL_DEMO)
    il_py = compile_python(PYTHON_EQUIV)
    words_func = lower_to_bytecode_safe(il_func)
    words_py = lower_to_bytecode_safe(il_py)

    assert words_func == words_py, "functional frontend bytecode diverged from equivalent python frontend"

    def _s32(v: int) -> int:
        return v - 0x100000000 if v & 0x80000000 else v

    def _decode_print(vm: PicoVM) -> list:
        return [_s32(int.from_bytes(chunk, "big")) for chunk in vm.output]

    vm = PicoVM().run(words_func)
    expected = [42, 42, 10, 11, 12, 13, 14, 42, 3]
    got = _decode_print(vm)
    assert got == expected, f"unexpected PicoVM output: got={got!r} expected={expected!r}"

    print("PASS functional frontend self-test")
