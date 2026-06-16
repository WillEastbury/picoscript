#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""picoscript_lang.py -- PicoScript Source Language & Compiler

Language/editor contract: docs/picoscript-language-editor.md
Hardware bytecode contract: docs/picoscript-hardware.md

PicoScript source looks like method calls on hardware namespaces:

    Storage.Load(tenant, pack, card, R0);
    Math.Add(R1, R0, 42);
    Flow.Branch(GT, R1, R0, :done);
    Storage.Pipe(tenant, pack, card, Stream.Out);
    :done
    Flow.Return();

Each statement compiles 1:1 to a single 32-bit instruction.
No optimisation. No reordering. What you write is what executes.

Namespaces map directly to FPGA hardware subsystems:

    Storage.*   → SRAM/SD controller
    Thread.*    → Context scheduler
    Math.*      → ALU (combinatorial + soft MUL/DIV)
    Flow.*      → Branch unit + call stack
    Dsp.*       → Soft MAC / ECP5 coprocessor
    Net.*       → HTTP response framer (integrated with HW parser)
    Kernel.*    → Host IRQ/SW_IRQ hook surface
    Queue.*     → Host queue hook surface
    Random.*    → Host RNG hook surface
    Memory.*    → Host arena allocation hook surface
    Span.*      → Host pointer/span hook surface
    Descriptor.*→ Host descriptor hook surface
    Storage.*   → Host card/schema hooks (swappable backend)
    Lease.*     → Lease/type-hint access control hooks

Registers: R0-R14 general purpose, R15 = connection context (read-only)
Labels: prefixed with colon (:label)
Streams: Stream.Out (TCP TX), Stream.In (TCP RX)
Conditions: EQ, NE, LT, GT, LE, GE, Z, NZ, EOF, ERR

