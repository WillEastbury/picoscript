#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_workflow_ast_bridge.py -- Workflow <-> AST-JSON bridge.

Closes the loop identified while building the visual AST designer
(docs/ast_designer_spike.html): Workflow is a deliberately flat, externally-
constrained step list (docs/WORKFLOW_DIALECT.md), so anything it can't
express natively should still be reachable by escaping onto the full AST.

`picoscript_workflow.workflow_to_ast` stops one stage before
`compile_workflow` (which goes steps -> English -> PicoIL) and returns the
parsed AST instead of lowering it, so a workflow can be:
  - inspected/edited as a structural tree (via picoscript_ast.ast_to_json),
  - and converted back to bytecode via picoscript_ast.compile_ast,
producing byte-identical bytecode to compiling the workflow directly.

Also exercises `picoscript_build.to_ast_json` / `emit --as ast`, which uses
the same bridge for the CLI.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_workflow import compile_workflow, workflow_to_ast  # noqa: E402
from picoscript_ast import ast_to_json, compile_ast  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402
from picoscript_build import to_ast_json, detect_lang  # noqa: E402

WORKFLOW_SRC = json.dumps([
    {"type": "SET", "name": "sum", "value": 0},
    {"type": "FOREACH", "var": "item", "in": [10, 20, 30]},
    {"type": "SET", "name": "sum", "expr": "sum + item"},
    {"type": "END"},
    {"type": "LOG", "message": "sum"},
])


def _s32(v: int) -> int:
    return v - 0x100000000 if v & 0x80000000 else v


def test_workflow_to_ast_matches_compile_workflow_bytecode():
    direct_words = lower_to_bytecode_safe(compile_workflow(WORKFLOW_SRC))
    prog, warnings = workflow_to_ast(WORKFLOW_SRC)
    assert warnings == []
    via_ast_words = lower_to_bytecode_safe(compile_ast(json.dumps(ast_to_json(prog))))
    assert via_ast_words == direct_words


def test_workflow_to_ast_runs_and_produces_expected_output():
    prog, _warnings = workflow_to_ast(WORKFLOW_SRC)
    words = lower_to_bytecode_safe(compile_ast(json.dumps(ast_to_json(prog))))
    vm = PicoVM().run(words)
    output = [_s32(int.from_bytes(chunk, "big")) for chunk in vm.output]
    assert output == [60]  # 10 + 20 + 30


def test_build_to_ast_json_for_workflow_matches_direct_bridge():
    assert detect_lang("x.wf", None) == "workflow"
    cli_json = to_ast_json(WORKFLOW_SRC, "workflow")
    prog, _warnings = workflow_to_ast(WORKFLOW_SRC)
    assert json.loads(cli_json) == ast_to_json(prog)


def test_build_to_ast_json_reformats_ast_source():
    prog, _warnings = workflow_to_ast(WORKFLOW_SRC)
    ast_json = json.dumps(ast_to_json(prog))
    reformatted = to_ast_json(ast_json, "ast")
    assert json.loads(reformatted) == json.loads(ast_json)


def test_build_to_ast_json_rejects_ast_incapable_lang():
    import pytest
    with pytest.raises(ValueError):
        to_ast_json("int x = 1;", "c")
