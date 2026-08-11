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
  x = y + 3 * (x - 1);  // arithmetic and parentheses
  x = (x << 3) | 7;     // native C bitwise syntax
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
assigned (materialized to 0/1). Everything is a 32-bit word unless an explicitly
wide systems type is used.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields, is_dataclass
from typing import List, Optional, Tuple, Union, Dict

from picoscript_il import ILBuilder, VReg, Imm, COND, COND_NEGATE, canon_host
from picoscript_lang import encode_card_addr, resolve_named_constant, HOST_HOOK_CODES
from picoscript_basic import event_type_hash
from picoscript_systems import (
    F_INT64, F_MEMORY, F_POINTER, F_FRAME,
    T_I64, T_U64, T_PTR, T_SIZE, T_OFFSET,
    S_MOV64, S_MOVI64, S_ADD64, S_SUB64, S_MUL64, S_DIV64,
    S_AND64, S_OR64, S_XOR64, S_SHL64, S_SHR64, S_FROM_R32, S_TO_R32,
    S_EQ64, S_NE64, S_LT64, S_GT64, S_LE64, S_GE64,
    S_LOAD8, S_LOAD16, S_LOAD32, S_LOAD64,
    S_STORE8, S_STORE16, S_STORE32, S_STORE64,
    S_MEMSET, S_PTR_ADD,
    S_FRAME_ENTER, S_FRAME_LEAVE, S_ADDR_LOCAL,
)


_SYSTEM_HEADERS = {
    "assert.h", "stdbool.h", "stddef.h", "stdint.h", "stdlib.h", "string.h",
    "limits.h", "inttypes.h",
}


def _macro_expand_line(line: str, macros: Dict[str, str]) -> str:
    """Expand object-like macros outside string/character literals.

    PicoScript deliberately implements a bounded, deterministic preprocessing
    subset: object macros, includes and conditional compilation. Function-like
    macros remain rejected instead of attempting an incomplete C preprocessor.
    """
    out = []
    i = 0
    quote = None
    while i < len(line):
        c = line[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < len(line):
                out.append(line[i + 1]); i += 2; continue
            if c == quote:
                quote = None
            i += 1; continue
        if c in ('"', "'"):
            quote = c; out.append(c); i += 1; continue
        if c.isalpha() or c == "_":
            j = i + 1
            while j < len(line) and (line[j].isalnum() or line[j] == "_"):
                j += 1
            name = line[i:j]
            out.append(macros.get(name, name))
            i = j; continue
        out.append(c); i += 1
    return "".join(out)


def _pp_condition(expr: str, macros: Dict[str, str]) -> bool:
    expr = re.sub(r"defined\s*\(\s*([A-Za-z_]\w*)\s*\)",
                  lambda m: "1" if m.group(1) in macros else "0", expr)
    expr = re.sub(r"defined\s+([A-Za-z_]\w*)",
                  lambda m: "1" if m.group(1) in macros else "0", expr)
    expr = _macro_expand_line(expr, macros)
    expr = re.sub(r"\b[A-Za-z_]\w*\b", "0", expr)
    expr = expr.replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"!(?!=)", " not ", expr)
    if not re.fullmatch(r"[\s0-9a-fA-FxX()+\-*/%<>=!&|.^~notandor]+", expr):
        raise SyntaxError(f"unsupported preprocessor expression {expr!r}")
    try:
        return bool(eval(expr, {"__builtins__": {}}, {}))
    except Exception as exc:
        raise SyntaxError(f"invalid preprocessor expression {expr!r}") from exc


def preprocess_c(source: str, *, source_path: Optional[str] = None,
                 include_resolver=None, defines: Optional[Dict[str, object]] = None,
                 _seen=None) -> str:
    """Apply PicoScript's deterministic C-preprocessor subset.

    Supported: ``#include``, object-like ``#define``/``#undef``, ``#if``,
    ``#ifdef``, ``#ifndef``, ``#elif``, ``#else``, ``#endif`` and ``#pragma
    once``. Standard freestanding headers are recognized as type declarations
    supplied by the dialect. Quoted includes resolve relative to source_path or
    through include_resolver(name, angled).
    """
    # Recursive includes share the macro table, as in C; only the public entry
    # copies caller-owned definitions.
    macros = ({str(k): str(v) for k, v in (defines or {}).items()}
              if _seen is None else defines)
    seen = set() if _seen is None else _seen
    logical = source.replace("\\\r\n", "").replace("\\\n", "")
    out = []
    pack_alignment = 0
    # frame = [parent_active, this_active, any_branch_taken]
    cond = []

    def active():
        return all(frame[1] for frame in cond)

    for raw in logical.splitlines():
        stripped = raw.lstrip()
        if not stripped.startswith("#"):
            line = _macro_expand_line(raw, macros) if active() else ""
            if pack_alignment == 1 and "{" in line and re.search(r"\bstruct\s+", line):
                line = re.sub(r"\bstruct\s+", "struct __attribute__((packed)) ", line, count=1)
            out.append(line)
            continue
        directive = stripped[1:].strip()
        word, _, rest = directive.partition(" ")
        word = word.strip(); rest = rest.strip()
        if word in ("if", "ifdef", "ifndef"):
            parent = active()
            if word == "ifdef":
                take = rest in macros
            elif word == "ifndef":
                take = rest not in macros
            else:
                take = _pp_condition(rest, macros) if parent else False
            cond.append([parent, parent and take, parent and take])
        elif word == "elif":
            if not cond: raise SyntaxError("#elif without #if")
            frame = cond[-1]
            take = frame[0] and not frame[2] and _pp_condition(rest, macros)
            frame[1] = take; frame[2] = frame[2] or take
        elif word == "else":
            if not cond: raise SyntaxError("#else without #if")
            frame = cond[-1]
            take = frame[0] and not frame[2]
            frame[1] = take; frame[2] = True
        elif word == "endif":
            if not cond: raise SyntaxError("#endif without #if")
            cond.pop()
        elif not active():
            continue
        elif word == "define":
            name, sep, value = rest.partition(" ")
            if "(" in name:
                raise SyntaxError("function-like macros are not supported")
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                raise SyntaxError(f"invalid macro name {name!r}")
            macros[name] = value.strip() if sep else "1"
        elif word == "undef":
            macros.pop(rest, None)
        elif word == "include":
            m = re.fullmatch(r'([<"])([^>"]+)[>"]', rest)
            if not m: raise SyntaxError(f"invalid #include {rest!r}")
            angled = m.group(1) == "<"; name = m.group(2)
            if angled and name in _SYSTEM_HEADERS:
                continue
            text = None; child_path = None
            if include_resolver is not None:
                resolved = include_resolver(name, angled)
                if isinstance(resolved, tuple): child_path, text = resolved
                else: text = resolved
            elif not angled and source_path:
                child_path = os.path.abspath(os.path.join(os.path.dirname(source_path), name))
                if os.path.isfile(child_path):
                    with open(child_path, encoding="utf-8") as handle: text = handle.read()
            if text is None:
                raise SyntaxError(f"cannot resolve include {name!r}")
            identity = child_path or name
            if identity not in seen:
                seen.add(identity)
                out.append(preprocess_c(text, source_path=child_path,
                                        include_resolver=include_resolver,
                                        defines=macros, _seen=seen))
        elif word == "pragma" and rest in ("once",):
            continue
        elif word == "pragma" and rest.startswith("pack"):
            arg = rest[4:].strip().strip("()")
            parts = [x.strip() for x in arg.split(",") if x.strip()]
            if "pop" in parts:
                pack_alignment = 0
            elif parts and parts[-1].isdigit():
                pack_alignment = int(parts[-1])
            elif not parts:
                pack_alignment = 0
        else:
            raise SyntaxError(f"unsupported preprocessor directive #{word}")
    if cond:
        raise SyntaxError("unterminated preprocessor conditional")
    return "\n".join(out)

# ── tokens ──────────────────────────────────────────────────────────────────

KEYWORDS = {"int", "var", "void", "char", "short", "long", "signed", "unsigned",
            "bool", "struct", "typedef", "sizeof", "static", "extern", "volatile",
            "if", "else", "while", "for", "return",
            "break", "continue", "switch", "case", "default", "do", "goto",
            "dispatch", "const", "enum", "try", "catch", "finally", "raise", "on"}