The compiler is ~200 lines because there's nothing to optimise.
It's a 1:1 text→bytecode translator. That's the whole point.
"""


# ═══════════════════════════════════════════════════════════════════════
# Instruction encoding (matches hardware decoder exactly)
# ═══════════════════════════════════════════════════════════════════════

# Opcodes [31:28] — 4-bit, single clock decode
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

# DSP sub-ops [19:16]
DSP_MATMUL    = 0x0
DSP_SOFTMAX   = 0x1
DSP_DOT       = 0x2
DSP_SCALE     = 0x3
DSP_RELU      = 0x4
DSP_NORM      = 0x5
DSP_TOPK      = 0x6
DSP_GELU      = 0x7
DSP_TRANSPOSE = 0x8
DSP_VADD      = 0x9
DSP_EMBED     = 0xA
DSP_QUANT     = 0xB
DSP_DEQUANT   = 0xC
DSP_MASK      = 0xD
DSP_CONCAT    = 0xE
DSP_SPLIT     = 0xF

# Branch conditions [19:16]
COND_EQ  = 0x0
COND_NE  = 0x1
COND_LT  = 0x2
COND_GT  = 0x3
COND_LE  = 0x4
COND_GE  = 0x5
COND_Z   = 0x6
COND_NZ  = 0x7
COND_EOF = 0x8
COND_ERR = 0x9

# Addressing modes (encoded in Rs2 field when relevant)
ADDR_IMMEDIATE = 0x0
ADDR_REGISTER  = 0x1
ADDR_BASE_OFF  = 0x2
ADDR_REG_OFF   = 0x3


# ═══════════════════════════════════════════════════════════════════════
# Namespace → Opcode mapping
# ═══════════════════════════════════════════════════════════════════════

NAMESPACE_MAP = {
    "Storage": {
        "Load":  OP_LOAD,
        "Save":  OP_SAVE,
        "Pipe":  OP_PIPE,
        "GetSchemaForPack": OP_NOOP,
        "SetSchemaForPack": OP_NOOP,
        "AddCard":          OP_NOOP,
        "UpdateCard":       OP_NOOP,
        "DeleteCard":       OP_NOOP,
        "PatchCard":        OP_NOOP,
        "ReadCard":         OP_NOOP,
        "QueryCard":        OP_NOOP,
        "UsePack":          OP_NOOP,
        "EditCard":         OP_NOOP,
        "GetField":         OP_NOOP,
        "SetField":         OP_NOOP,
        "SetFieldStr":      OP_NOOP,
        "GetFieldStr":      OP_NOOP,
        "QueryResult":      OP_NOOP,
        "Ready":            OP_NOOP,
        "IsUserPack":       OP_NOOP,
        "SetSlice":         OP_NOOP,
        "CardLen":          OP_NOOP,
        "ReadSlice":        OP_NOOP,
        "WriteSlice":       OP_NOOP,
    },
    "Query": {
        "BuildLookupFilter": OP_NOOP,
        "BuildManyToManyMap": OP_NOOP,
    },
    "Search": {
        "Clear": OP_NOOP,
        "UpsertText": OP_NOOP,
        "Delete": OP_NOOP,
        "IndexPack": OP_NOOP,
        "QueryText": OP_NOOP,
        "SetVector": OP_NOOP,
        "QueryHybrid": OP_NOOP,
        "Result": OP_NOOP,
        "Score": OP_NOOP,
        "Plan": OP_NOOP,
        "SetSemanticWeight": OP_NOOP,
    },
    "Tensor": {
        "SetShape": OP_NOOP,
        "DotI8": OP_NOOP,
        "MatVecI8": OP_NOOP,
        "AddI32": OP_NOOP,
        "MulI32": OP_NOOP,
        "ScaleI32": OP_NOOP,
        "ReluI32": OP_NOOP,
        "RmsNormI32": OP_NOOP,
        "RoPEI32": OP_NOOP,
        "SoftmaxI32": OP_NOOP,
        "ArgMaxI32": OP_NOOP,
    },
    "BitLinear": {
        "SetShape": OP_NOOP,
        "MatVecTernary": OP_NOOP,
    },
    "Thread": {
        "Skip":  OP_NOOP,
        "Wait":  OP_WAIT,
        "Raise": OP_RAISE,
    },
    "Io": {
        "Write":     OP_NOOP,
        "WriteByte": OP_NOOP,
    },
    "Utf8Writer": {
        "New": OP_NOOP, "Byte": OP_NOOP, "Int": OP_NOOP, "Span": OP_NOOP,
        "ToSpan": OP_NOOP, "Len": OP_NOOP, "Reset": OP_NOOP,
    },
    "Utf8Reader": {
        "New": OP_NOOP, "Peek": OP_NOOP, "Next": OP_NOOP, "Int": OP_NOOP,
        "SkipWs": OP_NOOP, "Eof": OP_NOOP, "Pos": OP_NOOP, "Match": OP_NOOP,
    },
    "Json": {
        "BeginObject": OP_NOOP, "EndObject": OP_NOOP, "BeginArray": OP_NOOP,
        "EndArray": OP_NOOP, "Key": OP_NOOP, "Str": OP_NOOP, "Int": OP_NOOP,
        "Bool": OP_NOOP, "Null": OP_NOOP, "Raw": OP_NOOP,
    },
    "Xml": {
        "Open": OP_NOOP, "AttrName": OP_NOOP, "AttrValue": OP_NOOP,
        "OpenEnd": OP_NOOP, "Text": OP_NOOP, "Close": OP_NOOP, "Empty": OP_NOOP,
    },
    "Math": {
        "Add":   OP_ADD,
        "Sub":   OP_SUB,
        "Mul":   OP_MUL,
        "Div":   OP_DIV,
        "Inc":   OP_INC,
    },
    "Flow": {
        "Jump":   OP_JUMP,
        "Branch": OP_BRANCH,
        "Call":   OP_CALL,
        "Return": OP_RETURN,
    },
    "Dsp": {
        "MatMul":   (OP_DSP, DSP_MATMUL),
        "Softmax":  (OP_DSP, DSP_SOFTMAX),
        "Dot":      (OP_DSP, DSP_DOT),
        "Scale":    (OP_DSP, DSP_SCALE),
        "Relu":     (OP_DSP, DSP_RELU),
        "Norm":     (OP_DSP, DSP_NORM),
        "TopK":     (OP_DSP, DSP_TOPK),
        "Gelu":     (OP_DSP, DSP_GELU),
        "Transpose":(OP_DSP, DSP_TRANSPOSE),
        "VAdd":     (OP_DSP, DSP_VADD),
        "Embed":    (OP_DSP, DSP_EMBED),
        "Quant":    (OP_DSP, DSP_QUANT),
        "Dequant":  (OP_DSP, DSP_DEQUANT),
        "Mask":     (OP_DSP, DSP_MASK),
        "Concat":   (OP_DSP, DSP_CONCAT),
        "Split":    (OP_DSP, DSP_SPLIT),
    },
    "Net": {
        "Status":  OP_NOOP,  # Encoded as NOOP + metadata (HTTP framer reads imm16)
        "Header":  OP_NOOP,  # Hardware HTTP framer handles these
        "Type":    OP_NOOP,  # Content-Type shorthand
        "Body":    OP_NOOP,  # End headers, start body
        "Close":   OP_NOOP,  # Close connection
    },
    "Kernel": {
        "WaitIRQ":       OP_NOOP,  # Host hook surface
        "WaitSWIRQ":     OP_NOOP,  # Host hook surface
        "FireSWIRQ":     OP_NOOP,  # Permission-gated wake request
        "ProfileStart":  OP_NOOP,  # Profiling hook (performance)
        "ProfileEnd":    OP_NOOP,  # Profiling hook (performance)
        "TracePoint":    OP_NOOP,  # Trace event hook (performance)
    },
    "Thread": {
        "Skip":         OP_NOOP,
        "Wait":         OP_WAIT,
        "Raise":        OP_RAISE,
        "YieldCounted": OP_NOOP,  # Batch preemption hint (performance)
    },
    "Queue": {
        "Dequeue":      OP_NOOP,  # Host hook surface
        "Enqueue":      OP_NOOP,  # Host hook surface
        "Depth":        OP_NOOP,  # Host hook surface
        "DequeueBatch": OP_NOOP,  # Batch drain (performance)
        "EnqueueBatch": OP_NOOP,  # Batch enqueue (performance)
    },
    "Random": {
        "U32": OP_NOOP,      # Host RNG hook surface
    },
    "Bits": {
        "And": OP_NOOP,
        "Or":  OP_NOOP,
        "Xor": OP_NOOP,
        "Shl": OP_NOOP,
        "Shr": OP_NOOP,
        "Sar": OP_NOOP,
        "Not": OP_NOOP,
    },
    "Dot8": {
        "Len": OP_NOOP,     # set active span length for Dot8.Of
        "Of":  OP_NOOP,     # int8 span dot product (NEON SDOT / SMLAD / scalar)
    },
    "Memory": {
        "ArenaInit":  OP_NOOP,
        "ArenaAlloc": OP_NOOP,
        "ArenaReset": OP_NOOP,
        "ArenaStats": OP_NOOP,
        "Peek":       OP_NOOP,  # Read typed memory at offset
        "Poke":       OP_NOOP,  # Write typed memory at offset
        "Set":        OP_NOOP,  # Set(addr, byte) -> write one byte
        "Get":        OP_NOOP,  # Get(addr) -> read one byte
    },
    "Span": {
        "Make":        OP_NOOP,
        "Slice":       OP_NOOP,  # Slice(span, offset) -> zero-copy sub-span view
        "Materialize": OP_NOOP,  # Materialize(span) -> memcpy to a new contiguous span
        "Len":         OP_NOOP,  # Len(span) -> length
        "Get":         OP_NOOP,  # Get(span, index) -> byte at span[index]
    },
    "Descriptor": {
        "Make":      OP_NOOP,
        "SetFlags":  OP_NOOP,
        "GetPtr":    OP_NOOP,
        "GetLen":    OP_NOOP,
        "GetFlags":  OP_NOOP,
        "CopyBatch": OP_NOOP,  # Batch span transfer (performance)
    },
    "Lease": {
        "Acquire":        OP_NOOP,
        "Release":        OP_NOOP,
        "Validate":       OP_NOOP,
        "CachedValidate": OP_NOOP,  # Fast-path validation (performance)
        "GetSpan":        OP_NOOP,
        "GetTypeHint":    OP_NOOP,
    },
    "String": {
        "Concat":         OP_NOOP,
        "Length":         OP_NOOP,
        "Substring":      OP_NOOP,
        "IndexOf":        OP_NOOP,
        "Replace":        OP_NOOP,
        "ToUpper":        OP_NOOP,
        "ToLower":        OP_NOOP,
        "Trim":           OP_NOOP,
        "Split":          OP_NOOP,
        "Join":           OP_NOOP,
        "StartsWith":     OP_NOOP,
        "EndsWith":       OP_NOOP,
        "SetReplace":     OP_NOOP,
    },
    "Number": {
        "Parse":          OP_NOOP,
        "ToString":       OP_NOOP,
        "ToHex":          OP_NOOP,
        "ToOctal":        OP_NOOP,
        "ToBinary":       OP_NOOP,
        "Abs":            OP_NOOP,
        "Floor":          OP_NOOP,
        "Ceiling":        OP_NOOP,
        "Round":          OP_NOOP,
        "Min":            OP_NOOP,
        "Max":            OP_NOOP,
    },
    "Template": {
        "Compile":        OP_NOOP,    # AOT: template source span -> compiled plan span (at save time)
        "Render":         OP_NOOP,    # plan span + model span -> rendered output span
    },
    "Maths": {
        "Sin":            OP_NOOP,
        "Cos":            OP_NOOP,
        "Tan":            OP_NOOP,
        "Sqrt":           OP_NOOP,
        "Power":          OP_NOOP,
        "Log":            OP_NOOP,
        "Log10":          OP_NOOP,
        "Exp":            OP_NOOP,
        "Random":         OP_NOOP,
        "RandomRange":    OP_NOOP,
        "Clamp":          OP_NOOP,
        "Lerp":           OP_NOOP,
    },
    "DateTime": {
        "Now":            OP_NOOP,
        "UtcNow":         OP_NOOP,
        "Parse":          OP_NOOP,
        "Format":         OP_NOOP,
        "AddSeconds":     OP_NOOP,
        "AddMinutes":     OP_NOOP,
        "AddHours":       OP_NOOP,
        "AddDays":        OP_NOOP,
        "GetDayOfWeek":   OP_NOOP,
        "GetDayOfYear":   OP_NOOP,
        "UnixTimestamp":  OP_NOOP,
    },
    "Locale": {
        "GetCurrentLocale": OP_NOOP,
        "SetLocale":        OP_NOOP,
        "FormatCurrency":   OP_NOOP,
        "FormatNumber":     OP_NOOP,
        "FormatDate":       OP_NOOP,
        "FormatTime":       OP_NOOP,
        "Translate":        OP_NOOP,
    },
    "Environment": {
        "GetOsVersion":     OP_NOOP,
        "GetCpuCount":      OP_NOOP,
        "GetMemoryTotal":   OP_NOOP,
        "GetMemoryFree":    OP_NOOP,
        "GetHostname":      OP_NOOP,
        "GetTimeZone":      OP_NOOP,
        "GetProcessId":     OP_NOOP,
        "GetThreadId":      OP_NOOP,
        "GetElapsedTime":   OP_NOOP,
    },
    "Context": {
        "GetVerb":          OP_NOOP,
        "GetPath":          OP_NOOP,
        "GetHost":          OP_NOOP,
        "GetPort":          OP_NOOP,
        "GetRemoteAddr":    OP_NOOP,
        "GetUser":          OP_NOOP,
        "GetPermissions":   OP_NOOP,
        "GetHeaders":       OP_NOOP,
        "GetQueryString":   OP_NOOP,
        "GetBody":          OP_NOOP,
        "SetScratchValue":  OP_NOOP,
        "GetScratchValue":  OP_NOOP,
        "GetRequestId":     OP_NOOP,
        "GetClientCert":    OP_NOOP,
        "GetTraceId":       OP_NOOP,
    },
    "Crypto": {
        "Sha256":           OP_NOOP,
        "Sha512":           OP_NOOP,
        "Blake2b":          OP_NOOP,
        "Blake3":           OP_NOOP,
        "HmacSha256":       OP_NOOP,
        "HmacSha512":       OP_NOOP,
        "Sign":             OP_NOOP,
        "Verify":           OP_NOOP,
        "Encrypt":          OP_NOOP,
        "Decrypt":          OP_NOOP,
        "GenerateKeyPair":  OP_NOOP,
        "DeriveKey":        OP_NOOP,
        "RandomBytes":      OP_NOOP,
        "Md5":              OP_NOOP,
        "Sha1":             OP_NOOP,
    },
    "Compress": {
        "BrotliCompress":   OP_NOOP,
        "BrotliDecompress": OP_NOOP,
        "PicoCompress":     OP_NOOP,
        "PicoDecompress":   OP_NOOP,
        "GzipCompress":     OP_NOOP,
        "GzipDecompress":   OP_NOOP,
        "DeflateCompress":  OP_NOOP,
        "DeflateDecompress": OP_NOOP,
    },
    "X509": {
        "FetchCertificate": OP_NOOP,
        "StoreCertificate": OP_NOOP,
        "GenerateCSR":      OP_NOOP,
        "GenerateKeyPair":  OP_NOOP,
        "VerifyCertChain":  OP_NOOP,
        "GetCertInfo":      OP_NOOP,
        "IsCertValid":      OP_NOOP,
        "GetKeyHandle":     OP_NOOP,
    },
    "Auth": {
        "GetUserCredentials": OP_NOOP,
        "ValidateCredentials": OP_NOOP,
        "SwitchUserContext": OP_NOOP,
        "GetUserPermissions": OP_NOOP,
        "RequestToken":     OP_NOOP,
        "GetToken":         OP_NOOP,
        "ValidateToken":    OP_NOOP,
        "SwitchTokenContext": OP_NOOP,
        "RefreshToken":     OP_NOOP,
        "RevokeToken":      OP_NOOP,
    },
    "Http": {
        "ReadHeader":       OP_NOOP,
        "ReadBody":         OP_NOOP,
        "GenerateHeaders":  OP_NOOP,
        "GenerateResponse": OP_NOOP,
        "ParseQuery":       OP_NOOP,
        "ParseForm":        OP_NOOP,
        "ParseJson":        OP_NOOP,
        "EncodeJson":       OP_NOOP,
    },
    "Html": {
        "CreateNode":       OP_NOOP,
        "AddChildNode":     OP_NOOP,
        "RemoveChildNode":  OP_NOOP,
        "SetAttribute":     OP_NOOP,
        "GetAttribute":     OP_NOOP,
        "ParseTree":        OP_NOOP,
        "Encode":           OP_NOOP,
        "Decode":           OP_NOOP,
        "Serialize":        OP_NOOP,
        "QuerySelector":    OP_NOOP,
    },
}

# Net.* uses NOOP opcode but with special imm16 encoding that the
# hardware HTTP framer intercepts. This keeps the opcode space at 16.
NET_STATUS_BASE  = 0x8000  # imm16 bit 15 set = HTTP control
NET_HEADER_BASE  = 0x9000
NET_TYPE_BASE    = 0xA000
NET_BODY_MARKER  = 0xB000
NET_CLOSE_MARKER = 0xC000

# Host/KERNEL/queue/rng hooks use NOOP + reserved imm16 range.
# These are language-level stable placeholders that host runtimes can bind.
HOST_HOOK_BASE = 0x7000
# Extended hostcall: imm16 = 0x6000 | (hook & 0x0FFF). Reaches hooks >= 0x100
# (Compress/X509/Auth/Http/Html) that do not fit the single-byte 0x7000 page.
EXT_HOST_HOOK_BASE = 0x6000
HOST_HOOK_CODES = {
    # Kernel hooks (0x01-0x06)
    ("Kernel", "WaitIRQ"):      0x01,
    ("Kernel", "WaitSWIRQ"):    0x02,
    ("Kernel", "FireSWIRQ"):    0x03,
    ("Kernel", "ProfileStart"): 0x04,
    ("Kernel", "ProfileEnd"):   0x05,
    ("Kernel", "TracePoint"):   0x06,
    # EL0-facing request context hooks (0x07-0x0E)
    ("Req", "Seq"):             0x07,
    ("Req", "Principal"):       0x08,
    ("Req", "Method"):          0x09,
    ("Req", "Path"):            0x0A,
    ("Req", "Header"):          0x0B,
    ("Req", "BodyMode"):        0x0C,
    ("Req", "BodyCount"):       0x0D,
    ("Req", "BodySpan"):        0x0E,
    ("Req", "SetSlice"):        0x01B0,
    ("Req", "BodySlice"):       0x01B1,
    ("Req", "BodyLen"):         0x01B2,
    # Queue hooks (0x10-0x14)
    ("Queue", "Dequeue"):       0x10,
    ("Queue", "Enqueue"):       0x11,
    ("Queue", "Depth"):         0x12,
    ("Queue", "DequeueBatch"):  0x13,
    ("Queue", "EnqueueBatch"):  0x14,
    # EL0-facing response descriptor graph hooks (0x15-0x1F, 0x38-0x39)
    ("Resp", "Status"):         0x15,
    ("Resp", "Header"):         0x16,
    ("Resp", "Write"):          0x17,
    ("Resp", "Trailer"):        0x18,
    ("Resp", "Seal"):           0x19,
    ("Resp", "End"):            0x1A,
    ("Resp", "Respond"):        0x1B,
    ("Resp", "Flush"):          0x1C,
    ("Resp", "Continue"):       0x1D,
    ("Resp", "EndStream"):      0x1E,
    ("Resp", "Upgrade"):        0x1F,
    # Random hooks (0x20)
    ("Random", "U32"):          0x20,
    # Memory hooks (0x30-0x35)
    ("Memory", "ArenaInit"):    0x30,
    ("Memory", "ArenaAlloc"):   0x31,
    ("Memory", "ArenaReset"):   0x32,
    ("Memory", "ArenaStats"):   0x33,
    ("Memory", "Peek"):         0x34,
    ("Memory", "Poke"):         0x35,
    ("Memory", "Set"):          0x36,
    ("Memory", "Get"):          0x37,
    ("Resp", "Abort"):          0x38,
    ("Resp", "EarlyHints"):     0x39,
    # Bits hooks (0x3A-0x3F, 0x4F)
    ("Bits", "And"):            0x3A,
    ("Bits", "Or"):             0x3B,
    ("Bits", "Xor"):            0x3C,
    ("Bits", "Shl"):            0x3D,
    ("Bits", "Shr"):            0x3E,
    ("Bits", "Sar"):            0x3F,
    ("Bits", "Not"):            0x4F,
    # Span hooks (0x40-0x44)
    ("Span", "Make"):           0x40,
    ("Span", "Slice"):          0x41,
    ("Span", "Materialize"):    0x42,
    ("Span", "Len"):            0x43,
    ("Span", "Get"):            0x44,
    # Descriptor hooks (0x50-0x55)
    ("Descriptor", "Make"):     0x50,
    ("Descriptor", "SetFlags"): 0x51,
    ("Descriptor", "GetPtr"):   0x52,
    ("Descriptor", "GetLen"):   0x53,
    ("Descriptor", "GetFlags"): 0x54,
    ("Descriptor", "CopyBatch"):0x55,
    # Dot8 SIMD int8 dot-product hooks (0x56-0x57)
    ("Dot8", "Len"):            0x56,
    ("Dot8", "Of"):             0x57,
    # Lease hooks (0x58-0x5D)
    ("Lease", "Acquire"):       0x58,
    ("Lease", "Release"):       0x59,
    ("Lease", "Validate"):      0x5A,
    ("Lease", "CachedValidate"):0x5B,
    ("Lease", "GetSpan"):       0x5C,
    ("Lease", "GetTypeHint"):   0x5D,
    # Status hook (0x5E): out-of-band typed status of the last fallible hook (INV-18).
    # 0=OK, 1=NOT_FOUND, 2=PARSE_ERROR, 3=EMPTY. Pure read of VM state; does not clear.
    ("Status", "Last"):         0x5E,
    # Const-pool write (0x5F): compiler-only literal write (INV-9). Distinct from Memory.Set
    # so the VM can mark [const_floor, 0x8000) read-only to user Memory.Set (literal immutability).
    ("Memory", "SetConst"):     0x5F,
    # Storage hooks (0x60-0x6E)
    ("Storage", "GetSchemaForPack"): 0x60,
    ("Storage", "SetSchemaForPack"): 0x61,
    ("Storage", "AddCard"):     0x62,
    ("Storage", "UpdateCard"):  0x63,
    ("Storage", "DeleteCard"):  0x64,
    ("Storage", "PatchCard"):   0x65,
    ("Storage", "ReadCard"):    0x66,
    ("Storage", "QueryCard"):   0x67,
    ("Storage", "UsePack"):     0x68,
    ("Storage", "EditCard"):    0x69,
    ("Storage", "GetField"):    0x6A,
    ("Storage", "SetField"):    0x6B,
    ("Storage", "SetFieldStr"): 0x6C,
    ("Storage", "GetFieldStr"): 0x6D,
    ("Storage", "QueryResult"): 0x6E,
    ("Storage", "Ready"):       0x6F,
    # Large-card slice hooks (0x01A0-0x01A3): SetSlice(offset,len), CardLen(card),
    # ReadSlice(card)->span, WriteSlice(card,span)->ok. Extended hostcall page.
    ("Storage", "SetSlice"):    0x01A0,
    ("Storage", "CardLen"):     0x01A1,
    ("Storage", "ReadSlice"):   0x01A2,
    ("Storage", "WriteSlice"):  0x01A3,
    ("Storage", "IsUserPack"):  0x01A4,
    # Query helper builders from picowal PR78 (bounded relation query helpers).
    ("Query", "BuildLookupFilter"): 0x01C0,
    ("Query", "BuildManyToManyMap"): 0x01C1,
    # Host search primitives from picowal PR78. The reference VM implements a
    # deterministic lexical/vector-signature approximation; production hosts can bind
    # BM25/vector ANN/hybrid/semantic callbacks behind the same hooks.
    ("Search", "Clear"):        0x01D0,
    ("Search", "UpsertText"):   0x01D1,
    ("Search", "Delete"):       0x01D2,
    ("Search", "IndexPack"):    0x01D3,
    ("Search", "QueryText"):    0x01D4,
    ("Search", "SetVector"):    0x01D5,
    ("Search", "QueryHybrid"):  0x01D6,
    ("Search", "Result"):       0x01D7,
    ("Search", "Score"):        0x01D8,
    ("Search", "Plan"):         0x01D9,
    ("Search", "SetSemanticWeight"): 0x01DA,
    # Tensor/matrix primitives for deterministic inference kernels.
    ("Tensor", "SetShape"):     0x01E0,   # rs1=rows/len rs2=cols          rd=ok
    ("Tensor", "DotI8"):        0x01E1,   # rs1=a-span rs2=b-span          rd=int32 dot
    ("Tensor", "MatVecI8"):     0x01E2,   # rs1=matrix i8 span rs2=vec i8  rd=span<int32_be>
    ("Tensor", "AddI32"):       0x01E3,   # rs1=a i32be span rs2=b i32be   rd=span<int32_be>
    ("Tensor", "MulI32"):       0x01E4,   # rs1=a i32be span rs2=b i32be   rd=span<int32_be>
    ("Tensor", "ScaleI32"):     0x01E5,   # rs1=i32be span rs2=scale       rd=span<int32_be>
    ("Tensor", "ReluI32"):      0x01E6,   # rs1=i32be span                 rd=span<int32_be>
    ("Tensor", "RmsNormI32"):   0x01E7,   # rs1=x i32be span rs2=gamma     rd=span<int32_be> (Q8 scale)
    ("Tensor", "RoPEI32"):      0x01E8,   # rs1=x pairs rs2=cos/sin Q15    rd=span<int32_be>
    ("Tensor", "SoftmaxI32"):   0x01E9,   # rs1=logits i32be               rd=span<Q15 i32be>
    ("Tensor", "ArgMaxI32"):    0x01EA,   # rs1=i32be span                 rd=index
    # BitLinear / BitNet-style ternary weights (2-bit packed; 4 weights/byte).
    ("BitLinear", "SetShape"):  0x01F0,   # rs1=rows rs2=cols              rd=ok
    ("BitLinear", "MatVecTernary"): 0x01F1,# rs1=packed weights rs2=i8 vec rd=span<int32_be>
    # Thread hints (0x70)
    ("Thread", "YieldCounted"): 0x70,
    # Io / output (0x71-0x72)
    ("Io", "Write"):            0x71,
    ("Io", "WriteByte"):        0x72,
    # Template engine (0x7A-0x7B): AOT-compiled-at-save, holes rendered at run
    ("Template", "Compile"):    0x7A,
    ("Template", "Render"):     0x7B,
    # Arena scopes (0x7C-0x7E): bump-arena mark / rewind / reset for request-scoped
    # allocation -- Mark() snapshots the arena, Rewind(mark) reclaims everything since,
    # Reset() drops all arena spans back to the base. Frees the span/string namespaces
    # from leaking across a long-running handler loop.
    ("Arena", "Mark"):          0x7C,
    ("Arena", "Rewind"):        0x7D,
    ("Arena", "Reset"):         0x7E,
    # Utf8Writer (0x21-0x27) -- arena-backed string/byte writer
    ("Utf8Writer", "New"):      0x21,
    ("Utf8Writer", "Byte"):     0x22,
    ("Utf8Writer", "Int"):      0x23,
    ("Utf8Writer", "Span"):     0x24,
    ("Utf8Writer", "ToSpan"):   0x25,
    ("Utf8Writer", "Len"):      0x26,
    ("Utf8Writer", "Reset"):    0x27,
    # Utf8Reader (0x28-0x2F) -- span scanner
    ("Utf8Reader", "New"):      0x28,
    ("Utf8Reader", "Peek"):     0x29,
    ("Utf8Reader", "Next"):     0x2A,
    ("Utf8Reader", "Int"):      0x2B,
    ("Utf8Reader", "SkipWs"):   0x2C,
    ("Utf8Reader", "Eof"):      0x2D,
    ("Utf8Reader", "Pos"):      0x2E,
    ("Utf8Reader", "Match"):    0x2F,
    # Json writer (0x45-0x4E) -- streaming JSON on a Utf8Writer
    ("Json", "BeginObject"):    0x45,
    ("Json", "EndObject"):      0x46,
    ("Json", "BeginArray"):     0x47,
    ("Json", "EndArray"):       0x48,
    ("Json", "Key"):            0x49,
    ("Json", "Str"):            0x4A,
    ("Json", "Int"):            0x4B,
    ("Json", "Bool"):           0x4C,
    ("Json", "Null"):           0x4D,
    ("Json", "Raw"):            0x4E,
    # Xml/Html element writer (0x73-0x79)
    ("Xml", "Open"):            0x73,
    ("Xml", "AttrName"):        0x74,
    ("Xml", "AttrValue"):       0x75,
    ("Xml", "OpenEnd"):         0x76,
    ("Xml", "Text"):            0x77,
    ("Xml", "Close"):           0x78,
    ("Xml", "Empty"):           0x79,
    # String library (0x80-0x8B)
    ("String", "Concat"):       0x80,
    ("String", "Length"):       0x81,
    ("String", "Substring"):    0x82,
    ("String", "IndexOf"):      0x83,
    ("String", "Replace"):      0x84,
    ("String", "ToUpper"):      0x85,
    ("String", "ToLower"):      0x86,
    ("String", "Trim"):         0x87,
    ("String", "Split"):        0x88,
    ("String", "Join"):         0x89,
    ("String", "StartsWith"):   0x8A,
    ("String", "EndsWith"):     0x8B,
    ("String", "SetReplace"):   0x8C,
    # Number library (0x90-0x9A)
    ("Number", "Parse"):        0x90,
    ("Number", "ToString"):     0x91,
    ("Number", "ToHex"):        0x92,
    ("Number", "ToOctal"):      0x93,
    ("Number", "ToBinary"):     0x94,
    ("Number", "Abs"):          0x95,
    ("Number", "Floor"):        0x96,
    ("Number", "Ceiling"):      0x97,
    ("Number", "Round"):        0x98,
    ("Number", "Min"):          0x99,
    ("Number", "Max"):          0x9A,
    # Maths library (0xA0-0xAB)
    ("Maths", "Sin"):           0xA0,
    ("Maths", "Cos"):           0xA1,
    ("Maths", "Tan"):           0xA2,
    ("Maths", "Sqrt"):          0xA3,
    ("Maths", "Power"):         0xA4,
    ("Maths", "Log"):           0xA5,
    ("Maths", "Log10"):         0xA6,
    ("Maths", "Exp"):           0xA7,
    ("Maths", "Random"):        0xA8,
    ("Maths", "RandomRange"):   0xA9,
    ("Maths", "Clamp"):         0xAA,
    ("Maths", "Lerp"):          0xAB,
    # DateTime library (0xB0-0xBA)
    ("DateTime", "Now"):        0xB0,
    ("DateTime", "UtcNow"):     0xB1,
    ("DateTime", "Parse"):      0xB2,
    ("DateTime", "Format"):     0xB3,
    ("DateTime", "AddSeconds"): 0xB4,
    ("DateTime", "AddMinutes"): 0xB5,
    ("DateTime", "AddHours"):   0xB6,
    ("DateTime", "AddDays"):    0xB7,
    ("DateTime", "GetDayOfWeek"): 0xB8,
    ("DateTime", "GetDayOfYear"): 0xB9,
    ("DateTime", "UnixTimestamp"): 0xBA,
    # Locale library (0xC0-0xC6)
    ("Locale", "GetCurrentLocale"): 0xC0,
    ("Locale", "SetLocale"):    0xC1,
    ("Locale", "FormatCurrency"): 0xC2,
    ("Locale", "FormatNumber"): 0xC3,
    ("Locale", "FormatDate"):   0xC4,
    ("Locale", "FormatTime"):   0xC5,
    ("Locale", "Translate"):    0xC6,
    # Environment library (0xD0-0xD8)
    ("Environment", "GetOsVersion"): 0xD0,
    ("Environment", "GetCpuCount"): 0xD1,
    ("Environment", "GetMemoryTotal"): 0xD2,
    ("Environment", "GetMemoryFree"): 0xD3,
    ("Environment", "GetHostname"): 0xD4,
    ("Environment", "GetTimeZone"): 0xD5,
    ("Environment", "GetProcessId"): 0xD6,
    ("Environment", "GetThreadId"): 0xD7,
    ("Environment", "GetElapsedTime"): 0xD8,
    # Context library (0xE0-0xEE)
    ("Context", "GetVerb"):     0xE0,
    ("Context", "GetPath"):     0xE1,
    ("Context", "GetHost"):     0xE2,
    ("Context", "GetPort"):     0xE3,
    ("Context", "GetRemoteAddr"): 0xE4,
    ("Context", "GetUser"):     0xE5,
    ("Context", "GetPermissions"): 0xE6,
    ("Context", "GetHeaders"):  0xE7,
    ("Context", "GetQueryString"): 0xE8,
    ("Context", "GetBody"):     0xE9,
    ("Context", "SetScratchValue"): 0xEA,
    ("Context", "GetScratchValue"): 0xEB,
    ("Context", "GetRequestId"): 0xEC,
    ("Context", "GetClientCert"): 0xED,
    ("Context", "GetTraceId"):  0xEE,
    # Crypto library (0xF0-0xFE)
    ("Crypto", "Sha256"):       0xF0,
    ("Crypto", "Sha512"):       0xF1,
    ("Crypto", "Blake2b"):      0xF2,
    ("Crypto", "Blake3"):       0xF3,
    ("Crypto", "HmacSha256"):   0xF4,
    ("Crypto", "HmacSha512"):   0xF5,
    ("Crypto", "Sign"):         0xF6,
    ("Crypto", "Verify"):       0xF7,
    ("Crypto", "Encrypt"):      0xF8,
    ("Crypto", "Decrypt"):      0xF9,
    ("Crypto", "GenerateKeyPair"): 0xFA,
    ("Crypto", "DeriveKey"):    0xFB,
    ("Crypto", "RandomBytes"):  0xFC,
    ("Crypto", "Md5"):          0xFD,
    ("Crypto", "Sha1"):         0xFE,
    # Application features use 16-bit codes (0x0100+)
    # Compress library (0x0100-0x0107)
    ("Compress", "BrotliCompress"): 0x0100,
    ("Compress", "BrotliDecompress"): 0x0101,
    ("Compress", "PicoCompress"): 0x0102,
    ("Compress", "PicoDecompress"): 0x0103,
    ("Compress", "GzipCompress"): 0x0104,
    ("Compress", "GzipDecompress"): 0x0105,
    ("Compress", "DeflateCompress"): 0x0106,
    ("Compress", "DeflateDecompress"): 0x0107,
    # X509 library (0x0110-0x0117)
    ("X509", "FetchCertificate"): 0x0110,
    ("X509", "StoreCertificate"): 0x0111,
    ("X509", "GenerateCSR"):    0x0112,
    ("X509", "GenerateKeyPair"): 0x0113,
    ("X509", "VerifyCertChain"): 0x0114,
    ("X509", "GetCertInfo"):    0x0115,
    ("X509", "IsCertValid"):    0x0116,
    ("X509", "GetKeyHandle"):   0x0117,
    # Auth library (0x0120-0x0129)
    ("Auth", "GetUserCredentials"): 0x0120,
    ("Auth", "ValidateCredentials"): 0x0121,
    ("Auth", "SwitchUserContext"): 0x0122,
    ("Auth", "GetUserPermissions"): 0x0123,
    ("Auth", "RequestToken"):   0x0124,
    ("Auth", "GetToken"):       0x0125,
    ("Auth", "ValidateToken"):  0x0126,
    ("Auth", "SwitchTokenContext"): 0x0127,
    ("Auth", "RefreshToken"):   0x0128,
    ("Auth", "RevokeToken"):    0x0129,
    # Http library (0x0130-0x0137)
    ("Http", "ReadHeader"):     0x0130,
    ("Http", "ReadBody"):       0x0131,
    ("Http", "GenerateHeaders"): 0x0132,
    ("Http", "GenerateResponse"): 0x0133,
    ("Http", "ParseQuery"):     0x0134,
    ("Http", "ParseForm"):      0x0135,
    ("Http", "ParseJson"):      0x0136,
    ("Http", "EncodeJson"):     0x0137,
    # Html library (0x0140-0x0149)
    ("Html", "CreateNode"):     0x0140,
    ("Html", "AddChildNode"):   0x0141,
    ("Html", "RemoveChildNode"): 0x0142,
    ("Html", "SetAttribute"):   0x0143,
    ("Html", "GetAttribute"):   0x0144,
    ("Html", "ParseTree"):      0x0145,
    ("Html", "Encode"):         0x0146,
    ("Html", "Decode"):         0x0147,
    ("Html", "Serialize"):      0x0148,
    ("Html", "QuerySelector"):  0x0149,
    # Gpio device library (0x0150-0x0156) -- pins exposed as cards carrying an
    # analog value in [0, 1024] (digital reads/writes saturate to 0 or 1024;
    # PWM/ADC-capable pins use the full range). Direction (in/out) and pull
    # (none/up/down) are configured by the program. The behaviour behind these
    # hooks is supplied by an injected GPIO provider: the browser ships an
    # emulator (vm/picodevices.js); PIOS supplies the real driver + per-pin
    # allow-list. All ops stay within the 2-in/1-out host ABI. See
    # docs/PIOS_PROVIDER_CONTRACT.md.
    ("Gpio", "Count"):          0x0150,   # rd = pin count                (rs ignored)
    ("Gpio", "SetDir"):         0x0151,   # rs1=pin rs2=dir(0=in,1=out)   rd=ok
    ("Gpio", "GetDir"):         0x0152,   # rs1=pin                       rd=dir
    ("Gpio", "SetPull"):        0x0153,   # rs1=pin rs2=pull(0=none,1=up,2=down) rd=ok
    ("Gpio", "GetPull"):        0x0154,   # rs1=pin                       rd=pull
    ("Gpio", "Write"):          0x0155,   # rs1=pin rs2=value[0,1024]     rd=ok
    ("Gpio", "Read"):           0x0156,   # rs1=pin                       rd=value[0,1024]
    # Capsule runtime hooks (0x0160-0x0167): pack-aware card store + intra-capsule
    # IPC FIFOs that a capsule process calls at runtime. Provider-backed (browser
    # PiosCapsuleStore reference / PIOS real backend). Manifest building + the
    # source/bytecode card pairing are the picocapsule lib (authoring time), not
    # runtime hooks. FIFOs are declared in the manifest; Fifo.Open resolves by name.
    # See docs/PIOS_CAPSULE_HANDOFF.md.
    ("Pack", "Use"):            0x0160,   # rs1=pack                      rd=ok
    ("Card", "Read"):           0x0161,   # rs1=card                      rd=span
    ("Card", "Write"):          0x0162,   # rs1=card  rs2=span            rd=ok
    ("Card", "Address"):        0x0163,   # rs1=pack  rs2=card            rd=span (pack/card)
    ("Fifo", "Open"):           0x0164,   # rs1=name-span                 rd=handle
    ("Fifo", "Send"):           0x0165,   # rs1=handle rs2=span           rd=ok
    ("Fifo", "Recv"):           0x0166,   # rs1=handle                    rd=span
    ("Fifo", "Poll"):           0x0167,   # rs1=handle                    rd=count
    # Device enumeration/lifecycle (0x0168-0x016B) + Stream DMA-ring (0x0170-0x0175).
    # Streaming hardware is structurally identical to Req/Resp but over a DMA ring:
    # Device.* opens a named device, Stream.* is thin sugar over Lease+Descriptor+
    # WaitIRQ. Provider-backed: the browser ships a deterministic ring emulator
    # (vm/picovm.js + HostApi), PIOS the real DMA driver. See PIOS_DEVICE_BINDINGS.md.
    ("Device", "Open"):         0x0168,   # rs1=id-span rs2=cfg           rd=devHandle
    ("Device", "Caps"):         0x0169,   # rs1=devHandle                 rd=capsBits
    ("Device", "Close"):        0x016A,   # rs1=devHandle                 rd=ok
    ("Device", "Status"):       0x016B,   # rs1=devHandle                 rd=status
    ("Stream", "Open"):         0x0170,   # rs1=devHandle rs2=ringCfg     rd=streamHandle
    ("Stream", "Next"):         0x0171,   # rs1=streamHandle              rd=leaseHandle (0=EOF)
    ("Stream", "Span"):         0x0172,   # rs1=leaseHandle               rd=span (zero-copy)
    ("Stream", "Submit"):       0x0173,   # rs1=streamHandle rs2=lease    rd=ok (TX)
    ("Stream", "Release"):      0x0174,   # rs1=leaseHandle               rd=ok (RX return-to-ring)
    ("Stream", "Close"):        0x0175,   # rs1=streamHandle              rd=ok
    ("Stream", "SetSlice"):     0x0176,   # rs1=offset rs2=len            rd=ok
    ("Stream", "Slice"):        0x0177,   # rs1=leaseHandle               rd=span (window)
    # Assert/PSUnit (0x0178-0x017C): a PicoScript-authored unit/smoke test harness.
    # Host-injected (counters in the host), like Gpio/Stream; deterministic integer
    # logic so Python VM == JS VM. Tests call Assert.Eq/True; the runner reads
    # Assert.Failed()/Count(). See psunit.py + tests/psunit/.
    ("Assert", "Eq"):           0x0178,   # rs1=actual rs2=expected       rd=1 if equal else 0
    ("Assert", "True"):         0x0179,   # rs1=cond                      rd=1 if cond!=0 else 0
    ("Assert", "Count"):        0x017A,   #                               rd=total assertions
    ("Assert", "Failed"):       0x017B,   #                               rd=failed assertions
    ("Assert", "Reset"):        0x017C,   #                               rd=0 (clears counters)
    # Event (0x0180-0x0186): an in-runtime event queue -- the reactive core for GUIs
    # and async I/O. A program loops on Event.Next() (mirrors Stream.Next leases),
    # dispatching on Type/Target and reading the Data span. Post enqueues; the host
    # (browser/PIOS) injects external events (clicks, keys, timers) through the same
    # queue. Deterministic reference queue so Python VM == JS VM; CAP_EVENT-gated.
    ("Event", "Post"):          0x0180,   # rs1=type rs2=target           rd=eventId
    ("Event", "Next"):          0x0181,   #                               rd=eventId (0=queue empty)
    ("Event", "Type"):          0x0182,   # rs1=eventId                   rd=type
    ("Event", "Target"):        0x0183,   # rs1=eventId                   rd=target
    ("Event", "Data"):          0x0184,   # rs1=eventId                   rd=span (0=none)
    ("Event", "SetData"):       0x0185,   # rs1=eventId rs2=span          rd=ok
    ("Event", "Count"):         0x0186,   #                               rd=pending count
    ("Event", "SetSlice"):      0x01B3,   # rs1=offset rs2=len            rd=ok
    ("Event", "DataSlice"):     0x01B4,   # rs1=eventId                   rd=span (window)
    ("Event", "DataLen"):       0x01B5,   # rs1=eventId                   rd=len
    # Ui (0x0188-0x0193): a retained scene tree for a clean, minimal remote
    # windowing protocol (RDP/X spirit, but tiny). Build a window + boxes/text/
    # controls, then Ui.Serialize emits the compact deterministic PicoWire binary
    # (a thin client renders it and posts user events back through Event.*). The
    # tree + serializer live in the runtime (Python VM == JS VM byte-identical);
    # CAP_UI-gated. Control kinds: 1=window 2=panel 3=label 4=button 5=textbox
    # 6=checkbox. See docs/PICO_UI.md.
    ("Ui", "Window"):           0x0188,   # rs1=title-span                rd=node (root)
    ("Ui", "Panel"):            0x0189,   # rs1=parent                    rd=node
    ("Ui", "Label"):            0x018A,   # rs1=parent rs2=text-span      rd=node
    ("Ui", "Button"):           0x018B,   # rs1=parent rs2=text-span      rd=node
    ("Ui", "TextBox"):          0x018C,   # rs1=parent rs2=text-span      rd=node
    ("Ui", "Checkbox"):         0x018D,   # rs1=parent rs2=text-span      rd=node
    ("Ui", "Pos"):              0x018E,   # rs1=node rs2=(x<<16|y)        rd=ok
    ("Ui", "Size"):             0x018F,   # rs1=node rs2=(w<<16|h)        rd=ok
    ("Ui", "SetText"):          0x0190,   # rs1=node rs2=text-span        rd=ok
    ("Ui", "SetId"):            0x0191,   # rs1=node rs2=controlId        rd=ok
    ("Ui", "SetValue"):         0x0192,   # rs1=node rs2=value            rd=ok
    ("Ui", "Serialize"):        0x0193,   # rs1=root                      rd=span (PicoWire bytes)
}
HOST_HOOK_NAMES = {v: k for k, v in HOST_HOOK_CODES.items()}

# Common HTTP status codes
HTTP_STATUS = {
    200: 0x8000 | 200,
    201: 0x8000 | 201,
    204: 0x8000 | 204,
    301: 0x8000 | 301,
    302: 0x8000 | 302,
    400: 0x8000 | 400,
    401: 0x8000 | 401,
    403: 0x8000 | 403,
    404: 0x8000 | 404,
    500: 0x8000 | 500,
}

# Content-Type shortcuts
CONTENT_TYPES = {
    "text/html":        0xA000,
    "text/plain":       0xA001,
    "application/json": 0xA002,
    "text/css":         0xA003,
    "text/javascript":  0xA004,
    "image/png":        0xA005,
    "image/jpeg":       0xA006,
    "application/octet-stream": 0xA007,
}

# Branch condition name mapping
CONDITION_MAP = {
    "EQ": COND_EQ, "NE": COND_NE, "LT": COND_LT, "GT": COND_GT,
    "LE": COND_LE, "GE": COND_GE, "Z": COND_Z,   "NZ": COND_NZ,
    "EOF": COND_EOF, "ERR": COND_ERR,
}

DSP_BASIC_NAMES = {
    DSP_MATMUL: "MATMUL", DSP_SOFTMAX: "SOFTMAX", DSP_DOT: "DOT",
    DSP_SCALE: "SCALE", DSP_RELU: "RELU", DSP_NORM: "NORM",
    DSP_TOPK: "TOPK", DSP_GELU: "GELU", DSP_TRANSPOSE: "TRANSPOSE",
    DSP_VADD: "VADD", DSP_EMBED: "EMBED", DSP_QUANT: "QUANT",
    DSP_DEQUANT: "DEQUANT", DSP_MASK: "MASK", DSP_CONCAT: "CONCAT",
    DSP_SPLIT: "SPLIT",
}
DSP_BASIC_TO_SUBOP = {name: subop for subop, name in DSP_BASIC_NAMES.items()}
BASIC_CONTENT_TYPES = {name.upper(): imm for name, imm in CONTENT_TYPES.items()}


# ═══════════════════════════════════════════════════════════════════════
# Card address encoding
# ═══════════════════════════════════════════════════════════════════════

def encode_card_addr(tenant, pack, card):
    """Encode tenant/pack/card into 16-bit address.

    Layout: [15:11]=tenant(32) [10:5]=pack(64) [4:0]=card(32)
    """
    assert 0 <= tenant <= 31, f"tenant {tenant} out of range (0-31)"
    assert 0 <= pack <= 63, f"pack {pack} out of range (0-63)"
    assert 0 <= card <= 31, f"card {card} out of range (0-31)"
    return (tenant << 11) | (pack << 5) | card


def decode_card_addr(addr16):
    """Decode 16-bit address into tenant/pack/card."""
    tenant = (addr16 >> 11) & 0x1F
    pack = (addr16 >> 5) & 0x3F
    card = addr16 & 0x1F
    return tenant, pack, card


# ═══════════════════════════════════════════════════════════════════════
# Compiler: source text → bytecode
# ═══════════════════════════════════════════════════════════════════════

def parse_register(s):
    """Parse 'R0'-'R15' into register number."""
    s = s.strip()
    if s.upper().startswith("R"):
        n = int(s[1:])
        assert 0 <= n <= 15, f"Register {s} out of range"
        return n
    raise ValueError(f"Expected register (R0-R15), got '{s}'")


def parse_arg(s, labels=None):
    """Parse an argument: register, integer, label, or string."""
    s = s.strip()
    if s.upper().startswith("R"):
        return ("reg", int(s[1:]))
    if s.startswith(":"):
        return ("label", s[1:])
    if s.startswith('"') or s.startswith("'"):
        return ("str", s[1:-1])
    if s.startswith("Stream."):
        return ("stream", s.split(".")[1])
    if s in CONDITION_MAP:
        return ("cond", CONDITION_MAP[s])
    try:
        return ("imm", int(s))
    except ValueError:
        pass
    # Check content-type strings
    if s in CONTENT_TYPES:
        return ("ctype", CONTENT_TYPES[s])
    return ("sym", s)


def encode_instruction(opcode, rd=0, rs1=0, rs2=0, imm16=0):
    """Encode a 32-bit PicoScript instruction."""
    return (opcode << 28) | (rd << 24) | (rs1 << 20) | (rs2 << 16) | (imm16 & 0xFFFF)


def _split_basic_line_number(line):
    parts = line.split(None, 1)
    if parts and parts[0].isdigit():
        return int(parts[0]), parts[1].strip() if len(parts) > 1 else ""
    return None, line


def _normalize_basic_name(name):
    return name.replace("_", "").upper()


def _canonical_namespace(name):
    wanted = name.upper()
    for namespace in NAMESPACE_MAP:
        if namespace.upper() == wanted:
            return namespace
    return None


def _canonical_method(namespace, method):
    wanted = _normalize_basic_name(method)
    for candidate in NAMESPACE_MAP.get(namespace, {}):
        if _normalize_basic_name(candidate) == wanted:
            return candidate
    return None


def _basic_namespaces():
    return {namespace.upper() for namespace in NAMESPACE_MAP} | {"REM"}


def _looks_basic_statement(line):
    first = line.split(None, 1)[0].upper() if line.split(None, 1) else ""
    return first in _basic_namespaces()


class Compiler:
    """Single-pass compiler: PicoScript source → bytecode.

    Two passes:
      1. Collect labels and their instruction indices
      2. Emit bytecode with resolved label offsets
    """

    def __init__(self):
        self.instructions = []
        self.labels = {}
        self.source_lines = []
        self.basic_line_to_pc = {}
        self.instruction_count = 0

    def compile(self, source):
        """Compile source text to list of 32-bit instruction words."""
        self.instructions = []
        self.labels = {}
        self.source_lines = []
        self.basic_line_to_pc = {}
        self.instruction_count = 0
        lines = source.strip().split("\n")

        # Pass 1: collect labels, strip comments/blanks
        clean_lines = []
        pc = 0
        last_basic_line = None
        for line in lines:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith(":"):
                label = line[1:].rstrip(";").strip()
                if not label:
                    raise SyntaxError(f"Empty label at instruction {pc}")
                if label in self.labels:
                    raise SyntaxError(f"Duplicate label ':{label}' at instruction {pc}")
                self.labels[label] = pc
                continue
            basic_line, line = _split_basic_line_number(line)
            if basic_line is not None:
                if last_basic_line is not None and basic_line <= last_basic_line:
                    raise SyntaxError("BASIC line numbers must be unique and ascending")
                last_basic_line = basic_line
                self.basic_line_to_pc[basic_line] = pc
            clean_lines.append(line)
            pc += 1
        self.instruction_count = pc

        # Pass 2: compile each statement
        for i, line in enumerate(clean_lines):
            self.source_lines.append(line)
            word = self._compile_statement(line, i)
            self.instructions.append(word)

        return self.instructions

    def _compile_statement(self, line, pc):
        """Compile a single Namespace.Method(args); statement."""
        # Strip trailing semicolon
        line = line.rstrip(";").strip()

        if _looks_basic_statement(line):
            return self._compile_basic_statement(line, pc)

        # Parse Namespace.Method(args)
        try:
            dot_pos = line.index(".")
            paren_pos = line.index("(")
            close_pos = line.rindex(")")
        except ValueError as exc:
            raise SyntaxError(
                "Expected C-style Namespace.Method(...) or BASIC-style input "
                f"at instruction {pc}: {line}"
            ) from exc
        if dot_pos > paren_pos:
            raise SyntaxError(
                "Expected C-style Namespace.Method(...) or BASIC-style input "
                f"at instruction {pc}: {line}"
            )
        namespace = line[:dot_pos]
        method = line[dot_pos+1:paren_pos]
        args_str = line[paren_pos+1:close_pos]
        args = [a.strip() for a in args_str.split(",") if a.strip()] if args_str.strip() else []

        # Look up opcode
        ns_map = NAMESPACE_MAP.get(namespace)
        if ns_map is None:
            raise SyntaxError(f"Unknown namespace '{namespace}' at line {pc}: {line}")
        op_entry = ns_map.get(method)
        if op_entry is None:
            raise SyntaxError(f"Unknown method '{namespace}.{method}' at line {pc}: {line}")

        # Handle DSP (tuple: opcode + sub-op)
        if isinstance(op_entry, tuple):
            opcode, sub_op = op_entry
            return self._compile_dsp(sub_op, args, pc)

        opcode = op_entry

        # Dispatch by namespace
        if namespace == "Storage":
            if method in ("Load", "Save", "Pipe"):
                return self._compile_storage(opcode, method, args, pc)
            return self._compile_host_hook(namespace, method, args, pc)
        elif namespace == "Thread":
            return self._compile_thread(opcode, method, args, pc)
        elif namespace == "Math":
            return self._compile_math(opcode, method, args, pc)
        elif namespace == "Flow":
            return self._compile_flow(opcode, method, args, pc)
        elif namespace == "Net":
            return self._compile_net(method, args, pc)
        elif namespace in ("Kernel", "Queue", "Random", "Memory", "Span", "Descriptor", "Lease", "Context", "Io"):
            return self._compile_host_hook(namespace, method, args, pc)
        else:
            raise SyntaxError(f"Unhandled namespace '{namespace}' at line {pc}")

    def _compile_storage(self, opcode, method, args, pc):
        """Storage.Load/Save/Pipe(tenant, pack, card, reg/stream)"""
        if len(args) != 4:
            raise SyntaxError(f"Storage.{method} requires 4 args (tenant, pack, card, target)")
        tenant = int(args[0])
        pack = int(args[1])
        card = int(args[2])
        addr16 = encode_card_addr(tenant, pack, card)
        target = parse_arg(args[3])
        rd = target[1] if target[0] == "reg" else 0
        return encode_instruction(opcode, rd=rd, imm16=addr16)

    def _compile_thread(self, opcode, method, args, pc):
        """Thread.Skip() / Thread.Wait() / Thread.Raise(channel)"""
        if method == "Raise" and args:
            channel = int(args[0])
            return encode_instruction(opcode, imm16=channel)
        return encode_instruction(opcode)

    def _compile_math(self, opcode, method, args, pc):
        """Math.Add(dest, src, value) / Math.Inc(reg)"""
        if method == "Inc":
            rd = parse_register(args[0])
            return encode_instruction(opcode, rd=rd)
        # ADD/SUB/MUL/DIV: dest, src, imm_or_reg
        rd = parse_register(args[0])
        rs1 = parse_register(args[1])
        third = parse_arg(args[2])
        if third[0] == "imm":
            return encode_instruction(opcode, rd=rd, rs1=rs1, imm16=third[1])
        elif third[0] == "reg":
            return encode_instruction(opcode, rd=rd, rs1=rs1, rs2=ADDR_REGISTER, imm16=third[1])
        raise SyntaxError(f"Math.{method}: third arg must be immediate or register")

    def _compile_flow(self, opcode, method, args, pc):
        """Flow.Jump(:label) / Flow.Branch(cond, Ra, Rb, :label) / Flow.Call(:label) / Flow.Return()"""
        if method == "Return":
            return encode_instruction(OP_RETURN)
        if method == "Jump":
            label = args[0].lstrip(":")
            target_pc = self._resolve_label(label, pc)
            return encode_instruction(OP_JUMP, imm16=target_pc)
        if method == "Call":
            label = args[0].lstrip(":")
            target_pc = self._resolve_label(label, pc)
            return encode_instruction(OP_CALL, imm16=target_pc)
        if method == "Branch":
            cond = CONDITION_MAP[args[0]]
            rd = parse_register(args[1])
            rs1 = parse_register(args[2])
            label = args[3].lstrip(":")
            target_pc = self._resolve_label(label, pc)
            offset = target_pc - pc
            imm16 = offset & 0xFFFF
            return encode_instruction(OP_BRANCH, rd=rd, rs1=rs1, rs2=cond, imm16=imm16)
        raise SyntaxError(f"Unknown Flow method: {method}")

    def _resolve_label(self, label, pc):
        if label.isdigit():
            target_pc = int(label, 10)
            if 0 <= target_pc < self.instruction_count:
                return target_pc
            raise SyntaxError(f"Instruction target ':{label}' out of range at instruction {pc}")
        if label not in self.labels:
            raise SyntaxError(f"Unknown label ':{label}' at instruction {pc}")
        return self.labels[label]

    def _resolve_basic_line(self, target_line, pc):
        try:
            line_number = int(target_line, 0)
        except ValueError as exc:
            raise SyntaxError(f"Expected BASIC line number at instruction {pc}: {target_line}") from exc
        if line_number not in self.basic_line_to_pc:
            raise SyntaxError(f"Unknown BASIC line {line_number} at instruction {pc}")
        return self.basic_line_to_pc[line_number]

    def _compile_net(self, method, args, pc):
        """Net.Status(200) / Net.Type("text/html") / Net.Body() / Net.Close()"""
        if method == "Status":
            code = int(args[0])
            return encode_instruction(OP_NOOP, imm16=NET_STATUS_BASE | code)
        elif method == "Type":
            ct = args[0].strip('"').strip("'")
            try:
                imm = int(ct, 0)
            except ValueError:
                imm = CONTENT_TYPES.get(ct)
            if imm is None:
                raise SyntaxError(f"Unknown content type '{ct}'")
            return encode_instruction(OP_NOOP, imm16=imm)
        elif method == "Header":
            imm = int(args[0], 0) if args else NET_HEADER_BASE
            return encode_instruction(OP_NOOP, imm16=imm)
        elif method == "Body":
            return encode_instruction(OP_NOOP, imm16=NET_BODY_MARKER)
        elif method == "Close":
            return encode_instruction(OP_NOOP, imm16=NET_CLOSE_MARKER)
        raise SyntaxError(f"Unknown Net method: {method}")

    def _compile_dsp(self, sub_op, args, pc):
        """Dsp.MatMul(dest, src) / Dsp.Dot(dest, srcA, srcB) etc."""
        rd = parse_register(args[0]) if args else 0
        rs1 = parse_register(args[1]) if len(args) > 1 else 0
        imm16 = 0
        if len(args) > 2:
            third = parse_arg(args[2])
            if third[0] == "reg":
                imm16 = third[1]
            elif third[0] == "imm":
                imm16 = third[1]
        return encode_instruction(OP_DSP, rd=rd, rs1=rs1, rs2=sub_op, imm16=imm16)

    def _compile_basic_statement(self, line, pc):
        """Compile BASIC-style statements such as '10 FLOW BRANCH, NZ, R0, R0, 10'."""
        head, sep, rest = line.partition(",")
        parts = head.strip().upper().split()
        if not parts:
            raise SyntaxError(f"Empty BASIC statement at instruction {pc}")
        namespace_token = parts[0]
        method_token = parts[1] if len(parts) > 1 else ""
        args = [a.strip() for a in rest.split(",") if a.strip()] if sep else []

        if namespace_token == "REM":
            return encode_instruction(OP_NOOP)
        if namespace_token == "NET":
            return self._compile_basic_net(method_token, args, pc)
        if namespace_token == "THREAD":
            return self._compile_basic_thread(method_token, args, pc)
        if namespace_token == "STORAGE":
            if method_token in ("LOAD", "SAVE", "PIPE"):
                return self._compile_basic_storage(method_token, args, pc)
            return self._compile_basic_host_hook(namespace_token, method_token, args, pc)
        if namespace_token == "MATH":
            return self._compile_basic_math(method_token, args, pc)
        if namespace_token == "FLOW":
            return self._compile_basic_flow(method_token, args, pc)
        if namespace_token == "DSP":
            return self._compile_basic_dsp(method_token, args, pc)
        return self._compile_basic_host_hook(namespace_token, method_token, args, pc)

    def _compile_basic_net(self, method, args, pc):
        if method == "STATUS":
            return encode_instruction(OP_NOOP, imm16=NET_STATUS_BASE | int(args[0], 0))
        if method == "TYPE":
            token = args[0].strip('"').strip("'").upper()
            if token.startswith("TYPE/"):
                imm = NET_TYPE_BASE | int(token.split("/", 1)[1], 0)
            elif token in BASIC_CONTENT_TYPES:
                imm = BASIC_CONTENT_TYPES[token]
            else:
                raise SyntaxError(f"Unknown BASIC content type '{args[0]}' at instruction {pc}")
            return encode_instruction(OP_NOOP, imm16=imm)
        if method == "HEADER":
            imm = int(args[0], 0) if args else NET_HEADER_BASE
            return encode_instruction(OP_NOOP, imm16=imm)
        if method == "BODY":
            return encode_instruction(OP_NOOP, imm16=NET_BODY_MARKER)
        if method == "CLOSE":
            return encode_instruction(OP_NOOP, imm16=NET_CLOSE_MARKER)
        raise SyntaxError(f"Unknown BASIC NET method '{method}' at instruction {pc}")

    def _compile_basic_thread(self, method, args, pc):
        if method == "SKIP":
            return encode_instruction(OP_NOOP)
        if method == "WAIT":
            return encode_instruction(OP_WAIT)
        if method == "RAISE":
            return encode_instruction(OP_RAISE, imm16=int(args[0], 0))
        raise SyntaxError(f"Unknown BASIC THREAD method '{method}' at instruction {pc}")

    def _compile_basic_storage(self, method, args, pc):
        opcode = {"LOAD": OP_LOAD, "SAVE": OP_SAVE, "PIPE": OP_PIPE}.get(method)
        if opcode is None:
            raise SyntaxError(f"Unknown BASIC STORAGE method '{method}' at instruction {pc}")
        if len(args) != 4:
            raise SyntaxError(f"BASIC STORAGE {method} requires tenant, pack, card, register")
        tenant, pack, card = (int(args[i], 0) for i in range(3))
        rd = parse_register(args[3])
        return encode_instruction(opcode, rd=rd, imm16=encode_card_addr(tenant, pack, card))

    def _compile_basic_math(self, method, args, pc):
        opcode = {"ADD": OP_ADD, "SUB": OP_SUB, "MUL": OP_MUL, "DIV": OP_DIV}.get(method)
        if method == "INC":
            return encode_instruction(OP_INC, rd=parse_register(args[0]))
        if opcode is None:
            raise SyntaxError(f"Unknown BASIC MATH method '{method}' at instruction {pc}")
        rd = parse_register(args[0])
        rs1 = parse_register(args[1])
        third = parse_arg(args[2])
        if third[0] == "reg":
            return encode_instruction(opcode, rd=rd, rs1=rs1, rs2=ADDR_REGISTER, imm16=third[1])
        if third[0] == "imm":
            return encode_instruction(opcode, rd=rd, rs1=rs1, imm16=third[1])
        raise SyntaxError(f"BASIC MATH {method}: third arg must be immediate or register")

    def _compile_basic_flow(self, method, args, pc):
        if method == "RETURN":
            return encode_instruction(OP_RETURN)
        if method == "JUMP":
            return encode_instruction(OP_JUMP, imm16=self._resolve_basic_line(args[0], pc))
        if method == "CALL":
            return encode_instruction(OP_CALL, imm16=self._resolve_basic_line(args[0], pc))
        if method == "BRANCH":
            cond = CONDITION_MAP[args[0].upper()]
            rd = parse_register(args[1])
            rs1 = parse_register(args[2])
            target_pc = self._resolve_basic_line(args[3], pc)
            return encode_instruction(OP_BRANCH, rd=rd, rs1=rs1, rs2=cond, imm16=(target_pc - pc) & 0xFFFF)
        raise SyntaxError(f"Unknown BASIC FLOW method '{method}' at instruction {pc}")

    def _compile_basic_dsp(self, method, args, pc):
        if method not in DSP_BASIC_TO_SUBOP:
            raise SyntaxError(f"Unknown BASIC DSP method '{method}' at instruction {pc}")
        rd = parse_register(args[0]) if args else 0
        rs1 = parse_register(args[1]) if len(args) > 1 else 0
        imm16 = 0
        if len(args) > 2:
            third = parse_arg(args[2])
            if third[0] in ("imm", "reg"):
                imm16 = third[1]
            else:
                raise SyntaxError(f"BASIC DSP {method}: third arg must be immediate or register")
        return encode_instruction(OP_DSP, rd=rd, rs1=rs1, rs2=DSP_BASIC_TO_SUBOP[method], imm16=imm16)

    def _compile_basic_host_hook(self, namespace_token, method_token, args, pc):
        namespace = _canonical_namespace(namespace_token)
        if namespace is None:
            raise SyntaxError(f"Unknown BASIC namespace '{namespace_token}' at instruction {pc}")
        method = _canonical_method(namespace, method_token)
        if method is None:
            raise SyntaxError(f"Unknown BASIC {namespace} method '{method_token}' at instruction {pc}")
        return self._compile_host_hook(namespace, method, args, pc)

    def _compile_host_hook(self, namespace, method, args, pc):
        """Compile host-fillable primitives via reserved NOOP hook encodings.

        Hook layouts:
          Kernel.WaitIRQ([Rmask])           -> rs2=REG, rs1=Rmask (optional)
          Kernel.WaitSWIRQ([Rmask])         -> rs2=REG, rs1=Rmask (optional)
          Kernel.FireSWIRQ(Rpid)            -> rs2=REG, rs1=Rpid
          Queue.Dequeue(queue, Rdest)       -> rs1=queue id, rd=dest reg
          Queue.Enqueue(queue, Rsrc)        -> rs1=queue id, rd=src reg
          Queue.Depth(queue, Rdest)         -> rs1=queue id, rd=dest reg
          Random.U32(Rdest)                 -> rd=dest reg
          Memory.ArenaInit(Rbase, Rsize, Rarena)    -> rs1=Rbase, rs2=Rsize, rd=Rarena
          Memory.ArenaAlloc(Rarena, Rbytes, Rptr)   -> rs1=Rarena, rs2=Rbytes, rd=Rptr
          Memory.ArenaReset(Rarena)                 -> rs1=Rarena
          Memory.ArenaStats(Rarena, Rout)           -> rs1=Rarena, rd=Rout
          Span.Make(Rptr, Rlen, Rspan)              -> rs1=Rptr, rs2=Rlen, rd=Rspan
          Span.Slice(Rspan, Roff, Rout)             -> rs1=Rspan, rs2=Roff, rd=Rout
          Descriptor.Make(Rptr, Rlen, Rdesc)        -> rs1=Rptr, rs2=Rlen, rd=Rdesc
          Descriptor.SetFlags(Rdesc, Rflags)        -> rs1=Rdesc, rs2=Rflags
          Descriptor.GetPtr/GetLen/GetFlags(Rd, Rout)-> rs1=Rd, rd=Rout
          Lease.Acquire(Rtype, Rspan, Rlease)       -> rs1=Rtype, rs2=Rspan, rd=Rlease
          Lease.Release(Rlease)                      -> rs1=Rlease
          Lease.Validate(Rlease, Rout)               -> rs1=Rlease, rd=Rout
          Lease.GetSpan(Rlease, RoutSpan)            -> rs1=Rlease, rd=RoutSpan
          Lease.GetTypeHint(Rlease, RoutType)        -> rs1=Rlease, rd=RoutType
          Storage.GetSchemaForPack(RpackCtx, Rout)   -> rs1=RpackCtx, rd=Rout
          Storage.SetSchemaForPack(RpackCtx, Rschema)-> rs1=RpackCtx, rs2=Rschema
          Storage.AddCard(RpackCtx, Rcard, RoutId)   -> rs1=RpackCtx, rs2=Rcard, rd=RoutId
          Storage.UpdateCard(RpackCtx, Rid, Rcard)   -> rs1=RpackCtx, rs2=Rid, rd=Rcard
          Storage.DeleteCard(RpackCtx, Rid)          -> rs1=RpackCtx, rs2=Rid
          Storage.PatchCard(RpackCtx, Rid, Rpatch)   -> rs1=RpackCtx, rs2=Rid, rd=Rpatch
          Storage.ReadCard(RpackCtx, Rid, Rout)      -> rs1=RpackCtx, rs2=Rid, rd=Rout
          Storage.QueryCard(RpackCtx, Rquery, Rout)  -> rs1=RpackCtx, rs2=Rquery, rd=Rout
        """
        hook = HOST_HOOK_CODES.get((namespace, method))
        if hook is None:
            raise SyntaxError(f"Unknown {namespace} method: {method}")

        imm16 = (HOST_HOOK_BASE | hook) if hook <= 0xFF else (EXT_HOST_HOOK_BASE | (hook & 0x0FFF))
        rd = 0
        rs1 = 0
        rs2 = 0

        if namespace == "Kernel" and method in ("WaitIRQ", "WaitSWIRQ"):
            if len(args) > 1:
                raise SyntaxError(f"{namespace}.{method} accepts zero or one arg")
            if args:
                mode, value = parse_arg(args[0])
                if mode == "reg":
                    rs1 = value
                    rs2 = ADDR_REGISTER
                else:
                    raise SyntaxError(f"{namespace}.{method} arg must be register mask")
        elif namespace == "Kernel" and method == "FireSWIRQ":
            if len(args) != 1:
                raise SyntaxError("Kernel.FireSWIRQ requires one register arg (Rpid)")
            mode, value = parse_arg(args[0])
            if mode == "reg":
                rs1 = value
                rs2 = ADDR_REGISTER
            else:
                raise SyntaxError("Kernel.FireSWIRQ arg must be register")
        elif namespace == "Queue":
            if len(args) != 2:
                raise SyntaxError(f"Queue.{method} requires 2 args (queue_id, Rreg)")
            q_mode, q_val = parse_arg(args[0])
            if q_mode != "imm":
                raise SyntaxError(f"Queue.{method} queue_id must be immediate")
            reg_mode, reg_val = parse_arg(args[1])
            if reg_mode != "reg":
                raise SyntaxError(f"Queue.{method} second arg must be register")
            rs1 = q_val & 0xF
            rd = reg_val
        elif namespace == "Random" and method == "U32":
            if len(args) != 1:
                raise SyntaxError("Random.U32 requires one destination register arg")
            mode, value = parse_arg(args[0])
            if mode != "reg":
                raise SyntaxError("Random.U32 arg must be a register")
            rd = value
        elif namespace == "Context":
            if len(args) != 1:
                raise SyntaxError(f"Context.{method} requires one destination register arg")
            mode, value = parse_arg(args[0])
            if mode != "reg":
                raise SyntaxError(f"Context.{method} arg must be a register")
            rd = value
        elif namespace == "Io":
            if len(args) != 1:
                raise SyntaxError(f"Io.{method} requires one source register arg")
            mode, value = parse_arg(args[0])
            if mode != "reg":
                raise SyntaxError(f"Io.{method} arg must be a register")
            rs1 = value
        elif namespace == "Memory":
            if method == "ArenaInit":
                if len(args) != 3:
                    raise SyntaxError("Memory.ArenaInit requires 3 register args (Rbase, Rsize, Rarena)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1]); m2, v2 = parse_arg(args[2])
                if m0 != "reg" or m1 != "reg" or m2 != "reg":
                    raise SyntaxError("Memory.ArenaInit args must be registers")
                rs1, rs2, rd = v0, v1, v2
            elif method == "ArenaAlloc":
                if len(args) != 3:
                    raise SyntaxError("Memory.ArenaAlloc requires 3 register args (Rarena, Rbytes, RptrOut)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1]); m2, v2 = parse_arg(args[2])
                if m0 != "reg" or m1 != "reg" or m2 != "reg":
                    raise SyntaxError("Memory.ArenaAlloc args must be registers")
                rs1, rs2, rd = v0, v1, v2
            elif method == "ArenaReset":
                if len(args) != 1:
                    raise SyntaxError("Memory.ArenaReset requires one register arg (Rarena)")
                mode, value = parse_arg(args[0])
                if mode != "reg":
                    raise SyntaxError("Memory.ArenaReset arg must be a register")
                rs1 = value
            elif method == "ArenaStats":
                if len(args) != 2:
                    raise SyntaxError("Memory.ArenaStats requires 2 register args (Rarena, Rout)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1])
                if m0 != "reg" or m1 != "reg":
                    raise SyntaxError("Memory.ArenaStats args must be registers")
                rs1, rd = v0, v1
        elif namespace == "Span":
            if method == "Make":
                if len(args) != 3:
                    raise SyntaxError("Span.Make requires 3 register args (Rptr, Rlen, RspanOut)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1]); m2, v2 = parse_arg(args[2])
                if m0 != "reg" or m1 != "reg" or m2 != "reg":
                    raise SyntaxError("Span.Make args must be registers")
                rs1, rs2, rd = v0, v1, v2
            elif method == "Slice":
                if len(args) != 3:
                    raise SyntaxError("Span.Slice requires 3 register args (Rspan, Roff, Rout)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1]); m2, v2 = parse_arg(args[2])
                if m0 != "reg" or m1 != "reg" or m2 != "reg":
                    raise SyntaxError("Span.Slice args must be registers")
                rs1, rs2, rd = v0, v1, v2
            elif method == "Len":
                if len(args) != 2:
                    raise SyntaxError("Span.Len requires 2 register args (Rspan, Rout)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1])
                if m0 != "reg" or m1 != "reg":
                    raise SyntaxError("Span.Len args must be registers")
                rs1, rd = v0, v1
            elif method == "Get":
                if len(args) != 3:
                    raise SyntaxError("Span.Get requires 3 register args (Rspan, Rindex, Rout)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1]); m2, v2 = parse_arg(args[2])
                if m0 != "reg" or m1 != "reg" or m2 != "reg":
                    raise SyntaxError("Span.Get args must be registers")
                rs1, rs2, rd = v0, v1, v2
            elif method == "Materialize":
                if len(args) != 2:
                    raise SyntaxError("Span.Materialize requires 2 register args (Rspan, Rout)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1])
                if m0 != "reg" or m1 != "reg":
                    raise SyntaxError("Span.Materialize args must be registers")
                rs1, rd = v0, v1
        elif namespace == "Descriptor":
            if method == "Make":
                if len(args) != 3:
                    raise SyntaxError("Descriptor.Make requires 3 register args (Rptr, Rlen, RdescOut)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1]); m2, v2 = parse_arg(args[2])
                if m0 != "reg" or m1 != "reg" or m2 != "reg":
                    raise SyntaxError("Descriptor.Make args must be registers")
                rs1, rs2, rd = v0, v1, v2
            elif method == "SetFlags":
                if len(args) != 2:
                    raise SyntaxError("Descriptor.SetFlags requires 2 register args (Rdesc, Rflags)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1])
                if m0 != "reg" or m1 != "reg":
                    raise SyntaxError("Descriptor.SetFlags args must be registers")
                rs1, rs2 = v0, v1
            elif method in ("GetPtr", "GetLen", "GetFlags"):
                if len(args) != 2:
                    raise SyntaxError(f"Descriptor.{method} requires 2 register args (Rdesc, Rout)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1])
                if m0 != "reg" or m1 != "reg":
                    raise SyntaxError(f"Descriptor.{method} args must be registers")
                rs1, rd = v0, v1
        elif namespace == "Lease":
            if method == "Acquire":
                if len(args) != 3:
                    raise SyntaxError("Lease.Acquire requires 3 register args (Rtype, Rspan, RleaseOut)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1]); m2, v2 = parse_arg(args[2])
                if m0 != "reg" or m1 != "reg" or m2 != "reg":
                    raise SyntaxError("Lease.Acquire args must be registers")
                rs1, rs2, rd = v0, v1, v2
            elif method == "Release":
                if len(args) != 1:
                    raise SyntaxError("Lease.Release requires 1 register arg (Rlease)")
                m0, v0 = parse_arg(args[0])
                if m0 != "reg":
                    raise SyntaxError("Lease.Release arg must be a register")
                rs1 = v0
            elif method in ("Validate", "GetSpan", "GetTypeHint"):
                if len(args) != 2:
                    raise SyntaxError(f"Lease.{method} requires 2 register args (Rlease, Rout)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1])
                if m0 != "reg" or m1 != "reg":
                    raise SyntaxError(f"Lease.{method} args must be registers")
                rs1, rd = v0, v1
        elif namespace == "Storage":
            if method == "GetSchemaForPack":
                if len(args) != 2:
                    raise SyntaxError("Storage.GetSchemaForPack requires 2 register args (RpackCtx, Rout)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1])
                if m0 != "reg" or m1 != "reg":
                    raise SyntaxError("Storage.GetSchemaForPack args must be registers")
                rs1, rd = v0, v1
            elif method == "SetSchemaForPack":
                if len(args) != 2:
                    raise SyntaxError("Storage.SetSchemaForPack requires 2 register args (RpackCtx, Rschema)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1])
                if m0 != "reg" or m1 != "reg":
                    raise SyntaxError("Storage.SetSchemaForPack args must be registers")
                rs1, rs2 = v0, v1
            elif method in ("AddCard", "UpdateCard", "PatchCard", "ReadCard", "QueryCard"):
                if len(args) != 3:
                    raise SyntaxError(f"Storage.{method} requires 3 register args")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1]); m2, v2 = parse_arg(args[2])
                if m0 != "reg" or m1 != "reg" or m2 != "reg":
                    raise SyntaxError(f"Storage.{method} args must be registers")
                rs1, rs2, rd = v0, v1, v2
            elif method == "DeleteCard":
                if len(args) != 2:
                    raise SyntaxError("Storage.DeleteCard requires 2 register args (RpackCtx, RcardId)")
                m0, v0 = parse_arg(args[0]); m1, v1 = parse_arg(args[1])
                if m0 != "reg" or m1 != "reg":
                    raise SyntaxError("Storage.DeleteCard args must be registers")
                rs1, rs2 = v0, v1

        return encode_instruction(OP_NOOP, rd=rd, rs1=rs1, rs2=rs2, imm16=imm16)


# ═══════════════════════════════════════════════════════════════════════
# Disassembler: bytecode → source
# ═══════════════════════════════════════════════════════════════════════

OPCODE_TO_NS = {
    OP_NOOP: ("Thread", "Skip"), OP_LOAD: ("Storage", "Load"),
    OP_SAVE: ("Storage", "Save"), OP_PIPE: ("Storage", "Pipe"),
    OP_ADD: ("Math", "Add"), OP_SUB: ("Math", "Sub"),
    OP_MUL: ("Math", "Mul"), OP_DIV: ("Math", "Div"),
    OP_INC: ("Math", "Inc"), OP_JUMP: ("Flow", "Jump"),
    OP_BRANCH: ("Flow", "Branch"), OP_CALL: ("Flow", "Call"),
    OP_RETURN: ("Flow", "Return"), OP_WAIT: ("Thread", "Wait"),
    OP_RAISE: ("Thread", "Raise"), OP_DSP: ("Dsp", ""),
}

DSP_METHOD_NAMES = {
    DSP_MATMUL: "MatMul", DSP_SOFTMAX: "Softmax", DSP_DOT: "Dot",
    DSP_SCALE: "Scale", DSP_RELU: "Relu", DSP_NORM: "Norm",
    DSP_TOPK: "TopK", DSP_GELU: "Gelu", DSP_TRANSPOSE: "Transpose",
    DSP_VADD: "VAdd", DSP_EMBED: "Embed", DSP_QUANT: "Quant",
    DSP_DEQUANT: "Dequant", DSP_MASK: "Mask", DSP_CONCAT: "Concat",
    DSP_SPLIT: "Split",
}

COND_NAMES = {v: k for k, v in CONDITION_MAP.items()}


def disassemble(words):
    """Disassemble bytecode back to PicoScript source."""
    lines = []
    for i, word in enumerate(words):
        opcode = (word >> 28) & 0xF
        rd = (word >> 24) & 0xF
        rs1 = (word >> 20) & 0xF
        rs2 = (word >> 16) & 0xF
        imm16 = word & 0xFFFF

        # Check host hook NOOP encodings first.
        if opcode == OP_NOOP and ((imm16 & 0xFF00) == HOST_HOOK_BASE or (imm16 & 0xF000) == EXT_HOST_HOOK_BASE):
            hook_id = (imm16 & 0x0FFF) if (imm16 & 0xF000) == EXT_HOST_HOOK_BASE else (imm16 & 0x00FF)
            hook = HOST_HOOK_NAMES.get(hook_id)
            if hook:
                namespace, method = hook
                if namespace == "Kernel" and method in ("WaitIRQ", "WaitSWIRQ"):
                    if rs2 == ADDR_REGISTER:
                        lines.append(f"    {namespace}.{method}(R{rs1});")
                    elif rs2 == ADDR_IMMEDIATE:
                        lines.append(f"    {namespace}.{method}();")
                    else:
                        lines.append(f"    {namespace}.{method}();")
                elif namespace == "Kernel" and method == "FireSWIRQ":
                    if rs2 == ADDR_REGISTER:
                        lines.append(f"    Kernel.FireSWIRQ(R{rs1});")
                    else:
                        lines.append(f"    Kernel.FireSWIRQ({imm16});")
                elif namespace == "Queue":
                    lines.append(f"    Queue.{method}({rs1}, R{rd});")
                elif namespace == "Random":
                    lines.append(f"    Random.U32(R{rd});")
                elif namespace == "Memory":
                    if method in ("ArenaInit", "ArenaAlloc"):
                        lines.append(f"    Memory.{method}(R{rs1}, R{rs2}, R{rd});")
                    elif method == "ArenaReset":
                        lines.append(f"    Memory.ArenaReset(R{rs1});")
                    else:
                        lines.append(f"    Memory.ArenaStats(R{rs1}, R{rd});")
                elif namespace == "Span":
                    if method == "Make":
                        lines.append(f"    Span.Make(R{rs1}, R{rs2}, R{rd});")
                    else:
                        lines.append(f"    Span.Slice(R{rs1}, R{rs2}, R{rd});")
                elif namespace == "Descriptor":
                    if method == "Make":
                        lines.append(f"    Descriptor.Make(R{rs1}, R{rs2}, R{rd});")
                    elif method == "SetFlags":
                        lines.append(f"    Descriptor.SetFlags(R{rs1}, R{rs2});")
                    else:
                        lines.append(f"    Descriptor.{method}(R{rs1}, R{rd});")
                elif namespace == "Lease":
                    if method == "Acquire":
                        lines.append(f"    Lease.Acquire(R{rs1}, R{rs2}, R{rd});")
                    elif method == "Release":
                        lines.append(f"    Lease.Release(R{rs1});")
                    else:
                        lines.append(f"    Lease.{method}(R{rs1}, R{rd});")
                elif namespace == "Storage":
                    if method in ("GetSchemaForPack",):
                        lines.append(f"    Storage.GetSchemaForPack(R{rs1}, R{rd});")
                    elif method in ("SetSchemaForPack", "DeleteCard"):
                        lines.append(f"    Storage.{method}(R{rs1}, R{rs2});")
                    else:
                        lines.append(f"    Storage.{method}(R{rs1}, R{rs2}, R{rd});")
                else:
                    # Generic host hook (e.g. ext-page Http.*/Auth.*/Html.*/String.*/Json.*):
                    # faithfully decode the encoded registers in IL (args -> dst) order.
                    lines.append(f"    {namespace}.{method}(R{rs1}, R{rs2}, R{rd});")
                continue

        # Check for Net.* (NOOP with high bit set in imm16)
        if opcode == OP_NOOP and imm16 & 0x8000:
            if imm16 & 0xF000 == 0x8000:
                lines.append(f"    Net.Status({imm16 & 0x1FF});")
            elif imm16 & 0xF000 == 0xA000:
                ct_name = next((k for k, v in CONTENT_TYPES.items() if v == imm16), None)
                if ct_name is None:
                    lines.append(f"    Net.Type({imm16:#06x});")
                else:
                    lines.append(f'    Net.Type("{ct_name}");')
            elif imm16 == NET_BODY_MARKER:
                lines.append("    Net.Body();")
            elif imm16 == NET_CLOSE_MARKER:
                lines.append("    Net.Close();")
            else:
                lines.append(f"    Net.Header({imm16:#06x});")
            continue

        ns, method = OPCODE_TO_NS.get(opcode, ("?", "?"))

        if opcode == OP_DSP:
            method = DSP_METHOD_NAMES.get(rs2, f"Sub{rs2}")
            if imm16:
                lines.append(f"    Dsp.{method}(R{rd}, R{rs1}, {imm16});")
            else:
                lines.append(f"    Dsp.{method}(R{rd}, R{rs1});")
        elif opcode in (OP_LOAD, OP_SAVE, OP_PIPE):
            t, p, c = decode_card_addr(imm16)
            lines.append(f"    {ns}.{method}({t}, {p}, {c}, R{rd});")
        elif opcode in (OP_ADD, OP_SUB, OP_MUL, OP_DIV):
            if rs2 == ADDR_REGISTER:
                lines.append(f"    {ns}.{method}(R{rd}, R{rs1}, R{imm16});")
            else:
                lines.append(f"    {ns}.{method}(R{rd}, R{rs1}, {imm16});")
        elif opcode == OP_INC:
            lines.append(f"    Math.Inc(R{rd});")
        elif opcode == OP_JUMP:
            lines.append(f"    Flow.Jump(:{imm16});")
        elif opcode == OP_CALL:
            lines.append(f"    Flow.Call(:{imm16});")
        elif opcode == OP_BRANCH:
            cond = COND_NAMES.get(rs2, f"?{rs2}")
            offset = imm16 if imm16 < 0x8000 else imm16 - 0x10000
            target = i + offset
            lines.append(f"    Flow.Branch({cond}, R{rd}, R{rs1}, :{target});")
        elif opcode == OP_RETURN:
            lines.append("    Flow.Return();")
        elif opcode == OP_NOOP:
            lines.append("    Thread.Skip();")
        elif opcode == OP_WAIT:
            lines.append("    Thread.Wait();")
        elif opcode == OP_RAISE:
            lines.append(f"    Thread.Raise({imm16});")
        else:
            lines.append(f"    // ??? opcode={opcode:#x} rd={rd} rs1={rs1} rs2={rs2} imm={imm16}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Multi-syntax decompilers (bytecode → different source views)
# ═══════════════════════════════════════════════════════════════════════

# All syntaxes are CRLF terminated. The card stores only bytecode.
# These are CLIENT-SIDE view transforms — zero cost on the device.

def _decode_word(word):
    """Common decode helper."""
    return {
        "opcode": (word >> 28) & 0xF,
        "rd": (word >> 24) & 0xF,
        "rs1": (word >> 20) & 0xF,
        "rs2": (word >> 16) & 0xF,
        "imm16": word & 0xFFFF,
    }


def decompile_csharp(words):
    """Decompile to C# style: Namespace.Method(args);\\r\\n"""
    # This is the same as disassemble() but with CRLF
    return disassemble(words).replace("\n", "\r\n")


