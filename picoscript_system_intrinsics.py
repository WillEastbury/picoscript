"""Shared source-dialect intrinsics for the PicoScript systems ISA.

C reaches these operations through its type system. BASIC, Python-style and
English source share this explicit namespace surface through their common
lowerer, keeping the semantic capability available without importing C syntax.
"""

from picoscript_systems import (
    F_INT64, F_MEMORY, F_POINTER, F_FRAME, F_STORAGE, F_GRAPH,
    T_I64, T_U64, T_PTR, T_SIZE, T_WAL, T_INDEX, T_POSTINGS, T_GRAPH, T_NODE,
    S_MOV64, S_MOVI64, S_ADD64, S_SUB64, S_MUL64, S_DIV64,
    S_AND64, S_OR64, S_XOR64, S_SHL64, S_SHR64, S_FROM_R32, S_TO_R32,
    S_EQ64, S_NE64, S_LT64, S_GT64, S_LE64, S_GE64,
    S_LOAD8, S_LOAD16, S_LOAD32, S_LOAD64,
    S_STORE8, S_STORE16, S_STORE32, S_STORE64, S_MEMCPY, S_MEMSET,
    S_PTR_ADD, S_PTR_DIFF, S_PTR_INDEX,
    S_FRAME_ENTER, S_FRAME_LEAVE, S_ADDR_LOCAL,
    S_WAL_OPEN, S_WAL_PACK, S_WAL_PUT, S_WAL_CREATE, S_WAL_GET,
    S_WAL_DELETE, S_WAL_EXISTS, S_WAL_SCAN, S_WAL_SYNC, S_WAL_RECOVER,
    S_INDEX_OPEN, S_INDEX_UPSERT, S_INDEX_DELETE, S_INDEX_EXACT,
    S_INDEX_REVERSE, S_INDEX_RESULT,
    S_FTS_OPEN, S_FTS_UPSERT, S_FTS_DELETE, S_FTS_FIND, S_FTS_RESULT,
    S_GRAPH_OPEN, S_GRAPH_SET_WEIGHT, S_GRAPH_ADD, S_GRAPH_DELETE,
    S_GRAPH_WEIGHT, S_GRAPH_OUT, S_GRAPH_IN, S_GRAPH_RESULT_NODE,
    S_GRAPH_RESULT_WEIGHT, S_GRAPH_SHORTEST_PATH,
)


def system_intrinsic_result_type(ns, method):
    """Native result type for a value-producing intrinsic, or ``None``."""
    if ns is None:
        return None
    namespace, name = ns.upper(), method.upper()
    if namespace in ("U64", "I64"):
        return (namespace.lower() if name not in {
            "TOR", "EQ", "NE", "LT", "GT", "LE", "GE",
            "CMP_EQ", "CMP_NE", "CMP_LT", "CMP_GT", "CMP_LE", "CMP_GE",
        } else "i32")
    if namespace == "PTR":
        return "ptr" if name in {"ADD", "INDEX"} else ("offset" if name == "DIFF" else None)
    if namespace in ("SYSMEM", "SYSTEMMEMORY"):
        if name.startswith("LOAD"):
            return "u64"
        return "ptr" if name in {"MEMSET", "MEMCPY"} else None
    if namespace == "FRAME":
        return "ptr" if name == "ADDRLOCAL" else None
    if namespace == "WAL":
        return "wal" if name in {"OPEN", "PACK"} else ("span" if name == "GET" else "i32")
    if namespace in {"INDEX", "FTS", "FULLTEXT"}:
        return "index" if name in {"OPEN", "ON", "FIELD"} else "i32"
    if namespace == "GRAPH":
        return "graph" if name in {"OPEN", "ON"} else "i32"
    return "size" if namespace == "BLOCK" and name == "SIZE" else None


def system_intrinsic_returns_x(ns, method):
    return system_intrinsic_result_type(ns, method) in {
        "i64", "u64", "ptr", "size", "offset", "wal", "index",
        "postings", "graph", "node",
    }