# C-frontend aliases: libc-style spellings -> canonical (ns, method). Pure frontend
# sugar -- resolves to the same host call, so bytecode/output is identical on all five
# paths. C's radix convention is bare lowercase, so atoi/itoa/Number.ToHex need no
# prefix. A user-defined function of the same name takes precedence over an alias.
C_ALIASES = {
    "strlen":  ("String", "Length"),
    "strcat":  ("String", "Concat"),
    "strstr":  ("String", "IndexOf"),
    "toupper": ("String", "ToUpper"),
    "tolower": ("String", "ToLower"),
    "substr":  ("String", "Substring"),
    "atoi":    ("Number", "Parse"),
    "itoa":    ("Number", "ToString"),
    "tohex":   ("Number", "ToHex"),
    "abs":     ("Number", "Abs"),
    "sqrt":    ("Maths", "Sqrt"),
    "pow":     ("Maths", "Power"),
    "sha256":  ("Crypto", "Sha256"),
}

_TWO = {"==", "!=", "<=", ">=", "&&", "||", "++", "--", "<<", ">>", "->",
        "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="}
_ONE = set("+-*/%()<>=;,{}.!?:&|^~[]")


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
                    escaped = src[j + 1]
                    buf.append({
                        "n": "\n", "r": "\r", "t": "\t",
                        "\\": "\\", '"': '"', "'": "'",
                    }.get(escaped, escaped))
                    j += 2
                    continue
                buf.append(src[j]); j += 1
            toks.append(Tok("str", "".join(buf), i))
            i = j + 1
            continue
        if c == "'":
            j = i + 1
            if j < n and src[j] == "\\":
                esc = src[j + 1] if j + 1 < n else ""
                value = {"n": 10, "r": 13, "t": 9, "0": 0,
                         "\\": 92, "'": 39, '"': 34}.get(esc, ord(esc) if esc else 0)
                j += 2
            else:
                value = ord(src[j]) if j < n else 0; j += 1
            if j >= n or src[j] != "'":
                raise SyntaxError(f"unterminated character literal at {i}")
            toks.append(Tok("num", str(value), i)); i = j + 1; continue
        if c.isdigit() or (c == "0" and i + 1 < n and src[i + 1] in "xX"):
            j = i
            if src[j] == "0" and j + 1 < n and src[j + 1] in "xX":
                j += 2
                while j < n and src[j] in "0123456789abcdefABCDEF":
                    j += 1
            else:
                while j < n and src[j].isdigit():
                    j += 1
            while j < n and src[j] in "uUlL":
                j += 1
            spelling = src[i:j].rstrip("uUlL")
            toks.append(Tok("num", spelling, i))
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
class FieldRef:
    obj: str; field: str
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
@dataclass(frozen=True)
class CType:
    name: str
    pointers: int = 0
    const: bool = False
    func_params: tuple = ()
    volatile: bool = False
    @property
    def is_function_pointer(self):
        return bool(self.func_params)
@dataclass
class StructField:
    name: str; ctype: CType; count: int = 1; offset: int = 0
@dataclass
class StructDef:
    name: str; fields: list; packed: bool = False
@dataclass
class TypedefDef:
    name: str; ctype: CType
@dataclass
class IndexRef:
    base: object; index: object
@dataclass
class MemberRef:
    base: object; field: str; through_pointer: bool = False
@dataclass
class AddressOf:
    target: object
@dataclass
class Deref:
    pointer: object
@dataclass
class SizeofExpr:
    target: object; is_type: bool = False
@dataclass
class CastExpr:
    ctype: CType; value: object
@dataclass
class WideValue:
    lo: VReg
    hi: Optional[VReg] = None       # retained for AST compatibility; systems ISA uses one X reg
@dataclass
class Call:
    ns: Optional[str]; method: str; args: list
@dataclass
class Invoke:
    callee: object; args: list
@dataclass
class Decl:
    name: str; init: object; ctype: Optional[CType] = None; count: int = 1
@dataclass
class ConstDecl:
    name: str; value: object
@dataclass
class EnumDecl:
    enum_name: str; members: list   # members=[(name, value_expr_or_None), ...]
@dataclass
class Assign:
    name: str; value: object
@dataclass
class Store:
    target: object; value: object
@dataclass
class FieldAssign:
    obj: str; field: str; value: object
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
class ServerMain:
    body: list                       # transparent server-entry wrapper: Server.Main { ... }
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
class TryCatch:
    try_body: list; catch_body: list; finally_body: Optional[list] = None
@dataclass
class Raise:
    value: object = None
@dataclass
class OnBlock:
    event_ns: str; event_method: str; body: list   # on Ns.Method { body }
@dataclass
class Func:
    name: str; body: list; params: list = None   # params = parameter names (None = legacy)
    param_types: list = None
    return_type: Optional[CType] = None


# ── parser (recursive descent + Pratt expressions) ──────────────────────────

_PREC = {
    "||": 1, "&&": 2, "|": 3, "^": 4, "&": 5,
    "==": 6, "!=": 6, "<": 7, ">": 7, "<=": 7, ">=": 7,
    "<<": 8, ">>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10,
}
_COMPOUND = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%",
             "&=": "&", "|=": "|", "^=": "^"}