def decompile_basic(words):
    """Decompile to C64 BASIC style: BLOCK TEXT, SPACED, COMMA SEPARATED\\r\\n

    Example:
        10 NET STATUS, 200
        20 NET TYPE, TEXT/HTML
        30 NET BODY
        40 STORAGE PIPE, 0, 1, 0, R0
        50 FLOW RETURN
    """
    CT_BASIC = {v: k.upper() for k, v in CONTENT_TYPES.items()}

    lines = []
    for i, word in enumerate(words):
        d = _decode_word(word)
        opcode = d["opcode"]
        rd, rs1, rs2, imm16 = d["rd"], d["rs1"], d["rs2"], d["imm16"]
        lineno = (i + 1) * 10

        # Net.* (NOOP with high bit)
        if opcode == OP_NOOP and ((imm16 & 0xFF00) == HOST_HOOK_BASE or (imm16 & 0xF000) == EXT_HOST_HOOK_BASE):
            hook_id = (imm16 & 0x0FFF) if (imm16 & 0xF000) == EXT_HOST_HOOK_BASE else (imm16 & 0x00FF)
            hook = HOST_HOOK_NAMES.get(hook_id)
            if hook:
                namespace, method = hook
                if namespace == "Kernel":
                    if method in ("WaitIRQ", "WaitSWIRQ"):
                        basic_name = "WAIT_IRQ" if method == "WaitIRQ" else "WAIT_SW_IRQ"
                        if rs2 == ADDR_REGISTER:
                            lines.append(f"{lineno} KERNEL {basic_name}, R{rs1}")
                        else:
                            lines.append(f"{lineno} KERNEL {basic_name}")
                    else:
                        lines.append(f"{lineno} KERNEL FIRE_SW_IRQ, R{rs1}")
                elif namespace == "Queue":
                    lines.append(f"{lineno} QUEUE {method.upper()}, {rs1}, R{rd}")
                elif namespace == "Random":
                    lines.append(f"{lineno} RANDOM U32, R{rd}")
                elif namespace == "Memory":
                    if method in ("ArenaInit", "ArenaAlloc"):
                        lines.append(f"{lineno} MEMORY {method.upper()}, R{rs1}, R{rs2}, R{rd}")
                    elif method == "ArenaReset":
                        lines.append(f"{lineno} MEMORY ARENA_RESET, R{rs1}")
                    else:
                        lines.append(f"{lineno} MEMORY ARENA_STATS, R{rs1}, R{rd}")
                elif namespace == "Span":
                    if method == "Make":
                        lines.append(f"{lineno} SPAN MAKE, R{rs1}, R{rs2}, R{rd}")
                    else:
                        lines.append(f"{lineno} SPAN SLICE, R{rs1}, R{rs2}, R{rd}")
                elif namespace == "Descriptor":
                    if method == "Make":
                        lines.append(f"{lineno} DESCRIPTOR MAKE, R{rs1}, R{rs2}, R{rd}")
                    elif method == "SetFlags":
                        lines.append(f"{lineno} DESCRIPTOR SET_FLAGS, R{rs1}, R{rs2}")
                    else:
                        lines.append(f"{lineno} DESCRIPTOR {method.upper()}, R{rs1}, R{rd}")
                elif namespace == "Lease":
                    if method == "Acquire":
                        lines.append(f"{lineno} LEASE ACQUIRE, R{rs1}, R{rs2}, R{rd}")
                    elif method == "Release":
                        lines.append(f"{lineno} LEASE RELEASE, R{rs1}")
                    else:
                        lines.append(f"{lineno} LEASE {method.upper()}, R{rs1}, R{rd}")
                elif namespace == "Storage":
                    if method == "GetSchemaForPack":
                        lines.append(f"{lineno} STORAGE GET_SCHEMA_FOR_PACK, R{rs1}, R{rd}")
                    elif method in ("SetSchemaForPack", "DeleteCard"):
                        lines.append(f"{lineno} STORAGE {method.upper()}, R{rs1}, R{rs2}")
                    else:
                        lines.append(f"{lineno} STORAGE {method.upper()}, R{rs1}, R{rs2}, R{rd}")
                continue

        if opcode == OP_NOOP and imm16 & 0x8000:
            if imm16 & 0xF000 == 0x8000:
                lines.append(f"{lineno} NET STATUS, {imm16 & 0x1FF}")
            elif imm16 & 0xF000 == 0xA000:
                ct = CT_BASIC.get(imm16, f"TYPE/{imm16 & 0xFFF}")
                lines.append(f"{lineno} NET TYPE, {ct}")
            elif imm16 == NET_BODY_MARKER:
                lines.append(f"{lineno} NET BODY")
            elif imm16 == NET_CLOSE_MARKER:
                lines.append(f"{lineno} NET CLOSE")
            else:
                lines.append(f"{lineno} NET HEADER, {imm16:#06X}")
            continue

        if opcode == OP_NOOP:
            lines.append(f"{lineno} THREAD SKIP")
        elif opcode == OP_WAIT:
            lines.append(f"{lineno} THREAD WAIT")
        elif opcode == OP_RAISE:
            lines.append(f"{lineno} THREAD RAISE, {imm16}")
        elif opcode in (OP_LOAD, OP_SAVE, OP_PIPE):
            t, p, c = decode_card_addr(imm16)
            cmd = {OP_LOAD: "LOAD", OP_SAVE: "SAVE", OP_PIPE: "PIPE"}[opcode]
            lines.append(f"{lineno} STORAGE {cmd}, {t}, {p}, {c}, R{rd}")
        elif opcode in (OP_ADD, OP_SUB, OP_MUL, OP_DIV):
            cmd = {OP_ADD: "ADD", OP_SUB: "SUB", OP_MUL: "MUL", OP_DIV: "DIV"}[opcode]
            if rs2 == ADDR_REGISTER:
                lines.append(f"{lineno} MATH {cmd}, R{rd}, R{rs1}, R{imm16}")
            else:
                lines.append(f"{lineno} MATH {cmd}, R{rd}, R{rs1}, {imm16}")
        elif opcode == OP_INC:
            lines.append(f"{lineno} MATH INC, R{rd}")
        elif opcode == OP_JUMP:
            lines.append(f"{lineno} FLOW JUMP, {imm16 * 10 + 10}")
        elif opcode == OP_CALL:
            lines.append(f"{lineno} FLOW CALL, {imm16 * 10 + 10}")
        elif opcode == OP_BRANCH:
            cond = COND_NAMES.get(rs2, f"?{rs2}")
            offset = imm16 if imm16 < 0x8000 else imm16 - 0x10000
            target = (i + offset) * 10 + 10
            lines.append(f"{lineno} FLOW BRANCH, {cond}, R{rd}, R{rs1}, {target}")
        elif opcode == OP_RETURN:
            lines.append(f"{lineno} FLOW RETURN")
        elif opcode == OP_DSP:
            method = DSP_BASIC_NAMES.get(rs2, f"OP{rs2}")
            if imm16:
                lines.append(f"{lineno} DSP {method}, R{rd}, R{rs1}, {imm16}")
            else:
                lines.append(f"{lineno} DSP {method}, R{rd}, R{rs1}")
        else:
            lines.append(f"{lineno} REM UNKNOWN {word:08X}")

    return "\r\n".join(lines) + "\r\n"


