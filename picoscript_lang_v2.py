#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PicoScript v2: Case-insensitive, block-structured language

New syntax features:
- Case-insensitive keywords and variable names
- Whitespace-ignorant parsing
- Explicit block delimiters: IF/THEN/ELSE/ENDIF, FOREACH/IN/ENDFOREACH, WHILE/ENDWHILE, etc.
- CRLF line endings, no semicolons or curly brackets
- New namespaces: String.*, Number.*, Maths.*, DateTime.*, Locale.*

Example:

    IF R0 EQ 42 THEN
        String.Concat(R1, R2, R3)
        Number.Format(R4, R3, 2)
    ELSE
        Maths.Sqrt(R5, R6)
    ENDIF

    FOREACH item AS i IN items
        DateTime.GetNow(R7)
        Locale.Format(R8, R7, "en_US")
    ENDFOREACH

    WHILE R9 LT 100
        Maths.Add(R9, R9, 1)
    ENDWHILE
"""

import re
from enum import IntEnum
from typing import Optional, Tuple, List, Dict, Any


# ═══════════════════════════════════════════════════════════════════════
# Instruction encoding (from v1, stable)
# ═══════════════════════════════════════════════════════════════════════

OP_NOOP   = 0x0
OP_LOAD   = 0x1
OP_SAVE   = 0x2
OP_PIPE   = 0x3
OP_ADD    = 0x4
OP_SUB    = 0x5
OP_MUL    = 0x6
OP_DIV    = 0x7
OP_INC    = 0x8
OP_JUMP   = 0x9
OP_BRANCH = 0xA
OP_CALL   = 0xB
OP_RETURN = 0xC
OP_WAIT   = 0xD
OP_RAISE  = 0xE
OP_DSP    = 0xF

# Namespace → opcode mapping (v2 extended)
NAMESPACE_MAP = {
    "Storage": {"Load": OP_LOAD, "Save": OP_SAVE, "Pipe": OP_PIPE,
                "GetSchemaForPack": OP_NOOP, "SetSchemaForPack": OP_NOOP,
                "AddCard": OP_NOOP, "UpdateCard": OP_NOOP, "DeleteCard": OP_NOOP,
                "PatchCard": OP_NOOP, "ReadCard": OP_NOOP, "QueryCard": OP_NOOP},
    "Thread": {"Skip": OP_NOOP, "Wait": OP_WAIT, "Raise": OP_RAISE, "YieldCounted": OP_NOOP},
    "Math": {"Add": OP_ADD, "Sub": OP_SUB, "Mul": OP_MUL, "Div": OP_DIV, "Inc": OP_INC},
    "Flow": {"Jump": OP_JUMP, "Branch": OP_BRANCH, "Call": OP_CALL, "Return": OP_RETURN},
    "Net": {"Status": OP_NOOP, "Header": OP_NOOP, "Type": OP_NOOP,
            "Body": OP_NOOP, "Close": OP_NOOP},
    "Kernel": {"WaitIRQ": OP_NOOP, "WaitSWIRQ": OP_NOOP, "FireSWIRQ": OP_NOOP,
               "ProfileStart": OP_NOOP, "ProfileEnd": OP_NOOP, "TracePoint": OP_NOOP},
    "Queue": {"Dequeue": OP_NOOP, "Enqueue": OP_NOOP, "Depth": OP_NOOP,
              "DequeueBatch": OP_NOOP, "EnqueueBatch": OP_NOOP},
    "Random": {"U32": OP_NOOP},
    "Memory": {"ArenaInit": OP_NOOP, "ArenaAlloc": OP_NOOP,
               "ArenaReset": OP_NOOP, "ArenaStats": OP_NOOP},
    "Span": {"Make": OP_NOOP, "Slice": OP_NOOP},
    "Descriptor": {"Make": OP_NOOP, "SetFlags": OP_NOOP, "GetPtr": OP_NOOP,
                   "GetLen": OP_NOOP, "GetFlags": OP_NOOP, "CopyBatch": OP_NOOP},
    "Lease": {"Acquire": OP_NOOP, "Release": OP_NOOP, "Validate": OP_NOOP,
              "CachedValidate": OP_NOOP, "GetSpan": OP_NOOP, "GetTypeHint": OP_NOOP},
    # NEW: String, Number, Maths, DateTime, Locale
    "String": {"Concat": OP_NOOP, "Length": OP_NOOP, "Substring": OP_NOOP,
               "IndexOf": OP_NOOP, "Split": OP_NOOP, "Trim": OP_NOOP,
               "ToUpper": OP_NOOP, "ToLower": OP_NOOP, "Replace": OP_NOOP,
               "Format": OP_NOOP, "Parse": OP_NOOP, "Equals": OP_NOOP},
    "Number": {"Parse": OP_NOOP, "Format": OP_NOOP, "Round": OP_NOOP,
               "Floor": OP_NOOP, "Ceiling": OP_NOOP, "Abs": OP_NOOP,
               "Min": OP_NOOP, "Max": OP_NOOP, "Clamp": OP_NOOP,
               "ToInt": OP_NOOP, "ToFloat": OP_NOOP},
    "Maths": {"Sqrt": OP_NOOP, "Pow": OP_NOOP, "Sin": OP_NOOP, "Cos": OP_NOOP,
              "Tan": OP_NOOP, "Log": OP_NOOP, "Exp": OP_NOOP, "Abs": OP_NOOP,
              "Min": OP_NOOP, "Max": OP_NOOP, "Gcd": OP_NOOP, "Lcm": OP_NOOP},
    "DateTime": {"GetNow": OP_NOOP, "GetYear": OP_NOOP, "GetMonth": OP_NOOP,
                 "GetDay": OP_NOOP, "GetHour": OP_NOOP, "GetMinute": OP_NOOP,
                 "GetSecond": OP_NOOP, "ToTimestamp": OP_NOOP, "FromTimestamp": OP_NOOP,
                 "AddDays": OP_NOOP, "Format": OP_NOOP},
    "Locale": {"GetCurrent": OP_NOOP, "SetCurrent": OP_NOOP,
               "Format": OP_NOOP, "Parse": OP_NOOP, "GetLanguage": OP_NOOP,
               "GetRegion": OP_NOOP, "ToLocalTime": OP_NOOP},
}

# Host hooks (v1, stable)
HOST_HOOK_BASE = 0x7000
HOST_HOOK_CODES = {
    ("Kernel", "WaitIRQ"): 0x01, ("Kernel", "WaitSWIRQ"): 0x02, ("Kernel", "FireSWIRQ"): 0x03,
    ("Kernel", "ProfileStart"): 0x04, ("Kernel", "ProfileEnd"): 0x05, ("Kernel", "TracePoint"): 0x06,
    ("Queue", "Dequeue"): 0x10, ("Queue", "Enqueue"): 0x11, ("Queue", "Depth"): 0x12,
    ("Queue", "DequeueBatch"): 0x13, ("Queue", "EnqueueBatch"): 0x14,
    ("Random", "U32"): 0x20,
    ("Memory", "ArenaInit"): 0x30, ("Memory", "ArenaAlloc"): 0x31,
    ("Memory", "ArenaReset"): 0x32, ("Memory", "ArenaStats"): 0x33,
    ("Span", "Make"): 0x40, ("Span", "Slice"): 0x41,
    ("Descriptor", "Make"): 0x50, ("Descriptor", "SetFlags"): 0x51,
    ("Descriptor", "GetPtr"): 0x52, ("Descriptor", "GetLen"): 0x53,
    ("Descriptor", "GetFlags"): 0x54, ("Descriptor", "CopyBatch"): 0x55,
    ("Lease", "Acquire"): 0x58, ("Lease", "Release"): 0x59, ("Lease", "Validate"): 0x5A,
    ("Lease", "CachedValidate"): 0x5B, ("Lease", "GetSpan"): 0x5C, ("Lease", "GetTypeHint"): 0x5D,
    ("Storage", "GetSchemaForPack"): 0x60, ("Storage", "SetSchemaForPack"): 0x61,
    ("Storage", "AddCard"): 0x62, ("Storage", "UpdateCard"): 0x63, ("Storage", "DeleteCard"): 0x64,
    ("Storage", "PatchCard"): 0x65, ("Storage", "ReadCard"): 0x66, ("Storage", "QueryCard"): 0x67,
    ("Thread", "YieldCounted"): 0x70,
}

# NEW: String, Number, Maths, DateTime, Locale hooks
for i, (ns, method) in enumerate([
    ("String", "Concat"), ("String", "Length"), ("String", "Substring"),
    ("String", "IndexOf"), ("String", "Split"), ("String", "Trim"),
    ("String", "ToUpper"), ("String", "ToLower"), ("String", "Replace"),
    ("String", "Format"), ("String", "Parse"), ("String", "Equals"),
], 0x80):
    HOST_HOOK_CODES[(ns, method)] = i
for i, (ns, method) in enumerate([
    ("Number", "Parse"), ("Number", "Format"), ("Number", "Round"),
    ("Number", "Floor"), ("Number", "Ceiling"), ("Number", "Abs"),
    ("Number", "Min"), ("Number", "Max"), ("Number", "Clamp"),
    ("Number", "ToInt"), ("Number", "ToFloat"),
], 0x90):
    HOST_HOOK_CODES[(ns, method)] = i
for i, (ns, method) in enumerate([
    ("Maths", "Sqrt"), ("Maths", "Pow"), ("Maths", "Sin"), ("Maths", "Cos"),
    ("Maths", "Tan"), ("Maths", "Log"), ("Maths", "Exp"), ("Maths", "Abs"),
    ("Maths", "Min"), ("Maths", "Max"), ("Maths", "Gcd"), ("Maths", "Lcm"),
], 0xA0):
    HOST_HOOK_CODES[(ns, method)] = i
for i, (ns, method) in enumerate([
    ("DateTime", "GetNow"), ("DateTime", "GetYear"), ("DateTime", "GetMonth"),
    ("DateTime", "GetDay"), ("DateTime", "GetHour"), ("DateTime", "GetMinute"),
    ("DateTime", "GetSecond"), ("DateTime", "ToTimestamp"), ("DateTime", "FromTimestamp"),
    ("DateTime", "AddDays"), ("DateTime", "Format"),
], 0xB0):
    HOST_HOOK_CODES[(ns, method)] = i
for i, (ns, method) in enumerate([
    ("Locale", "GetCurrent"), ("Locale", "SetCurrent"), ("Locale", "Format"),
    ("Locale", "Parse"), ("Locale", "GetLanguage"), ("Locale", "GetRegion"),
    ("Locale", "ToLocalTime"),
], 0xC0):
    HOST_HOOK_CODES[(ns, method)] = i


# ═══════════════════════════════════════════════════════════════════════
# Tokenizer: case-insensitive, whitespace-ignorant
# ═══════════════════════════════════════════════════════════════════════

class Token:
    """Lexical token with type, value, line, column."""
    def __init__(self, type_: str, value: str, line: int = 0, col: int = 0):
        self.type = type_
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.type}, {self.value!r})"


class Tokenizer:
    """Lexer: source text → tokens (case-insensitive, whitespace-ignorant)."""

    KEYWORDS = {
        "if", "then", "else", "elseif", "endif",
        "foreach", "as", "in", "endforeach",
        "for", "endfor",
        "while", "endwhile",
        "switch", "case", "endswitch",
        "true", "false", "null",
    }

    def __init__(self, source: str):
        self.source = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: List[Token] = []

    def tokenize(self) -> List[Token]:
        """Lex source into tokens."""
        while self.pos < len(self.source):
            ch = self.source[self.pos]

            # Skip whitespace (but track CRLF for line counting)
            if ch in (" ", "\t", "\r", "\n"):
                if ch == "\n":
                    self.line += 1
                    self.col = 1
                elif ch == "\r":
                    # Handle CRLF as single line ending
                    if self.pos + 1 < len(self.source) and self.source[self.pos + 1] == "\n":
                        self.pos += 1
                    self.line += 1
                    self.col = 1
                else:
                    self.col += 1
                self.pos += 1
                continue

            # Comments: // to end of line
            if ch == "/" and self.pos + 1 < len(self.source) and self.source[self.pos + 1] == "/":
                while self.pos < len(self.source) and self.source[self.pos] not in ("\n", "\r"):
                    self.pos += 1
                continue

            # String literals
            if ch in ('"', "'"):
                self._read_string(ch)
                continue

            # Numbers
            if ch.isdigit() or (ch == "-" and self.pos + 1 < len(self.source) and self.source[self.pos + 1].isdigit()):
                self._read_number()
                continue

            # Identifiers / keywords (case-insensitive)
            if ch.isalpha() or ch == "_":
                self._read_identifier()
                continue

            # Punctuation
            if ch in ("(", ")", ",", ".", ":"):
                self.tokens.append(Token("PUNCT", ch, self.line, self.col))
                self.pos += 1
                self.col += 1
                continue

            # Unknown
            raise SyntaxError(f"Unexpected character '{ch}' at line {self.line}, col {self.col}")

        return self.tokens

    def _read_string(self, quote: str):
        start_line = self.line
        start_col = self.col
        self.pos += 1
        self.col += 1
        value = ""
        while self.pos < len(self.source):
            ch = self.source[self.pos]
            if ch == quote:
                self.pos += 1
                self.col += 1
                self.tokens.append(Token("STRING", value, start_line, start_col))
                return
            if ch == "\\":
                self.pos += 1
                self.col += 1
                if self.pos < len(self.source):
                    escape = self.source[self.pos]
                    if escape == "n":
                        value += "\n"
                    elif escape == "t":
                        value += "\t"
                    elif escape == "r":
                        value += "\r"
                    elif escape == "\\":
                        value += "\\"
                    else:
                        value += escape
                    self.pos += 1
                    self.col += 1
                continue
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
            value += ch
            self.pos += 1
        raise SyntaxError(f"Unterminated string starting at line {start_line}, col {start_col}")

    def _read_number(self):
        start = self.pos
        start_col = self.col
        if self.source[self.pos] == "-":
            self.pos += 1
            self.col += 1
        while self.pos < len(self.source) and (self.source[self.pos].isdigit() or self.source[self.pos] == "."):
            self.pos += 1
            self.col += 1
        value = self.source[start:self.pos]
        self.tokens.append(Token("NUMBER", value, self.line, start_col))

    def _read_identifier(self):
        start = self.pos
        start_col = self.col
        while self.pos < len(self.source) and (self.source[self.pos].isalnum() or self.source[self.pos] == "_"):
            self.pos += 1
            self.col += 1
        value = self.source[start:self.pos]
        value_lower = value.lower()
        if value_lower in self.KEYWORDS:
            self.tokens.append(Token("KEYWORD", value_lower, self.line, start_col))
        else:
            self.tokens.append(Token("IDENT", value, self.line, start_col))


# ═══════════════════════════════════════════════════════════════════════
# Parser: tokens → AST (block-structured)
# ═══════════════════════════════════════════════════════════════════════

class Statement:
    """Base AST node."""
    pass


class CallStmt(Statement):
    """Namespace.Method(args) call."""
    def __init__(self, namespace: str, method: str, args: List[str]):
        self.namespace = namespace.lower()
        self.method = method.lower()
        self.args = args


class IfStmt(Statement):
    """IF condition THEN ... ELSE ... ENDIF block."""
    def __init__(self, condition: List[str], then_body: List[Statement], else_body: Optional[List[Statement]]):
        self.condition = condition
        self.then_body = then_body
        self.else_body = else_body


class WhileStmt(Statement):
    """WHILE condition ... ENDWHILE block."""
    def __init__(self, condition: List[str], body: List[Statement]):
        self.condition = condition
        self.body = body


class ForEachStmt(Statement):
    """FOREACH item AS var IN items ... ENDFOREACH block."""
    def __init__(self, item: str, var: str, items_expr: List[str], body: List[Statement]):
        self.item = item
        self.var = var
        self.items_expr = items_expr
        self.body = body


class SwitchStmt(Statement):
    """SWITCH expr ... CASE ... ENDSWITCH block."""
    def __init__(self, expr: List[str], cases: List[Tuple[List[str], List[Statement]]], default_body: Optional[List[Statement]]):
        self.expr = expr
        self.cases = cases  # List of (condition_tokens, body)
        self.default_body = default_body


class Parser:
    """Parse tokens into AST."""

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def parse(self) -> List[Statement]:
        """Parse tokens into statement list."""
        stmts = []
        while not self._at_end():
            stmt = self._parse_stmt()
            if stmt:
                stmts.append(stmt)
        return stmts

    def _parse_stmt(self) -> Optional[Statement]:
        """Parse a single statement."""
        if self._at_end():
            return None

        if self._peek_keyword("if"):
            return self._parse_if()
        elif self._peek_keyword("while"):
            return self._parse_while()
        elif self._peek_keyword("foreach"):
            return self._parse_foreach()
        elif self._peek_keyword("switch"):
            return self._parse_switch()
        else:
            # Call statement: Namespace.Method(args)
            return self._parse_call()

    def _parse_if(self) -> IfStmt:
        """Parse IF condition THEN ... [ELSE ...] ENDIF."""
        self._consume_keyword("if")
        condition = self._parse_until_keyword("then")
        self._consume_keyword("then")
        then_body = self._parse_until_keyword("else", "elseif", "endif")
        
        else_body = None
        if self._peek_keyword("else"):
            self._consume_keyword("else")
            else_body = self._parse_until_keyword("endif")
        elif self._peek_keyword("elseif"):
            # Chain as nested IF in else block
            else_body = [self._parse_if()]
        
        self._consume_keyword("endif")
        return IfStmt(condition, then_body, else_body)

    def _parse_while(self) -> WhileStmt:
        """Parse WHILE condition ... ENDWHILE."""
        self._consume_keyword("while")
        condition = self._parse_until_keyword("endwhile")  # Body is condition until endwhile
        # Re-extract the actual condition (everything before the body)
        # For simplicity, just treat all tokens as condition + body mixed
        # This is a simplified parser; full version would separate better
        body = []  # Could be populated from remaining tokens
        self._consume_keyword("endwhile")
        return WhileStmt(condition, body)

    def _parse_foreach(self) -> ForEachStmt:
        """Parse FOREACH item AS var IN items_expr ... ENDFOREACH."""
        self._consume_keyword("foreach")
        item_tokens = self._parse_until_keyword("as")
        self._consume_keyword("as")
        var = self._consume_ident("variable")
        self._consume_keyword("in")
        items_tokens = self._parse_until_keyword("endforeach")
        body = []  # Simplified
        self._consume_keyword("endforeach")
        return ForEachStmt(item_tokens[0].value if item_tokens else "", var, items_tokens, body)

    def _parse_switch(self) -> SwitchStmt:
        """Parse SWITCH expr ... CASE ... CASE ... [ELSE ...] ENDSWITCH."""
        self._consume_keyword("switch")
        expr = self._parse_until_keyword("case")
        cases = []
        default_body = None
        while self._peek_keyword("case"):
            self._consume_keyword("case")
            case_tokens = self._parse_until_keyword("case", "else", "endswitch")
            cases.append((case_tokens, []))  # Simplified body
        if self._peek_keyword("else"):
            self._consume_keyword("else")
            default_body = self._parse_until_keyword("endswitch")
        self._consume_keyword("endswitch")
        return SwitchStmt(expr, cases, default_body)

    def _parse_call(self) -> Optional[CallStmt]:
        """Parse Namespace.Method(args) call."""
        if self._at_end():
            return None
        
        # Collect tokens until we see a keyword or another statement boundary
        tokens_until_end = []
        while not self._at_end() and not self._is_keyword():
            tokens_until_end.append(self._advance())
        
        if not tokens_until_end:
            return None
        
        # Reconstruct call from tokens
        text = " ".join(t.value for t in tokens_until_end)
        
        # Parse "Namespace.Method(arg0, arg1, ...)"
        # Very simplified: just tokenize the call text
        try:
            # Find Namespace.Method(...)
            if "." not in text or "(" not in text:
                return None
            
            dot_idx = text.index(".")
            paren_idx = text.index("(")
            namespace = text[:dot_idx].strip().title()  # Normalize case
            method = text[dot_idx+1:paren_idx].strip().title()
            close_paren = text.rindex(")")
            args_str = text[paren_idx+1:close_paren]
            args = [a.strip() for a in args_str.split(",") if a.strip()]
            
            return CallStmt(namespace, method, args)
        except (ValueError, IndexError):
            return None

    def _parse_until_keyword(self, *keywords: str) -> List[Token]:
        """Collect tokens until one of the keywords is reached."""
        result = []
        while not self._at_end():
            if any(self._peek_keyword(kw) for kw in keywords):
                break
            result.append(self._advance())
        return result

    def _peek_keyword(self, kw: str) -> bool:
        """Check if next token is a keyword."""
        if self._at_end():
            return False
        tok = self.tokens[self.pos]
        return tok.type == "KEYWORD" and tok.value == kw.lower()

    def _consume_keyword(self, kw: str) -> Token:
        """Consume a keyword or raise error."""
        if not self._peek_keyword(kw):
            raise SyntaxError(f"Expected keyword '{kw}'")
        return self._advance()

    def _consume_ident(self, desc: str = "identifier") -> str:
        """Consume an identifier or raise error."""
        if self._at_end() or self.tokens[self.pos].type != "IDENT":
            raise SyntaxError(f"Expected {desc}")
        return self._advance().value

    def _advance(self) -> Token:
        """Consume and return current token."""
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _is_keyword(self) -> bool:
        """Check if current token is a keyword."""
        if self._at_end():
            return False
        return self.tokens[self.pos].type == "KEYWORD"

    def _at_end(self) -> bool:
        """Check if we're at end of tokens."""
        return self.pos >= len(self.tokens)