class Parser:
    def __init__(self, toks: List[Tok]):
        self.toks = toks
        self.i = 0
        self.type_names = {
            "int", "var", "void", "char", "short", "long", "signed", "unsigned",
            "bool", "uint8_t", "int8_t", "uint16_t", "int16_t", "uint32_t",
            "int32_t", "uint64_t", "int64_t", "size_t", "uintptr_t",
        }
        self.structs: Dict[str, StructDef] = {}
        self.typedefs: Dict[str, CType] = {}

    def peek(self) -> Tok:
        if self.i >= len(self.toks):
            raise SyntaxError(f"unexpected end of input at token {self.i}")
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
        if t.value == "typedef":
            return self.parse_typedef()
        if t.value == "struct" and self._is_struct_definition():
            return self.parse_struct_definition()
        if self.is_type_start(t):
            mark = self.i
            try:
                ctype = self.parse_type()
                while self.accept("*"):
                    ctype = CType(ctype.name, ctype.pointers + 1, ctype.const,
                                  ctype.func_params, ctype.volatile)
                is_func = self.peek().kind == "id" and self.toks[self.i + 1].value == "("
            except SyntaxError:
                is_func = False
            self.i = mark
            if is_func:
                return self._parse_func_def()
        return self.parse_stmt()

    def _parse_func_def(self) -> Func:
        """Parse a typed C function definition."""
        return_type = self.parse_type()
        while self.accept("*"):
            return_type = CType(return_type.name, return_type.pointers + 1,
                                return_type.const, return_type.func_params, return_type.volatile)
        name = self.next().value
        self.expect("(")
        params, param_types = [], []
        if not self.accept(")"):
            if self.peek().value == "void" and self.toks[self.i + 1].value == ")":
                self.next(); self.expect(")")
                body = self.parse_block()
                return Func(name, body, None, None, return_type)
            while True:
                pt = self.parse_type()
                pname, pt, _ = self.parse_declarator(pt)
                params.append(pname); param_types.append(pt)
                if not self.accept(","):
                    break
            self.expect(")")
        body = self.parse_block()
        return Func(name, body, params if params else None,
                    param_types if param_types else None, return_type)

    def is_type_start(self, tok: Optional[Tok] = None) -> bool:
        tok = tok or self.peek()
        return (tok.value in self.type_names or tok.value in self.typedefs
                or tok.value in ("struct", "const", "volatile", "static", "extern"))

    def parse_type(self) -> CType:
        const = False; volatile = False
        while self.peek().value in ("const", "static", "extern", "volatile"):
            q = self.next().value
            const = const or q == "const"; volatile = volatile or q == "volatile"
        if self.accept("struct"):
            name = self.next().value
            base = CType("struct " + name, const=const, volatile=volatile)
        else:
            parts = []
            while self.peek().value in ("signed", "unsigned", "short", "long"):
                parts.append(self.next().value)
            if self.peek().value in self.type_names or self.peek().value in self.typedefs:
                parts.append(self.next().value)
            if not parts:
                raise SyntaxError(f"expected type at {self.peek().pos}")
            spelling = " ".join(parts)
            aliases = {
                "unsigned char": "uint8_t", "signed char": "int8_t",
                "unsigned short": "uint16_t", "short": "int16_t",
                "signed short": "int16_t", "unsigned": "uint32_t",
                "unsigned int": "uint32_t", "signed": "int",
                "signed int": "int", "unsigned long": "uint32_t",
                "long": "int32_t", "long int": "int32_t",
                "unsigned long long": "uint64_t", "long long": "int64_t",
            }
            spelling = aliases.get(spelling, spelling)
            if spelling in self.typedefs:
                old = self.typedefs[spelling]
                base = CType(old.name, old.pointers, const or old.const, old.func_params,
                             volatile or old.volatile)
            else:
                base = CType(spelling, const=const, volatile=volatile)
        return base

    def parse_declarator(self, base: CType):
        pointers = base.pointers
        while self.accept("*"):
            pointers += 1
            self.accept("const")
        if self.accept("(") and self.accept("*"):
            name = self.next().value
            self.expect(")"); self.expect("(")
            params = []
            if not self.accept(")"):
                while True:
                    params.append(self.parse_type())
                    if self.peek().kind == "id": self.next()
                    if not self.accept(","): break
                self.expect(")")
            return name, CType(base.name, pointers + 1, base.const, tuple(params), base.volatile), 1
        name = self.next().value
        count = 1
        if self.accept("["):
            count = self._eval_array_bound()
            self.expect("]")
        return name, CType(base.name, pointers, base.const, base.func_params, base.volatile), count

    def _eval_array_bound(self):
        if self.peek().kind == "num":
            return int(self.next().value, 0)
        name = self.next().value
        raise SyntaxError(f"array bound {name!r} must be an integer literal")

    def _is_struct_definition(self):
        for tok in self.toks[self.i + 1:self.i + 12]:
            if tok.value == "{": return True
            if tok.value == ";": return False
        return False

    def _packed_attribute(self):
        if self.peek().value != "__attribute__": return False
        self.next(); self.expect("("); self.expect("(")
        packed = self.next().value == "packed"
        self.expect(")"); self.expect(")")
        return packed

    def parse_struct_definition(self, typedef_name=None):
        self.expect("struct")
        packed = self._packed_attribute()
        tag = self.next().value if self.peek().kind == "id" else (typedef_name or "__anon")
        packed = self._packed_attribute() or packed
        self.expect("{")
        fields = []
        while not self.accept("}"):
            ft = self.parse_type()
            fn, ft, count = self.parse_declarator(ft)
            self.expect(";")
            fields.append(StructField(fn, ft, count))
        packed = self._packed_attribute() or packed
        alias = self.next().value if self.peek().kind == "id" else None
        self.expect(";")
        definition = StructDef(tag, fields, packed)
        self.structs[tag] = definition
        self.type_names.add("struct " + tag)
        if alias:
            self.typedefs[alias] = CType("struct " + tag)
            self.type_names.add(alias)
        return definition

    def parse_typedef(self):
        self.expect("typedef")
        if self.peek().value == "struct" and self._is_struct_definition():
            return self.parse_struct_definition()
        base = self.parse_type()
        name, ctype, _ = self.parse_declarator(base)
        self.expect(";")
        self.typedefs[name] = ctype
        self.type_names.add(name)
        return TypedefDef(name, ctype)

    def parse_block(self) -> List[object]:
        self.expect("{")
        stmts = []
        while not self.accept("}"):
            if self.peek().kind == "eof":
                raise SyntaxError("unterminated block")
            stmts.append(self.parse_stmt())
        return stmts

    def parse_stmt(self) -> object:
        # INV-25: stamp every statement node with the source offset of its first
        # token, so the lowerer can attribute emitted bytecode back to source.
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
        if t.kind == "kw":
            if self.is_type_start(t) and t.value not in ("const", "enum"):
                return self.parse_decl()
            if t.value == "const":
                # Preserve PicoScript's legacy compile-time `const int/var`.
                # Qualified systems types are real typed, read-only objects.
                if (self.i + 1 < len(self.toks)
                        and self.toks[self.i + 1].value in ("int", "var")
                        and self.i + 2 < len(self.toks)
                        and self.toks[self.i + 2].kind == "id"):
                    return self.parse_const_decl()
                return self.parse_decl()
            if t.value == "enum":
                return self.parse_enum_decl()
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
            if t.value == "try":
                return self.parse_try()
            if t.value == "raise":
                self.next()
                if self.accept(";"):
                    return Raise(None)
                v = self.parse_expr(); self.expect(";"); return Raise(v)
            if t.value == "on":
                return self.parse_on_block()
        # Server.Main { ... } -- server-entry wrapper (transparent: body is the entry).
        if (t.kind == "id" and t.value == "Server" and self.i + 3 < len(self.toks)
                and self.toks[self.i + 1].value == "." and self.toks[self.i + 2].value == "Main"
                and self.toks[self.i + 3].value == "{"):
            self.next(); self.next(); self.next()     # Server . Main
            return ServerMain(self.parse_block())
        if t.value == "{":
            return ExprStmt(None) if False else self._block_stmt()
        # label:  name :
        if t.kind == "id" and self.toks[self.i + 1].value == ":":
            name = self.next().value
            self.next()  # ':'
            return Label(name)
        # typed declaration (including the legacy active-record spelling).
        # Type name is documentation/schema identity for now; the variable stores
        # the current card id/handle returned by Storage.GetCard/QueryResult.
        if t.kind == "id" and self.toks[self.i + 1].kind == "id":
            if t.value in self.type_names or t.value in self.typedefs:
                return self.parse_decl()
            self.next()  # legacy schema/record type name
            return self.parse_decl_after_type()
        # active-record field assignment: ord.qty = 42; / ord.qty-- / ord.qty += 2
        if (t.kind == "id" and self.toks[self.i + 1].value == "."
                and self.toks[self.i + 2].kind == "id"
                and self.toks[self.i + 3].value in ("=", "++", "--", "+=", "-=", "*=", "/=", "%=")):
            obj = self.next().value
            self.expect(".")
            field = self.next().value
            op = self.next().value
            if op == "=":
                v = self.parse_expr()
            elif op in ("++", "--"):
                v = Bin("+" if op == "++" else "-", FieldRef(obj, field), Num(1))
            else:
                v = Bin(_COMPOUND[op], FieldRef(obj, field), self.parse_expr())
            self.expect(";")
            return FieldAssign(obj, field, v)
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
        if self.peek().value == "=" or self.peek().value in _COMPOUND:
            op = self.next().value
            rhs = self.parse_expr()
            value = rhs if op == "=" else Bin(_COMPOUND[op], expr, rhs)
            self.expect(";")
            if isinstance(expr, Var):
                return Assign(expr.name, value)
            if isinstance(expr, FieldRef):
                return FieldAssign(expr.obj, expr.field, value)
            return Store(expr, value)
        self.expect(";")
        return ExprStmt(expr)

    def _block_stmt(self):
        body = self.parse_block()
        return If(Num(1), body, None)  # bare block == always-true if (keeps scope flat)

    def parse_decl(self) -> Decl:
        ctype = self.parse_type()
        name, ctype, count = self.parse_declarator(ctype)
        init = None
        if self.accept("="):
            init = self.parse_expr()
        self.expect(";")
        return Decl(name, init, ctype, count)

    def parse_const_decl(self) -> ConstDecl:
        self.next()  # const
        if self.peek().kind == "kw" and self.peek().value in ("int", "var"):
            self.next()
        name = self.next().value
        self.expect("=")
        value = self.parse_expr()
        self.expect(";")
        return ConstDecl(name, value)

    def parse_enum_decl(self) -> EnumDecl:
        self.next()  # enum
        enum_name = self.next().value
        self.expect("{")
        members = []
        while not self.accept("}"):
            if self.peek().kind == "eof":
                raise SyntaxError("unterminated enum declaration")
            member_name = self.next().value
            member_value = None
            if self.accept("="):
                member_value = self.parse_expr()
            members.append((member_name, member_value))
            self.accept(",")
        self.expect(";")
        return EnumDecl(enum_name, members)

    def parse_decl_after_type(self) -> Decl:
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

    def parse_try(self) -> TryCatch:
        """try { ... } catch { ... } [finally { ... }] (C-brace style,
        mirrors picoscript_python.py's try/except/finally at the AST level)."""
        self.next()                              # try
        try_body = self.parse_block()
        if not (self.peek().kind == "kw" and self.peek().value == "catch"):
            raise SyntaxError(f"line {self.peek().line}: expected 'catch' after try block")
        self.next()                              # catch
        catch_body = self.parse_block()
        finally_body = None
        if self.peek().kind == "kw" and self.peek().value == "finally":
            self.next()
            finally_body = self.parse_block()
        return TryCatch(try_body, catch_body, finally_body)

    def parse_on_block(self) -> OnBlock:
        """on Ns.Method { ... } (mirrors picoscript_basic.py's parse_on_block)."""
        self.next()                              # on
        ns = self.next().value
        self.expect(".")
        method = self.next().value
        body = self.parse_block()
        return OnBlock(ns, method, body)

    def parse_decl_noeat_semicolon(self) -> Decl:
        ctype = self.parse_type()
        name, ctype, count = self.parse_declarator(ctype)
        init = None
        if self.accept("="):
            init = self.parse_expr()
        self.expect(";")
        return Decl(name, init, ctype, count)

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
        if t.value == "(" and self.i + 1 < len(self.toks) and self.is_type_start(self.toks[self.i + 1]):
            self.next(); ct = self.parse_type()
            while self.accept("*"):
                ct = CType(ct.name, ct.pointers + 1, ct.const, ct.func_params, ct.volatile)
            self.expect(")")
            return CastExpr(ct, self.parse_unary())
        if t.kind == "op" and t.value in ("++", "--"):
            op = self.next().value
            return IncDec(op, self.parse_unary(), True)
        if t.value in ("-", "!", "~") and t.kind == "op":
            op = self.next().value
            return Unary(op, self.parse_unary())
        if t.value == "&" and t.kind == "op":
            self.next(); return AddressOf(self.parse_unary())
        if t.value == "*" and t.kind == "op":
            self.next(); return Deref(self.parse_unary())
        if t.value == "sizeof":
            self.next()
            if self.accept("("):
                if self.is_type_start():
                    ct = self.parse_type()
                    while self.accept("*"):
                        ct = CType(ct.name, ct.pointers + 1, ct.const, ct.func_params, ct.volatile)
                    self.expect(")")
                    return SizeofExpr(ct, True)
                e = self.parse_expr(); self.expect(")")
                return SizeofExpr(e, False)
            return SizeofExpr(self.parse_unary(), False)
        return self.parse_atom()

    def parse_atom(self) -> object:
        node = self._parse_primary()
        while True:
            if self.peek().value == "(":
                node = Invoke(node, self.parse_args()); continue
            if self.accept("["):
                index = self.parse_expr(); self.expect("]")
                node = IndexRef(node, index); continue
            if self.accept("->"):
                node = MemberRef(node, self.next().value, True); continue
            if self.peek().kind == "op" and self.peek().value in ("++", "--"):
                node = IncDec(self.next().value, node, False); continue
            break
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
                if self.peek().value == "(":
                    args = self.parse_args()
                    return (Call(t.value, method, args) if t.value[:1].isupper()
                            else Invoke(FieldRef(t.value, method), args))
                return FieldRef(t.value, method)
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
    def __init__(self, structs=None, typedefs=None):
        self.b = ILBuilder()
        self.vars: Dict[str, VReg] = {}
        self.var_types: Dict[str, CType] = {}
        self.var_counts: Dict[str, int] = {}
        self.memory_vars = set()
        self.const_vars = set()
        self.structs: Dict[str, StructDef] = dict(structs or {})
        self.typedefs: Dict[str, CType] = dict(typedefs or {})
        self.funcs: List[Func] = []
        self.user_constants: Dict[str, int] = {}
        self.loop_stack: List[Tuple[str, str]] = []   # (continue_label, break_label)
        # String-literal constant pool: each distinct literal is interned to its own
        # stable address (deduped), growing DOWN from the bump-arena base 0x8000 so
        # literal spans never overlap each other or the bump arena. Replaces the old
        # 2-alternating-slot scheme that clobbered a 3rd live literal.
        self._strpool: Dict[bytes, int] = {}
        self._strpool_top = 0x8000
        self._heap_top = VReg("__c_heap_top__", pinned=True)
        self._in_system_frame = False
        self._frame_offsets = {}
        self._current_func = None
        self._global_names = set()

    def type_size(self, ctype: Optional[CType]) -> int:
        if ctype is None: return 4
        if ctype.pointers or ctype.is_function_pointer: return 4
        name = ctype.name
        if name in self.typedefs:
            return self.type_size(self.typedefs[name])
        if name.startswith("struct "):
            return self.layout_struct(name[7:])
        return {"char": 1, "uint8_t": 1, "int8_t": 1, "bool": 1,
                "short": 2, "uint16_t": 2, "int16_t": 2,
                "uint64_t": 8, "int64_t": 8}.get(name, 4)

    def layout_struct(self, name: str) -> int:
        sd = self.structs.get(name)
        if sd is None: raise SyntaxError(f"unknown struct {name!r}")
        offset, max_align = 0, 1
        for f in sd.fields:
            size = self.type_size(f.ctype)
            align = 1 if sd.packed else min(size, 4)
            offset = (offset + align - 1) // align * align
            f.offset = offset
            offset += size * f.count; max_align = max(max_align, align)
        return offset if sd.packed else (offset + max_align - 1) // max_align * max_align

    def _ctype_key(self, name):
        return name.lower()

    def _symkey(self, name):
        raw = self._ctype_key(name)
        if (self._current_func and raw not in self._global_names
                and not raw.startswith("__arg") and raw not in ("__ret__", "__c_heap_top__")):
            return f"{self._current_func}::{raw}"
        return raw

    def _alloc(self, size) -> VReg:
        out = self.b.vreg()
        self.b.system(F_INT64, S_MOV64, out, self._heap_top, typecode=T_PTR)
        if isinstance(size, int):
            self.b.system(F_INT64, S_ADD64, self._heap_top, self._heap_top,
                          payload=size, typecode=T_SIZE)
        else:
            n = size if isinstance(size, (VReg, WideValue)) else self.eval(size)
            nx = n.lo if isinstance(n, WideValue) else self._x_from_r(n, T_SIZE)
            self.b.system(F_INT64, S_ADD64, self._heap_top, self._heap_top,
                          payload=nx, typecode=T_SIZE)
        return out

    def _addr_add(self, base: VReg, offset) -> VReg:
        if isinstance(offset, int) and offset == 0: return base
        out = self.b.vreg()
        if isinstance(offset, int):
            self.b.system(F_POINTER, S_PTR_ADD, out, base, payload=offset, typecode=T_PTR)
        else:
            ox = offset.lo if isinstance(offset, WideValue) else self._x_from_r(offset, T_SIZE)
            self.b.system(F_POINTER, S_PTR_ADD, out, base, payload=ox, typecode=T_PTR)
        return out

    def _x_from_r(self, reg: VReg, typecode=T_U64):
        out = self.b.vreg(); self.b.system(F_INT64, S_FROM_R32, out, reg, typecode=typecode)
        return out

    def _r_from_x(self, reg: VReg):
        out = self.b.vreg(); self.b.system(F_INT64, S_TO_R32, out, reg, typecode=T_U64)
        return out

    def _sys_type(self, ctype):
        if ctype and ctype.pointers: return T_PTR
        if ctype and ctype.name in ("size_t",): return T_SIZE
        if ctype and ctype.name in ("uint64_t",): return T_U64
        if ctype and ctype.name in ("int64_t",): return T_I64
        return T_U64

    def load_typed(self, addr: VReg, ctype: CType):
        size = self.type_size(ctype)
        op = {1: S_LOAD8, 2: S_LOAD16, 4: S_LOAD32, 8: S_LOAD64}[size]
        x = self.b.vreg(); self.b.system(F_MEMORY, op, x, addr, payload=0,
                                         typecode=self._sys_type(ctype))
        if size == 8: return WideValue(x)
        if ctype.pointers and not ctype.is_function_pointer: return x
        return self._r_from_x(x)

    def store_typed(self, addr: VReg, ctype: CType, value):
        size = self.type_size(ctype)
        if ctype.is_function_pointer:
            if isinstance(value, Var) and value.name.lower() in getattr(self, "_func_names", set()):
                rv = self._const(self._function_id(value.name))
            else:
                rv = self.eval(value) if not isinstance(value, VReg) else value
            x = self._x_from_r(rv, T_PTR)
            op = {1: S_STORE8, 2: S_STORE16, 4: S_STORE32, 8: S_STORE64}[size]
            self.b.system(F_MEMORY, op, x, addr, payload=0, typecode=T_PTR)
            return
        value = self.eval(value) if not isinstance(value, (VReg, WideValue)) else value
        if isinstance(value, WideValue): x = value.lo
        elif ctype.pointers: x = value
        else: x = self._x_from_r(value, self._sys_type(ctype))
        op = {1: S_STORE8, 2: S_STORE16, 4: S_STORE32, 8: S_STORE64}[size]
        self.b.system(F_MEMORY, op, x, addr, payload=0, typecode=self._sys_type(ctype))

    def as_wide(self, value):
        if isinstance(value, WideValue): return value
        if isinstance(value, Num):
            lo = self.b.vreg()
            self.b.system(F_INT64, S_MOVI64, lo, payload=value.value & 0xffffffff,
                          typecode=T_U64)
            high = (value.value >> 32) & 0xffffffff
            if high:
                hx = self.b.vreg(); self.b.system(F_INT64, S_MOVI64, hx, payload=high, typecode=T_U64)
                self.b.system(F_INT64, S_SHL64, hx, hx, payload=32, typecode=T_U64)
                merged = self.b.vreg(); self.b.system(F_INT64, S_OR64, merged, lo, payload=hx, typecode=T_U64)
                lo = merged
            return WideValue(lo)
        v = self.eval(value) if not isinstance(value, VReg) else value
        if isinstance(v, WideValue): return v
        return WideValue(self._x_from_r(v))

    def _zero_memory(self, addr: VReg, size):
        zero = self.b.vreg(); self.b.system(F_INT64, S_MOVI64, zero, payload=0, typecode=T_U64)
        if isinstance(size, int): payload = size
        else:
            n = size if isinstance(size, (VReg, WideValue)) else self.eval(size)
            payload = n.lo if isinstance(n, WideValue) else self._x_from_r(n, T_SIZE)
        self.b.system(F_MEMORY, S_MEMSET, addr, zero, payload=payload, typecode=T_SIZE)

    def _function_id(self, name):
        names = [f.name.lower() for f in self.funcs]
        try: return names.index(name.lower()) + 1
        except ValueError: raise SyntaxError(f"unknown callback function {name!r}")

    def expr_type(self, e) -> Optional[CType]:
        if isinstance(e, Var): return self.var_types.get(self._symkey(e.name))
        if isinstance(e, CastExpr): return e.ctype
        if isinstance(e, Bin):
            left, right = self.expr_type(e.lhs), self.expr_type(e.rhs)
            if left and (left.pointers or self.type_size(left) == 8): return left
            if right and self.type_size(right) == 8: return right
            return left or right
        if isinstance(e, AddressOf):
            t = self.expr_type(e.target) or CType("int")
            return CType(t.name, t.pointers + 1, t.const, t.func_params, t.volatile)
        if isinstance(e, Deref):
            t = self.expr_type(e.pointer)
            return CType(t.name, max(0, t.pointers - 1), t.const, t.func_params, t.volatile) if t else None
        if isinstance(e, IndexRef):
            t = self.expr_type(e.base)
            return CType(t.name, max(0, t.pointers - 1), t.const, t.func_params, t.volatile) if t else None
        if isinstance(e, (MemberRef, FieldRef)):
            base = e.base if isinstance(e, MemberRef) else Var(e.obj)
            bt = self.expr_type(base)
            if bt:
                name = bt.name[7:] if bt.name.startswith("struct ") else bt.name
                sd = self.structs.get(name)
                if sd:
                    field = e.field
                    for f in sd.fields:
                        if f.name == field:
                            if bt.const:
                                return CType(f.ctype.name, f.ctype.pointers, True,
                                             f.ctype.func_params, f.ctype.volatile)
                            return f.ctype
        return None

    def lvalue(self, e):
        if isinstance(e, Var):
            key = self._symkey(e.name); ct = self.var_types.get(key, CType("int"))
            if key not in self.memory_vars:
                # Address-taken scalar: promote once into memory.
                old = self.var(e.name); ptr = self._alloc(self.type_size(ct))
                self.store_typed(ptr, ct, old)
                self.b.system(F_INT64, S_MOV64, old, ptr, typecode=T_PTR)
                self.memory_vars.add(key)
            return self.var(e.name), ct
        if isinstance(e, Deref):
            return self.eval(e.pointer), self.expr_type(e) or CType("int")
        if isinstance(e, IndexRef):
            base = self.eval(e.base); ct = self.expr_type(e) or CType("int")
            idx = self.eval(e.index); scaled = idx
            size = self.type_size(ct)
            if size != 1:
                scaled = self.b.vreg(); self.b.arith("mul", scaled, idx, Imm(size))
            return self._addr_add(base, scaled), ct
        if isinstance(e, FieldRef):
            return self.lvalue(MemberRef(Var(e.obj), e.field, False))
        if isinstance(e, MemberRef):
            bt = self.expr_type(e.base)
            if bt is None: raise SyntaxError(f"cannot resolve member {e.field!r}")
            name = bt.name[7:] if bt.name.startswith("struct ") else bt.name
            sd = self.structs.get(name)
            if sd is None: raise SyntaxError(f"{bt.name!r} is not a struct")
            field = next((f for f in sd.fields if f.name == e.field), None)
            if field is None: raise SyntaxError(f"struct {name!r} has no field {e.field!r}")
            if e.through_pointer:
                base = self.eval(e.base)
            else:
                base, _ = self.lvalue(e.base)
            return self._addr_add(base, field.offset), field.ctype
        raise SyntaxError("expression is not an addressable lvalue")

    def lower_program(self, prog: List[object]) -> List:
        body = [s for s in prog if not isinstance(s, (Func, StructDef, TypedefDef))]
        self._global_names = {s.name.lower() for s in body if isinstance(s, Decl)}
        self.funcs = [s for s in prog if isinstance(s, Func)]
        self._func_names = {f.name.lower() for f in self.funcs}
        self._func_params = {f.name.lower(): (f.params or []) for f in self.funcs}
        self._func_param_types = {f.name.lower(): (f.param_types or []) for f in self.funcs}
        self.uses_heap = any(self._node_needs_heap(s) for s in prog)
        recursive = any(self._calls_name(f.body, f.name.lower()) for f in self.funcs)
        self.systems_mode = self.uses_heap or recursive
        if self.uses_heap:
            self.b.system(F_INT64, S_MOVI64, self._heap_top, payload=0x10000, typecode=T_PTR)
        for s in body:
            self.stmt(s)
        self.b.ret()
        for f in self.funcs:
            self.b.label(f"fn_{f.name.lower()}")
            old_frame = self._in_system_frame
            old_offsets = self._frame_offsets
            old_func = self._current_func
            self._current_func = f.name.lower()
            self._in_system_frame = self.systems_mode
            frame_size, self._frame_offsets = self._frame_layout(f)
            if self._in_system_frame:
                self.b.system(F_FRAME, S_FRAME_ENTER, payload=frame_size, typecode=0)
            # bind parameters: read from arg-passing regs into named locals
            for i, p in enumerate(f.params or []):
                pv = self.var(p)
                av = self.var(f"__arg{i}__")
                self.b.mov(pv, av)
            for s in f.body:
                self.stmt(s)
            if self._in_system_frame:
                self._emit_frame_leave()
            self.b.ret()
            self._in_system_frame = old_frame
            self._frame_offsets = old_offsets
            self._current_func = old_func
        return self.b.insts

    def _walk(self, node):
        if is_dataclass(node):
            yield node
            for f in fields(node):
                yield from self._walk(getattr(node, f.name))
        elif isinstance(node, (list, tuple)):
            for item in node: yield from self._walk(item)

    def _calls_name(self, node, name):
        return any(isinstance(x, Call) and x.ns is None and x.method.lower() == name
                   for x in self._walk(node))

    def _node_needs_heap(self, node):
        for x in self._walk(node):
            if isinstance(x, (StructDef, AddressOf, Deref, IndexRef, MemberRef, SizeofExpr)):
                return True
            if isinstance(x, Decl) and x.ctype and (x.count != 1 or x.ctype.pointers
                    or x.ctype.name.startswith("struct ") or self.type_size(x.ctype) == 8
                    or x.ctype.volatile):
                return True
            if isinstance(x, Call) and x.ns is None and x.method.lower() in ("malloc", "calloc", "free"):
                return True
        return False

    def _frame_layout(self, func):
        decls = {x.name.lower(): x for x in self._walk(func.body) if isinstance(x, Decl)}
        addressed = {x.target.name.lower() for x in self._walk(func.body)
                     if isinstance(x, AddressOf) and isinstance(x.target, Var)}
        offset, layout = 0, {}
        for name, d in decls.items():
            if not d.ctype: continue
            memory = (d.count != 1 or d.ctype.name.startswith("struct ")
                      or self.type_size(d.ctype) == 8 or d.ctype.volatile or name in addressed)
            if not memory: continue
            size = self.type_size(d.ctype) * d.count
            align = min(self.type_size(d.ctype), 8)
            offset = (offset + align - 1) // align * align
            layout[name] = offset; offset += size
        return (offset + 7) // 8 * 8, layout

    def _emit_frame_leave(self):
        ret = self.var("__ret__")
        payload = self._heap_top if self.uses_heap else 0
        self.b.system(F_FRAME, S_FRAME_LEAVE, ret, payload=payload, typecode=1)

    # -- variables -------------------------------------------------------
    def var(self, name: str) -> VReg:
        key = self._symkey(name)                    # variables: case-insensitive, function-scoped
        v = self.vars.get(key)
        if v is None:
            # Function-scoped C locals become real native locals.  On bytecode
            # targets FRAME_ENTER/LEAVE preserves their allocated R/X slots.
            v = VReg(name, pinned="::" not in key)
            self.vars[key] = v
        return v

    # -- statements ------------------------------------------------------
    def stmt(self, s):
        p = getattr(s, "pos", -1)
        if p is not None and p >= 0:
            self.b.cur_pos = p           # INV-25: attribute emitted IL to this statement
        if isinstance(s, Decl):
            v = self.var(s.name)
            sym = self._symkey(s.name); raw = self._ctype_key(s.name)
            if s.ctype is not None:
                self.var_types[sym] = s.ctype
                self.var_counts[sym] = s.count
                if s.ctype.const: self.const_vars.add(sym)
            memory = (s.count != 1 or (s.ctype and (s.ctype.name.startswith("struct ")
                      or self.type_size(s.ctype) == 8 or s.ctype.volatile)) or raw in self._frame_offsets)
            if memory:
                self.memory_vars.add(sym)
                if raw in self._frame_offsets:
                    self.b.system(F_FRAME, S_ADDR_LOCAL, v,
                                  payload=self._frame_offsets[raw], typecode=T_PTR)
                else:
                    allocated = self._alloc(self.type_size(s.ctype) * s.count)
                    self.b.system(F_INT64, S_MOV64, v, allocated, typecode=T_PTR)
                if s.init is not None:
                    self.store_typed(v, s.ctype, s.init)
                else:
                    self._zero_memory(v, self.type_size(s.ctype) * s.count)
            elif s.init is not None:
                if s.ctype and s.ctype.is_function_pointer and isinstance(s.init, Var):
                    self.b.const(v, self._function_id(s.init.name))
                elif s.ctype and s.ctype.pointers:
                    value = self.eval(s.init)
                    value = value.lo if isinstance(value, WideValue) else value
                    self.b.system(F_INT64, S_MOV64, v, value, typecode=T_PTR)
                else:
                    self.assign_to(v, s.init)
            else:
                self.b.const(v, 0)
        elif isinstance(s, ConstDecl):
            self.const_vars.add(self._symkey(s.name))
            self._define_constant(s.name, s.value)
        elif isinstance(s, EnumDecl):
            self._define_enum(s.enum_name, s.members)
        elif isinstance(s, Assign):
            sym = self._symkey(s.name)
            if sym in self.const_vars:
                raise SyntaxError(f"assignment to const variable {s.name!r}")
            if sym in self.memory_vars:
                self.store_typed(self.var(s.name), self.var_types[sym], s.value)
            else:
                self.assign_to(self.var(s.name), s.value)
        elif isinstance(s, Store):
            addr, ctype = self.lvalue(s.target)
            if ctype.const: raise SyntaxError("assignment through const lvalue")
            self.store_typed(addr, ctype, s.value)
        elif isinstance(s, FieldAssign):
            if self.expr_type(Var(s.obj)) and self.expr_type(FieldRef(s.obj, s.field)):
                addr, ctype = self.lvalue(FieldRef(s.obj, s.field))
                if ctype.const: raise SyntaxError("assignment through const lvalue")
                self.store_typed(addr, ctype, s.value)
            else:
                self.assign_field(s.obj, s.field, s.value)
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
            if self._in_system_frame:
                self._emit_frame_leave()
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
        elif isinstance(s, ServerMain):
            for st in s.body:
                self.stmt(st)
        elif isinstance(s, TryCatch):
            self.lower_try(s)
        elif isinstance(s, Raise):
            # See docs/EXCEPTION_ENGINE.md: Error.Raise(code) jumps to the
            # nearest Error.SetHandler'd handler (an enclosing lower_try), or
            # propagates as a real uncaught PicoFault if none is active.
            v = self.eval(s.value) if s.value is not None else self._const(0)
            ok = self.b.vreg()
            self.b.host("Error", "Raise", (v,), ok)
        elif isinstance(s, OnBlock):
            self.lower_on_block(s)
        else:
            raise SyntaxError(f"cannot lower statement {s}")

    def _const(self, value: int) -> VReg:
        v = self.b.vreg()
        self.b.const(v, value)
        return v

    def lower_try(self, s: TryCatch):
        """try { } catch { } [finally { }] -- a structured `trycatch` IL node
        (see ILBuilder.trycatch / docs/EXCEPTION_ENGINE.md), mirroring
        picoscript_basic.py's lower_try exactly (cfront has its own,
        independent AST + Lowerer, but shares the same picoscript_il
        ILBuilder, so the same structured-node mechanism applies here too --
        just re-expressed against cfront's own node/statement dispatch)."""
        self.b.trycatch(
            lambda: [self.stmt(st) for st in s.try_body],
            lambda: [self.stmt(st) for st in s.catch_body],
            (lambda: [self.stmt(st) for st in s.finally_body]) if s.finally_body else None,
        )

    def lower_on_block(self, s: OnBlock):
        """on Ns.Method { body } -- an inline drain-and-dispatch loop over
        pending Event.* queue entries; see docs/EVENTING.md and
        picoscript_basic.py's lower_on_block, which this mirrors exactly
        (same event_type_hash, imported from picoscript_basic rather than
        re-derived, so the SAME compile-time hash matches an ON block
        declared in ANY frontend, including this one)."""
        type_code = event_type_hash(s.event_ns, s.event_method)
        idx = self.b.vreg("__on_i__")
        cnt = self.b.vreg("__on_cnt__")
        self.b.host("Event", "Count", (), cnt)
        self.b.const(idx, 0)
        top = self.b.new_label("on"); cont = self.b.new_label("oncont")
        end = self.b.new_label("endon")
        self.b.label(top)
        self.b.cmpbr("GE", idx, cnt, end)
        evid = self.var("__event__")
        self.b.host("Event", "Next", (), evid)
        skip = self.b.new_label("onskip")
        etype = self.b.vreg("__on_type__")
        self.b.host("Event", "Type", (evid,), etype)
        typeconst = self.b.vreg("__on_typeconst__")
        self.b.const(typeconst, type_code)
        self.b.cmpbr("NE", etype, typeconst, skip)
        self.loop_stack.append((cont, end))
        for st in s.body:
            self.stmt(st)
        self.loop_stack.pop()
        self.b.label(skip)
        self.b.label(cont)
        self.b.inc(idx)
        self.b.jmp(top)
        self.b.label(end)

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
        if isinstance(expr, FieldRef):
            cv = self._resolve_constant(f"{expr.obj}.{expr.field}")
            if cv is None:
                raise SyntaxError(f"unknown constant {expr.obj}.{expr.field!r} in constant expression")
            return int(cv)
        if isinstance(expr, Unary):
            if expr.op == "-":
                return -self._eval_const_expr(expr.operand)
            if expr.op == "~":
                return ~(self._eval_const_expr(expr.operand))
            raise SyntaxError(f"unsupported unary op {expr.op!r} in constant expression")
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
            if expr.op == "%":
                if b == 0:
                    raise SyntaxError("modulo by zero in constant expression")
                return a - int(a / b) * b
            if expr.op == "&":
                return a & b
            if expr.op == "|":
                return a | b
            if expr.op == "^":
                return a ^ b
            if expr.op == "<<":
                return a << (b & 31)
            if expr.op == ">>":
                return a >> (b & 31)
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

    def assign_field(self, obj: str, field: str, expr):
        card = self.var(obj)
        self.b.host("Storage", "EditCard", (card,), None)
        name = self.emit_str_span(field)
        if isinstance(expr, Str):
            val = self.emit_str_span(expr.value)
            self.b.host("Storage", "SetFieldStr", (name, val), None)
        else:
            val = self.eval(expr)
            self.b.host("Storage", "SetField", (name, val), None)

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
            lt, rt = self.expr_type(cond.lhs), self.expr_type(cond.rhs)
            if ((lt and self.type_size(lt) == 8) or (rt and self.type_size(rt) == 8)
                    or self.eval_literal_wide(cond.lhs) or self.eval_literal_wide(cond.rhs)):
                v = self.eval(cond); self.b.cmpbr("Z", v, v, false_label); return
            a = self.eval(cond.lhs)
            b = self.eval(cond.rhs)
            self.b.cmpbr(COND_NEGATE[_CMP_OPS[cond.op]], a, b, false_label)
            return
        v = self.eval(cond)
        self.b.cmpbr("Z", v, v, false_label)   # if v == 0 -> false

    # -- expressions -----------------------------------------------------
    def eval(self, e, want_value: bool = True) -> Optional[VReg]:
        if isinstance(e, Num):
            if e.value > 0xffffffff or e.value < -0x80000000:
                return self.as_wide(e)
            v = self.b.vreg(); self.b.const(v, e.value); return v
        if isinstance(e, Var):
            cv = self._resolve_constant(e.name)
            if cv is not None:
                v = self.b.vreg(); self.b.const(v, cv); return v
            key = self._symkey(e.name)
            if key in self.memory_vars:
                ct = self.var_types[key]
                if self.var_counts.get(key, 1) != 1 or ct.name.startswith("struct "):
                    return self.var(e.name)       # array/struct decay to its address
                return self.load_typed(self.var(e.name), ct)
            return self.var(e.name)
        if isinstance(e, Bin):
            lt, rt = self.expr_type(e.lhs), self.expr_type(e.rhs)
            if e.op in ("+", "-") and lt and lt.pointers and not (rt and rt.pointers):
                a = self.eval(e.lhs); b = self.eval(e.rhs)
                if isinstance(a, WideValue): a = a.lo
                scale = self.type_size(CType(lt.name, lt.pointers - 1, lt.const, lt.func_params, lt.volatile))
                if scale != 1:
                    scaled = self.b.vreg(); self.b.arith("mul", scaled, b, Imm(scale)); b = scaled
                bx = self._x_from_r(b, T_SIZE)
                dst = self.b.vreg(); self.b.system(F_INT64, S_ADD64 if e.op == "+" else S_SUB64,
                                                    dst, a, payload=bx, typecode=T_PTR)
                return dst
            if ((lt and self.type_size(lt) == 8) or (rt and self.type_size(rt) == 8)
                    or self.eval_literal_wide(e.lhs) or self.eval_literal_wide(e.rhs)):
                if e.op in _CMP_OPS:
                    return self.eval_wide_compare(e.op, e.lhs, e.rhs, lt or rt)
                if e.op in ("+", "-", "*", "/", "&", "|", "^", "<<", ">>"):
                    return self.eval_wide_arith(e.op, e.lhs, e.rhs)
            if e.op in _CMP_OPS:
                return self.eval_bool(e)
            if e.op in ("&&", "||"):
                return self.eval_logical(e)
            if e.op == "%":
                return self.eval_mod(e.lhs, e.rhs)
            if e.op in ("&", "|", "^", "<<", ">>"):
                a = self.eval(e.lhs)
                b = self.eval(e.rhs)
                dst = self.b.vreg()
                method = {"&": "And", "|": "Or", "^": "Xor",
                          "<<": "Shl", ">>": "Sar"}[e.op]
                self.b.host("Bits", method, (a, b), dst)
                return dst
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
            if e.op == "~":
                inner = self.eval(e.operand)
                dst = self.b.vreg()
                self.b.host("Bits", "Not", (inner,), dst)
                return dst
        if isinstance(e, Call):
            return self.lower_call(e, want_value)
        if isinstance(e, Invoke):
            target = self.eval(e.callee)
            if isinstance(target, WideValue): target = self._r_from_x(target.lo)
            return self.lower_indirect_target(target, e.args, want_value)
        if isinstance(e, CastExpr):
            source_type = self.expr_type(e.value)
            value = self.eval(e.value)
            if e.ctype.pointers:
                if isinstance(value, WideValue): return value.lo
                if source_type and source_type.pointers: return value
                return self._x_from_r(value, T_PTR)
            if self.type_size(e.ctype) == 8:
                return value if isinstance(value, WideValue) else WideValue(
                    value if source_type and source_type.pointers else self._x_from_r(value,
                        T_I64 if e.ctype.name == "int64_t" else T_U64))
            if isinstance(value, WideValue): value = self._r_from_x(value.lo)
            elif source_type and source_type.pointers: value = self._r_from_x(value)
            size = self.type_size(e.ctype)
            if size < 4:
                masked = self.b.vreg()
                self.b.host("Bits", "And", (value, self._const((1 << (size * 8)) - 1)), masked)
                value = masked
            return value
        if isinstance(e, AddressOf):
            if isinstance(e.target, Var) and e.target.name.lower() in getattr(self, "_func_names", set()):
                return self._const(self._function_id(e.target.name))
            return self.lvalue(e.target)[0]
        if isinstance(e, Deref):
            addr, ct = self.lvalue(e); return self.load_typed(addr, ct)
        if isinstance(e, IndexRef):
            addr, ct = self.lvalue(e); return self.load_typed(addr, ct)
        if isinstance(e, MemberRef):
            addr, ct = self.lvalue(e); return self.load_typed(addr, ct)
        if isinstance(e, SizeofExpr):
            ct = e.target if e.is_type else self.expr_type(e.target)
            count = (self.var_counts.get(self._symkey(e.target.name), 1)
                     if isinstance(e.target, Var) else 1)
            return self._const(self.type_size(ct) * count)
        if isinstance(e, Str):
            return self.emit_str_span(e.value)
        if isinstance(e, FieldRef):
            if self.expr_type(e) is not None:
                addr, ct = self.lvalue(e); return self.load_typed(addr, ct)
            cv = self._resolve_constant(f"{e.obj}.{e.field}")
            if cv is not None:
                v = self.b.vreg(); self.b.const(v, cv); return v
            card = self.var(e.obj)
            self.b.host("Storage", "EditCard", (card,), None)
            name = self.emit_str_span(e.field)
            dst = self.b.vreg()
            self.b.host("Storage", "GetField", (name,), dst)
            return dst
        if isinstance(e, _RawVReg):
            return e.v
        raise SyntaxError(f"cannot evaluate {e}")

    def eval_literal_wide(self, e):
        return isinstance(e, Num) and (e.value > 0xffffffff or e.value < -0x80000000)

    def eval_wide_arith(self, op, lhs, rhs):
        a, b = self.as_wide(lhs), self.as_wide(rhs)
        out = self.b.vreg()
        sysop = {"+": S_ADD64, "-": S_SUB64, "*": S_MUL64, "/": S_DIV64,
                 "&": S_AND64, "|": S_OR64, "^": S_XOR64,
                 "<<": S_SHL64, ">>": S_SHR64}[op]
        self.b.system(F_INT64, sysop, out, a.lo, payload=b.lo, typecode=T_U64)
        return WideValue(out)

    def eval_wide_compare(self, op, lhs, rhs, ctype=None):
        a, b = self.as_wide(lhs), self.as_wide(rhs)
        out = self.b.vreg()
        sysop = {"==": S_EQ64, "!=": S_NE64, "<": S_LT64, ">": S_GT64,
                 "<=": S_LE64, ">=": S_GE64}[op]
        self.b.system(F_INT64, sysop, out, a.lo, payload=b.lo,
                      typecode=T_I64 if ctype and ctype.name == "int64_t" else T_U64)
        return out

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
            try:
                addr, ct = self.lvalue(e.target)
            except SyntaxError as exc:
                raise SyntaxError("++/-- requires a variable or addressable lvalue") from exc
            if ct.const: raise SyntaxError("increment/decrement through const lvalue")
            old = self.load_typed(addr, ct)
            if isinstance(old, WideValue): raise SyntaxError("wide ++/-- is not yet supported")
            new = self.b.vreg(); self.b.arith("add" if e.op == "++" else "sub", new, old, Imm(1))
            self.store_typed(addr, ct, new)
            return new if e.prefix else old
        v = self.var(e.target.name)
        if self._symkey(e.target.name) in self.const_vars:
            raise SyntaxError(f"increment/decrement of const variable {e.target.name!r}")
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
            key = method.lower()
            if key == "malloc":
                if len(c.args) != 1: raise SyntaxError("malloc requires one size argument")
                return self._alloc(c.args[0])
            if key == "calloc":
                if len(c.args) != 2: raise SyntaxError("calloc requires count and size")
                count = self.eval(c.args[0]); unit = self.eval(c.args[1]); size = self.b.vreg()
                self.b.arith("mul", size, count, unit)
                ptr = self._alloc(size); self._zero_memory(ptr, size); return ptr
            if key == "free":
                # PicoScript's deterministic C heap is a bounded bump arena.
                # Individual free is intentionally a safe no-op; its storage is
                # reclaimed when the program/VM arena is reset.
                if len(c.args) != 1: raise SyntaxError("free requires one pointer")
                self.eval(c.args[0])
                return self._const(0) if want_value else None
            if key in C_ALIASES and key not in getattr(self, "_func_names", set()):
                a_ns, a_m = C_ALIASES[key]
                return self.lower_call(Call(a_ns, a_m, c.args), want_value)
            sym = self._symkey(key)
            if key not in getattr(self, "_func_names", set()) and sym in self.var_types \
                    and self.var_types[sym].is_function_pointer:
                return self.lower_indirect_call(key, c.args, want_value)
            return self.emit_direct_call(key, c.args, want_value)
        # Net.*  (namespace + method case-insensitive)
        if ns.upper() == "NET":
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
                # Raw/client Net methods are ordinary host hooks. Only the
                # HTTP response-control subset uses the compact core encoding.
                pass
            if m in ("STATUS", "TYPE", "BODY", "CLOSE", "HEADER"):
                return None
            cns, cm = canon_host(ns, method)
            if (cns, cm) not in HOST_HOOK_CODES:
                raise SyntaxError(f"unknown {ns}.{method}")
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
        # Typed systems view over the portable two-word Block size ABI.
        # Providers keep returning low/high R values; PicoScript-C exposes one
        # native U64 X value without widening the Block binding itself.
        if ns.upper() == "BLOCK" and method.upper() == "SIZE":
            if c.args: raise SyntaxError("Block.Size takes no arguments")
            lo_r, hi_r = self.b.vreg(), self.b.vreg()
            self.b.host("Block", "SizeLow", (), lo_r)
            self.b.host("Block", "SizeHigh", (), hi_r)
            lo, hi = self._x_from_r(lo_r), self._x_from_r(hi_r)
            self.b.system(F_INT64, S_SHL64, hi, hi, payload=32, typecode=T_U64)
            out = self.b.vreg()
            self.b.system(F_INT64, S_OR64, out, lo, payload=hi, typecode=T_U64)
            return WideValue(out)
        # Active-record storage sugar. These do not add VM hooks; they lower to
        # the existing UsePack/EditCard/SetField/GetField/QueryCard primitives.
        if ns.upper() == "STORAGE" and method.upper() == "GETCARD":
            pack = self.eval(c.args[0])
            card = self.eval(c.args[1])
            self.b.host("Storage", "UsePack", (pack,), None)
            dst = self.b.vreg() if want_value else None
            self.b.host("Storage", "EditCard", (card,), dst)
            return dst
        if ns.upper() == "STORAGE" and method.upper() == "SAVECARD":
            card = self.eval(c.args[0])
            self.b.host("Storage", "EditCard", (card,), None)
            dst = self.b.vreg() if want_value else None
            if dst is not None:
                self.b.const(dst, 1)
            return dst
        if ns.upper() == "STORAGE" and method.upper() == "QUERYCARDS":
            pack = self.eval(c.args[0])
            query = self.eval(c.args[1])
            self.b.host("Storage", "UsePack", (pack,), None)
            dst = self.b.vreg() if want_value else None
            self.b.host("Storage", "QueryCard", (query,), dst)
            return dst
        # generic host hook: resolve to canonical ABI spelling (case-insensitive)
        ns, method = canon_host(ns, method)
        if ns == "CatQ" and method == "CalibrateTarget" and len(c.args) != 3:
            raise SyntaxError("CatQ.CalibrateTarget requires 3 args (input, target, options)")
        arg_limit = 3 if ns == "CatQ" and method == "CalibrateTarget" else 2
        argregs = [self.eval(a) for a in c.args[:arg_limit]]
        dst = self.b.vreg() if want_value else None
        self.b.host(ns, method, tuple(argregs), dst)
        return dst

    def emit_direct_call(self, key, args, want_value):
        if key not in getattr(self, "_func_names", set()):
            raise SyntaxError(f"unknown function {key!r}")
        # Stage every argument before touching shared ABI slots. This makes
        # nested calls such as f(g(x), h(y)) deterministic and re-entrant.
        staged = [self.eval(arg) for arg in args]
        for i, value in enumerate(staged):
            if isinstance(value, WideValue):
                raise SyntaxError("uint64_t function arguments require pointer passing")
            self.b.mov(self.var(f"__arg{i}__"), value)
        self.b.call(f"fn_{key}")
        if want_value:
            out = self.b.vreg(); self.b.mov(out, self.var("__ret__")); return out
        return None

    def lower_indirect_call(self, var_name, args, want_value):
        return self.lower_indirect_target(self.var(var_name), args, want_value)

    def lower_indirect_target(self, target, args, want_value):
        candidates = [f for f in self.funcs
                      if len(f.params or []) == len(args)]
        if not candidates: raise SyntaxError("callback has no compatible target")
        staged = [self.eval(arg) for arg in args]
        out = self.b.vreg() if want_value else None
        end = self.b.new_label("cbend")
        for f in candidates:
            nxt = self.b.new_label("cbnext")
            self.b.cmpbr("NE", target, self._const(self._function_id(f.name)), nxt)
            for i, value in enumerate(staged):
                self.b.mov(self.var(f"__arg{i}__"), value)
            self.b.call(f"fn_{f.name.lower()}")
            if out is not None: self.b.mov(out, self.var("__ret__"))
            self.b.jmp(end); self.b.label(nxt)
        if out is not None: self.b.const(out, 0)
        self.b.label(end)
        return out

    def emit_str_span(self, text: str):
        """Materialize a string literal as a span over its interned constant-pool
        bytes. Identical literals share one stable address (dedup); distinct ones
        never overlap, so any number can be live at once. Bytes are (re)written at
        the literal's fixed address before the span is made, so it is correct even
        when the first textual occurrence is inside a skipped branch or a loop."""
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

def compile_c(source: str, *, source_path: Optional[str] = None,
              include_resolver=None, defines: Optional[Dict[str, object]] = None):
    """C-syntax source -> PicoIL instruction list."""
    source = preprocess_c(source, source_path=source_path,
                          include_resolver=include_resolver, defines=defines)
    toks = tokenize(source)
    parser = Parser(toks)
    prog = parser.parse_program()
    return Lowerer(parser.structs, parser.typedefs).lower_program(prog)