def decompile_python(words):
    """Decompile to Python style: namespace.method(args)\\r\\n

    Example:
        net.status(200)
        net.type("text/html")
        net.body()
        storage.pipe(0, 1, 0, r0)
        flow.ret()
    """
    DSP_PY = {
        DSP_MATMUL: "matmul", DSP_SOFTMAX: "softmax", DSP_DOT: "dot",
        DSP_SCALE: "scale", DSP_RELU: "relu", DSP_NORM: "norm",
        DSP_TOPK: "topk", DSP_GELU: "gelu", DSP_TRANSPOSE: "transpose",
        DSP_VADD: "vadd", DSP_EMBED: "embed", DSP_QUANT: "quant",
        DSP_DEQUANT: "dequant", DSP_MASK: "mask", DSP_CONCAT: "concat",
        DSP_SPLIT: "split",
    }
    CT_REV = {v: k for k, v in CONTENT_TYPES.items()}

    lines = []
    for i, word in enumerate(words):
        d = _decode_word(word)
        opcode = d["opcode"]
        rd, rs1, rs2, imm16 = d["rd"], d["rs1"], d["rs2"], d["imm16"]

        if opcode == OP_NOOP and ((imm16 & 0xFF00) == HOST_HOOK_BASE or (imm16 & 0xF000) == EXT_HOST_HOOK_BASE):
            hook_id = (imm16 & 0x0FFF) if (imm16 & 0xF000) == EXT_HOST_HOOK_BASE else (imm16 & 0x00FF)
            hook = HOST_HOOK_NAMES.get(hook_id)
            if hook:
                namespace, method = hook
                if namespace == "Kernel":
                    if method in ("WaitIRQ", "WaitSWIRQ"):
                        py_name = "wait_irq" if method == "WaitIRQ" else "wait_sw_irq"
                        if rs2 == ADDR_REGISTER:
                            lines.append(f"kernel.{py_name}(r{rs1})")
                        else:
                            lines.append(f"kernel.{py_name}()")
                    else:
                        lines.append(f"kernel.fire_sw_irq(r{rs1})")
                elif namespace == "Queue":
                    lines.append(f"queue.{method.lower()}({rs1}, r{rd})")
                elif namespace == "Random":
                    lines.append(f"random.u32(r{rd})")
                elif namespace == "Memory":
                    snake = "".join([("_" + c.lower()) if c.isupper() else c for c in method]).lstrip("_")
                    if method in ("ArenaInit", "ArenaAlloc"):
                        lines.append(f"memory.{snake}(r{rs1}, r{rs2}, r{rd})")
                    elif method == "ArenaReset":
                        lines.append(f"memory.arena_reset(r{rs1})")
                    else:
                        lines.append(f"memory.arena_stats(r{rs1}, r{rd})")
                elif namespace == "Span":
                    if method == "Make":
                        lines.append(f"span.make(r{rs1}, r{rs2}, r{rd})")
                    else:
                        lines.append(f"span.slice(r{rs1}, r{rs2}, r{rd})")
                elif namespace == "Descriptor":
                    if method == "Make":
                        lines.append(f"descriptor.make(r{rs1}, r{rs2}, r{rd})")
                    elif method == "SetFlags":
                        lines.append(f"descriptor.set_flags(r{rs1}, r{rs2})")
                    else:
                        lines.append(f"descriptor.{method[0].lower() + method[1:]}(r{rs1}, r{rd})")
                elif namespace == "Lease":
                    if method == "Acquire":
                        lines.append(f"lease.acquire(r{rs1}, r{rs2}, r{rd})")
                    elif method == "Release":
                        lines.append(f"lease.release(r{rs1})")
                    else:
                        snake = "".join([("_" + c.lower()) if c.isupper() else c for c in method]).lstrip("_")
                        lines.append(f"lease.{snake}(r{rs1}, r{rd})")
                elif namespace == "Storage":
                    if method == "GetSchemaForPack":
                        lines.append(f"storage.get_schema_for_pack(r{rs1}, r{rd})")
                    elif method in ("SetSchemaForPack", "DeleteCard"):
                        if method == "SetSchemaForPack":
                            lines.append(f"storage.set_schema_for_pack(r{rs1}, r{rs2})")
                        else:
                            lines.append(f"storage.delete_card(r{rs1}, r{rs2})")
                    else:
                        snake = "".join([("_" + c.lower()) if c.isupper() else c for c in method]).lstrip("_")
                        lines.append(f"storage.{snake}(r{rs1}, r{rs2}, r{rd})")
                continue

        if opcode == OP_NOOP and imm16 & 0x8000:
            if imm16 & 0xF000 == 0x8000:
                lines.append(f"net.status({imm16 & 0x1FF})")
            elif imm16 & 0xF000 == 0xA000:
                ct = CT_REV.get(imm16, "application/octet-stream")
                lines.append(f'net.type("{ct}")')
            elif imm16 == NET_BODY_MARKER:
                lines.append("net.body()")
            elif imm16 == NET_CLOSE_MARKER:
                lines.append("net.close()")
            else:
                lines.append(f"net.header({imm16:#06x})")
            continue

        if opcode == OP_NOOP:
            lines.append("thread.skip()")
        elif opcode == OP_WAIT:
            lines.append("thread.wait()")
        elif opcode == OP_RAISE:
            lines.append(f"thread.raise_irq({imm16})")
        elif opcode in (OP_LOAD, OP_SAVE, OP_PIPE):
            t, p, c = decode_card_addr(imm16)
            cmd = {OP_LOAD: "load", OP_SAVE: "save", OP_PIPE: "pipe"}[opcode]
            lines.append(f"storage.{cmd}({t}, {p}, {c}, r{rd})")
        elif opcode in (OP_ADD, OP_SUB, OP_MUL, OP_DIV):
            cmd = {OP_ADD: "add", OP_SUB: "sub", OP_MUL: "mul", OP_DIV: "div"}[opcode]
            if rs2 == ADDR_REGISTER:
                lines.append(f"math.{cmd}(r{rd}, r{rs1}, r{imm16})")
            else:
                lines.append(f"math.{cmd}(r{rd}, r{rs1}, {imm16})")
        elif opcode == OP_INC:
            lines.append(f"math.inc(r{rd})")
        elif opcode == OP_JUMP:
            lines.append(f"flow.jump(:{imm16})")
        elif opcode == OP_CALL:
            lines.append(f"flow.call(:{imm16})")
        elif opcode == OP_BRANCH:
            cond = COND_NAMES.get(rs2, f"?{rs2}").lower()
            offset = imm16 if imm16 < 0x8000 else imm16 - 0x10000
            target = i + offset
            lines.append(f"flow.branch({cond}, r{rd}, r{rs1}, :{target})")
        elif opcode == OP_RETURN:
            lines.append("flow.ret()")
        elif opcode == OP_DSP:
            method = DSP_PY.get(rs2, f"op{rs2}")
            if imm16:
                lines.append(f"dsp.{method}(r{rd}, r{rs1}, {imm16})")
            else:
                lines.append(f"dsp.{method}(r{rd}, r{rs1})")
        else:
            lines.append(f"# unknown {word:08X}")

    return "\r\n".join(lines) + "\r\n"