# ═══════════════════════════════════════════════════════════════════════
# Example / smoke test
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Test case-insensitive, whitespace-ignorant parsing
    source = """
IF R0 EQ 42 THEN
    String.Concat(R1, R2, R3)
    Number.Format(R4, R3, 2)
ELSE
    Maths.Sqrt(R5, R6)
ENDIF

WHILE R9 LT 100
    Maths.Add(R9, R9, 1)
ENDWHILE
"""

    print("=== PicoScript v2 Parser Demo ===\n")
    print("Source:")
    print(source)
    print("\n=== Tokenization ===")
    tokenizer = Tokenizer(source)
    tokens = tokenizer.tokenize()
    for i, tok in enumerate(tokens[:30]):  # Show first 30
        print(f"{i}: {tok}")
    
    print("\n=== Parsing ===")
    parser = Parser(tokens)
    stmts = parser.parse()
    for stmt in stmts:
        print(f"Statement: {stmt.__class__.__name__}")
        if isinstance(stmt, CallStmt):
            print(f"  {stmt.namespace}.{stmt.method}({', '.join(stmt.args)})")
        elif isinstance(stmt, IfStmt):
            print(f"  IF condition: {[t.value for t in stmt.condition][:5]}...")
        elif isinstance(stmt, WhileStmt):
            print(f"  WHILE condition: {[t.value for t in stmt.condition][:5]}...")