def lower_system_intrinsic(builder, eval_arg, ns, method, args, want_value=True,
                           coerce_x=None, coerce_r=None):
    """Return ``(handled, result_vreg_or_none)`` for a shared intrinsic call."""
    if ns is None: return False, None
    namespace, name = ns.upper(), method.upper()

    def xarg(expr, typ):
        value = eval_arg(expr)
        return coerce_x(value, typ) if coerce_x is not None else value

    def rarg(expr):
        value = eval_arg(expr)
        return coerce_r(value) if coerce_r is not None else value

    if namespace in ("U64", "I64"):
        typ = T_I64 if namespace == "I64" else T_U64
        if name == "FROMR":
            out = builder.vreg(); builder.system(F_INT64, S_FROM_R32, out, eval_arg(args[0]), typecode=typ)
            return True, out
        if name == "TOR":
            out = builder.vreg(); builder.system(F_INT64, S_TO_R32, out, xarg(args[0], namespace.lower()), typecode=typ)
            return True, out
        if name in ("CONST", "IMM"):
            value = getattr(args[0], "value", None)
            if not isinstance(value, int):
                out = builder.vreg(); builder.system(F_INT64, S_FROM_R32, out, eval_arg(args[0]), typecode=typ)
            else:
                out = builder.vreg(); builder.system(F_INT64, S_MOVI64, out, payload=value, typecode=typ)
            return True, out
        unary = {"MOV": S_MOV64}
        binary = {"ADD": S_ADD64, "SUB": S_SUB64, "MUL": S_MUL64, "DIV": S_DIV64,
                  "AND": S_AND64, "OR": S_OR64, "XOR": S_XOR64,
                  "SHL": S_SHL64, "SHR": S_SHR64}
        compare = {"EQ": S_EQ64, "NE": S_NE64, "LT": S_LT64, "GT": S_GT64,
                   "LE": S_LE64, "GE": S_GE64, "CMP_EQ": S_EQ64,
                   "CMP_NE": S_NE64, "CMP_LT": S_LT64, "CMP_GT": S_GT64,
                   "CMP_LE": S_LE64, "CMP_GE": S_GE64}
        if name in unary:
            out = builder.vreg(); builder.system(F_INT64, unary[name], out, xarg(args[0], namespace.lower()), typecode=typ)
            return True, out
        if name in binary or name in compare:
            a = xarg(args[0], namespace.lower())
            b = xarg(args[1], namespace.lower())
            out = builder.vreg()
            builder.system(F_INT64, (binary | compare)[name], out, a, payload=b, typecode=typ)
            return True, out

    if namespace == "PTR":
        ops = {"ADD": S_PTR_ADD, "DIFF": S_PTR_DIFF, "INDEX": S_PTR_INDEX}
        if name in ops:
            a, b = xarg(args[0], "ptr"), xarg(args[1], "offset"); out = builder.vreg()
            builder.system(F_POINTER, ops[name], out, a, payload=b, typecode=T_PTR)
            return True, out

    if namespace in ("SYSMEM", "SYSTEMMEMORY"):
        loads = {"LOAD8": S_LOAD8, "LOAD16": S_LOAD16,
                 "LOAD32": S_LOAD32, "LOAD64": S_LOAD64}
        stores = {"STORE8": S_STORE8, "STORE16": S_STORE16,
                  "STORE32": S_STORE32, "STORE64": S_STORE64}
        if name in loads:
            out = builder.vreg(); builder.system(F_MEMORY, loads[name], out, xarg(args[0], "ptr"), typecode=T_U64)
            return True, out
        if name in stores:
            base, value = xarg(args[0], "ptr"), xarg(args[1], "u64")
            builder.system(F_MEMORY, stores[name], value, base, typecode=T_U64)
            return True, value if want_value else None
        if name in ("MEMSET", "MEMCPY"):
            dst = xarg(args[0], "ptr")
            second = xarg(args[1], "ptr" if name == "MEMCPY" else "u64")
            length = xarg(args[2], "size")
            builder.system(F_MEMORY, S_MEMSET if name == "MEMSET" else S_MEMCPY,
                           dst, second, payload=length, typecode=T_SIZE)
            return True, dst if want_value else None

    if namespace == "FRAME":
        if name == "ENTER":
            size = getattr(args[0], "value", 0) if args else 0
            builder.system(F_FRAME, S_FRAME_ENTER, payload=size, typecode=T_SIZE)
            return True, None
        if name == "LEAVE":
            builder.system(F_FRAME, S_FRAME_LEAVE)
            return True, None
        if name == "ADDRLOCAL":
            offset = getattr(args[0], "value", 0); out = builder.vreg()
            builder.system(F_FRAME, S_ADDR_LOCAL, out, payload=offset, typecode=T_PTR)
            return True, out

    if namespace == "BLOCK" and name == "SIZE":
        lo_r, hi_r = builder.vreg(), builder.vreg()
        builder.host("Block", "SizeLow", (), lo_r); builder.host("Block", "SizeHigh", (), hi_r)
        lo, hi = builder.vreg(), builder.vreg()
        builder.system(F_INT64, S_FROM_R32, lo, lo_r, typecode=T_U64)
        builder.system(F_INT64, S_FROM_R32, hi, hi_r, typecode=T_U64)
        builder.system(F_INT64, S_SHL64, hi, hi, payload=32, typecode=T_U64)
        out = builder.vreg(); builder.system(F_INT64, S_OR64, out, lo, payload=hi, typecode=T_U64)
        return True, out

    if namespace == "WAL":
        if name == "OPEN":
            out = builder.vreg(); builder.system(F_STORAGE, S_WAL_OPEN, out, typecode=T_WAL)
            return True, out
        if name == "PACK":
            out = builder.vreg(); builder.system(F_STORAGE, S_WAL_PACK, out,
                                                  xarg(args[0], "wal"), rarg(args[1]), T_WAL)
            return True, out
        simple = {"PUT": S_WAL_PUT, "CREATE": S_WAL_CREATE, "GET": S_WAL_GET,
                  "DELETE": S_WAL_DELETE, "EXISTS": S_WAL_EXISTS, "SCAN": S_WAL_SCAN}
        if name in simple:
            handle, first = xarg(args[0], "wal"), rarg(args[1])
            second = rarg(args[2]) if len(args) > 2 else 0
            builder.system(F_STORAGE, simple[name], first, handle, second, T_WAL)
            return True, first
        if name in {"SYNC", "RECOVER"}:
            out = builder.vreg()
            builder.system(F_STORAGE, S_WAL_SYNC if name == "SYNC" else S_WAL_RECOVER,
                           out, xarg(args[0], "wal"), typecode=T_WAL)
            return True, out

    if namespace == "INDEX":
        if name in {"OPEN", "ON", "FIELD"}:
            out = builder.vreg(); builder.system(F_STORAGE, S_INDEX_OPEN, out,
                                                  xarg(args[0], "wal"), rarg(args[1]), T_INDEX)
            return True, out
        ops = {"UPSERT": S_INDEX_UPSERT, "DELETE": S_INDEX_DELETE,
               "EXACT": S_INDEX_EXACT, "REVERSE": S_INDEX_REVERSE,
               "RESULT": S_INDEX_RESULT}
        if name in ops:
            handle, first = xarg(args[0], "index"), rarg(args[1])
            second = rarg(args[2]) if len(args) > 2 else 0
            builder.system(F_STORAGE, ops[name], first, handle, second, T_INDEX)
            return True, first

    if namespace in {"FTS", "FULLTEXT"}:
        if name in {"OPEN", "ON", "FIELD"}:
            out = builder.vreg(); builder.system(F_STORAGE, S_FTS_OPEN, out,
                                                  xarg(args[0], "wal"), rarg(args[1]), T_POSTINGS)
            return True, out
        ops = {"UPSERT": S_FTS_UPSERT, "DELETE": S_FTS_DELETE,
               "FIND": S_FTS_FIND, "SEARCH": S_FTS_FIND, "RESULT": S_FTS_RESULT}
        if name in ops:
            handle, first = xarg(args[0], "index"), rarg(args[1])
            second = rarg(args[2]) if len(args) > 2 else 0
            builder.system(F_STORAGE, ops[name], first, handle, second, T_POSTINGS)
            return True, first

    if namespace == "GRAPH":
        if name in {"OPEN", "ON"}:
            out = builder.vreg(); builder.system(F_GRAPH, S_GRAPH_OPEN, out,
                                                  xarg(args[0], "wal"), rarg(args[1]), T_GRAPH)
            return True, out
        handle = xarg(args[0], "graph")
        if name in {"ADD", "SETWEIGHT"}:
            source, destination = rarg(args[1]), rarg(args[2])
            weight = rarg(args[3])
            builder.system(F_GRAPH, S_GRAPH_SET_WEIGHT, weight, handle, typecode=T_GRAPH)
            builder.system(F_GRAPH, S_GRAPH_ADD, source, handle, destination, T_GRAPH)
            return True, source
        ops = {"DELETE": S_GRAPH_DELETE, "WEIGHT": S_GRAPH_WEIGHT,
               "OUT": S_GRAPH_OUT, "IN": S_GRAPH_IN,
               "RESULTNODE": S_GRAPH_RESULT_NODE, "RESULTWEIGHT": S_GRAPH_RESULT_WEIGHT,
               "SHORTESTPATH": S_GRAPH_SHORTEST_PATH, "PATH": S_GRAPH_SHORTEST_PATH}
        if name in ops:
            first = rarg(args[1]); second = rarg(args[2]) if len(args) > 2 else 0
            builder.system(F_GRAPH, ops[name], first, handle, second,
                           T_NODE if name in {"RESULTNODE", "SHORTESTPATH", "PATH"} else T_GRAPH)
            return True, first

    return False, None