def decompile_hex(words):
    """Raw hex dump: one 32-bit word per line.\\r\\n"""
    return "\r\n".join(f"{w:08X}" for w in words) + "\r\n"


# Available syntax modes
SYNTAXES = {
    "csharp": {"name": "C#",     "ext": ".pico",  "decompile": decompile_csharp},
    "basic":  {"name": "BASIC",  "ext": ".bas",   "decompile": decompile_basic},
    "python": {"name": "Python", "ext": ".py",    "decompile": decompile_python},
    "hex":    {"name": "Hex",    "ext": ".hex",   "decompile": decompile_hex},
}


# ═══════════════════════════════════════════════════════════════════════
# Example programs
# ═══════════════════════════════════════════════════════════════════════

EXAMPLE_HELLO = """\
// hello.pico -- Serve a static HTML page
Net.Status(200);
Net.Type("text/html");
Net.Body();
Storage.Pipe(0, 1, 0, R0);
Flow.Return();
"""

EXAMPLE_API = """\
// api_user.pico -- JSON API: GET /api/users/{id}
// R15 = connection context (card address from URL)
Net.Status(200);
Net.Type("application/json");
Net.Body();
Storage.Load(0, 2, 0, R0);
Flow.Branch(Z, R0, R0, :notfound);
Storage.Pipe(0, 2, 0, R0);
Flow.Return();
:notfound
Net.Status(404);
Net.Type("application/json");
Net.Body();
Storage.Pipe(0, 0, 31, R0);
Flow.Return();
"""

