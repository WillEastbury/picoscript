#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_ast.py -- AST-JSON frontend for PicoScript (spike/ast-json-dialect).

Every other frontend (C-syntax, BASIC, Python-style, English, ...) exists to
turn *text* into the shared AST that `picoscript_basic.Lowerer` consumes. This
module skips the text step entirely: the "source" IS a JSON serialization of
that same AST, so tools that already hold a structured program in memory --
the Workflow/Schema/Ontology/Report designers, or an LLM/agent -- can hand the
Lowerer a tree directly instead of synthesizing and re-parsing a surface
language.

Because it reuses the exact dataclasses and Lowerer that BASIC/Python/English
share, a program expressed as AST-JSON lowers through the identical pipeline
and produces byte-identical bytecode to the same program written in any other
dialect (see tests/test_ast_frontend.py for a parity spike).

JSON shape
----------
Each AST node is a JSON object: {"node": "<ClassName>", <field>: <value>, ...}
A program (or any statement-list field, e.g. an `If` arm's body) is a plain
JSON array of node objects. Scalars (int/str/bool/None) pass through as-is.

Public API
----------
    ast_to_json(node)      AST node/program -> plain JSON-able dict/list
    json_to_ast(data)      plain dict/list -> AST node/program (inverse)
    compile_ast(source)    JSON text -> PicoIL (json.loads -> json_to_ast -> Lowerer)
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from typing import Any

from picoscript_basic import (
    Num, Str, Var, Bin, Cmp, Call, Let, Dim, IncDec, Ternary, If, While,
    DoLoop, ForTo, ForEach, Switch, Dispatch, Goto, Label, Gosub, Sub,
    ServerMain, Return, Break, Skip, Print, CallStmt, TryExcept, Raise,
    OnBlock, ConstDecl, EnumDecl, Lowerer,
)

# Every AST node class the (de)serializer understands, keyed by class name.
_NODE_CLASSES = {
    cls.__name__: cls
    for cls in (
        Num, Str, Var, Bin, Cmp, Call, Let, Dim, IncDec, Ternary, If, While,
        DoLoop, ForTo, ForEach, Switch, Dispatch, Goto, Label, Gosub, Sub,
        ServerMain, Return, Break, Skip, Print, CallStmt, TryExcept, Raise,
        OnBlock, ConstDecl, EnumDecl,
    )
}


def ast_to_json(node: Any) -> Any:
    """Recursively convert an AST node (or list/scalar) to plain JSON data."""
    if node is None or isinstance(node, (int, float, str, bool)):
        return node
    if isinstance(node, (list, tuple)):
        return [ast_to_json(n) for n in node]
    if is_dataclass(node):
        out = {"node": type(node).__name__}
        for f in fields(node):
            out[f.name] = ast_to_json(getattr(node, f.name))
        return out
    raise TypeError(f"cannot serialize AST node of type {type(node)!r}")


def json_to_ast(data: Any) -> Any:
    """Recursively rebuild AST node(s) from data produced by ast_to_json.

    Tuple-typed fields (e.g. `If.arms`, `Switch.cases`, `EnumDecl.members`)
    round-trip as lists rather than tuples -- the Lowerer only ever unpacks
    them (`for cond, body in node.arms`), which works identically for either.

    Unknown fields are dropped rather than raising: the JS port
    (`vm/picoc.js`) attaches a `pos` (source offset) to some statement nodes
    for error reporting that these Python dataclasses don't carry, and
    AST-JSON emitted by the JS side (e.g. via `translate(..., "ast")`)
    should still compile here unmodified.
    """
    if data is None or isinstance(data, (int, float, str, bool)):
        return data
    if isinstance(data, list):
        return [json_to_ast(d) for d in data]
    if isinstance(data, dict):
        kind = data.get("node")
        cls = _NODE_CLASSES.get(kind)
        if cls is None:
            raise ValueError(f"unknown/missing AST node kind: {kind!r}")
        valid_fields = {f.name for f in fields(cls)}
        kwargs = {k: json_to_ast(v) for k, v in data.items() if k in valid_fields}
        return cls(**kwargs)
    raise TypeError(f"cannot deserialize AST data of type {type(data)!r}")


def compile_ast(source: str):
    """AST-as-JSON source -> PicoIL instruction list.

    `source` is JSON text for a top-level statement array (the same shape
    `picoscript_basic.Parser.parse_program()` returns before lowering).
    """
    prog = json_to_ast(json.loads(source))
    return Lowerer().lower_program(prog)