EXAMPLE_FILTER = """\
// filter.pico -- Loop over cards, emit matching ones
Math.Add(R0, R0, 100);
Math.Add(R1, R1, 110);
Math.Add(R2, R2, 50);
:loop
Storage.Load(0, 3, 0, R3);
Flow.Branch(LE, R3, R2, :skip);
Storage.Pipe(0, 3, 0, R0);
:skip
Math.Inc(R0);
Flow.Branch(LT, R0, R1, :loop);
Flow.Return();
"""

EXAMPLE_AI = """\
// inference.pico -- Dot product + softmax (vector search)
Storage.Load(0, 10, 0, R0);
Storage.Load(0, 10, 1, R1);
Dsp.Dot(R2, R0, R1);
Dsp.Softmax(R3, R2);
Dsp.TopK(R4, R3, 10);
Net.Status(200);
Net.Type("application/json");
Net.Body();
Storage.Save(0, 0, 30, R4);
Storage.Pipe(0, 0, 30, R4);
Flow.Return();
"""

EXAMPLE_EVENT = """\
// event.pico -- Wait for interrupt, then process
:wait_loop
Thread.Wait();
Storage.Load(0, 5, 0, R0);
Net.Status(200);
Net.Type("text/plain");
Net.Body();
Storage.Pipe(0, 5, 0, R0);
Flow.Jump(:wait_loop);
"""


# ═══════════════════════════════════════════════════════════════════════
# Main: demo compile + disassemble round-trip
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("PicoScript Language v1.0 -- Multi-Syntax Bytecode Views")
    print("=" * 65)
    print()
    print("Cards store ONLY bytecode (4 bytes per instruction).")
    print("The 'language' is a client-side view. Compile on save, decompile on load.")
    print("Same card, different view. Zero cost on device.")
    print()
    print("Available syntaxes:")
    for key, info in SYNTAXES.items():
        print(f"  {info['name']:8s} ({info['ext']})")
    print()

    # Compile the hello world example once
    print("─" * 65)
    print("EXAMPLE: Hello World — same bytecode, 4 different views")
    print("─" * 65)
    print()

    compiler = Compiler()
    bytecode = compiler.compile(EXAMPLE_HELLO)

    print(f"Card bytecode ({len(bytecode)} words, {len(bytecode)*4} bytes):")
    for i, word in enumerate(bytecode):
        print(f"  [{i:2d}] {word:08X}")
    print()

    print("┌─── View as C# (.pico) ────────────────────────────────────────┐")
    for line in decompile_csharp(bytecode).replace("\r\n", "\n").strip().split("\n"):
        print(f"│ {line:62s}│")
    print("└────────────────────────────────────────────────────────────────┘")
    print()

    print("┌─── View as BASIC (.bas) ───────────────────────────────────────┐")
    for line in decompile_basic(bytecode).replace("\r\n", "\n").strip().split("\n"):
        print(f"│ {line:62s}│")
    print("└────────────────────────────────────────────────────────────────┘")
    print()

    print("┌─── View as Python (.py) ───────────────────────────────────────┐")
    for line in decompile_python(bytecode).replace("\r\n", "\n").strip().split("\n"):
        print(f"│ {line:62s}│")
    print("└────────────────────────────────────────────────────────────────┘")
    print()

    print("┌─── View as Hex (.hex) ─────────────────────────────────────────┐")
    for line in decompile_hex(bytecode).replace("\r\n", "\n").strip().split("\n"):
        print(f"│ {line:62s}│")
    print("└────────────────────────────────────────────────────────────────┘")
    print()

    # Show a more complex example in BASIC
    print("─" * 65)
    print("EXAMPLE: Filter loop — BASIC style")
    print("─" * 65)
    print()
    compiler2 = Compiler()
    bytecode2 = compiler2.compile(EXAMPLE_FILTER)
    print(decompile_basic(bytecode2).replace("\r\n", "\n"))

    # Show AI example in all styles
    print("─" * 65)
    print("EXAMPLE: AI inference — all styles")
    print("─" * 65)
    print()
    compiler3 = Compiler()
    bytecode3 = compiler3.compile(EXAMPLE_AI)
    print("  C#:")
    print(decompile_csharp(bytecode3).replace("\r\n", "\n"))
    print("  BASIC:")
    print(decompile_basic(bytecode3).replace("\r\n", "\n"))
    print("  Python:")
    print(decompile_python(bytecode3).replace("\r\n", "\n"))

    # Architecture summary
    print("=" * 65)
    print("Architecture:")
    print()
    print("  ┌──────────────┐       ┌──────────────┐")
    print("  │ Browser/CLI  │       │   PicoWAL    │")
    print("  │              │       │   Device     │")
    print("  │ Edit in C#   │       │              │")
    print("  │ Edit in BASIC│  TCP  │  ┌────────┐  │")
    print("  │ Edit in Py   │◄─────►│  │BYTECODE│  │")
    print("  │              │       │  │ 4B/inst │  │")
    print("  │ Compile ─────┼──────►│  └────────┘  │")
    print("  │ Decompile ◄──┼───────┤   SD card    │")
    print("  └──────────────┘       └──────────────┘")
    print()
    print("  • Card stores bytecode only (never text)")
    print("  • Client picks display syntax (preference)")
    print("  • Compile: text → bytecode (trivial, 1:1)")
    print("  • Decompile: bytecode → text (trivial, 1:1)")
    print("  • Could even decompile in a DIFFERENT language than you wrote in")
    print("  • Write in Python, colleague reads it in C#. Same card.")
    print("  • All CRLF terminated for universal compatibility")
    print("=" * 65)
