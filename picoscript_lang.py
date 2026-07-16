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
        "Configure": OP_NOOP,
        "Compatible": OP_NOOP,
        "Rebuild": OP_NOOP,
        "SetFacet": OP_NOOP,
        "SetNumber": OP_NOOP,
        "ClearFields": OP_NOOP,
        "Facets": OP_NOOP,
        "FacetValue": OP_NOOP,
        "FacetCount": OP_NOOP,
        "Range": OP_NOOP,
        "Save": OP_NOOP,
        "Load": OP_NOOP,
        "JournalUpsert": OP_NOOP,
        "JournalDelete": OP_NOOP,
        "JournalFacet": OP_NOOP,
        "JournalNumber": OP_NOOP,
        "JournalReplay": OP_NOOP,
    },
    "Tensor": {
        "HasAccel": OP_NOOP,
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
        "HasFormat": OP_NOOP,
        "SetShape": OP_NOOP,
        "MatVecTernary": OP_NOOP,
        "MatVecBitmap": OP_NOOP,
        "MatVecBase3": OP_NOOP,
        "MatVecTernaryBlock": OP_NOOP,
        "MatVecBitmapBlock": OP_NOOP,
        "MatVecBase3Block": OP_NOOP,
    },
    "Quant": {
        "AbsMax": OP_NOOP,
        "QuantI8": OP_NOOP,
        "DequantI8": OP_NOOP,
        "ApplyScale": OP_NOOP,
        "GroupScale": OP_NOOP,
    },
    "Attention": {
        "SetShape": OP_NOOP,
        "Scores": OP_NOOP,
        "Mix": OP_NOOP,
        "Attend": OP_NOOP,
    },
    "Tokenizer": {
        "SetVocab": OP_NOOP,
        "EncodeBytes": OP_NOOP,
        "EncodeTrie": OP_NOOP,
        "DecodeBytes": OP_NOOP,
        "DecodeTrie": OP_NOOP,
        "Count": OP_NOOP,
        "Token": OP_NOOP,
    },
    "Model": {
        "SetConfig": OP_NOOP,
        "GetConfig": OP_NOOP,
        "TensorView": OP_NOOP,
        "TensorOffset": OP_NOOP,
        "TensorRows": OP_NOOP,
        "TensorCols": OP_NOOP,
        "TensorFormat": OP_NOOP,
        "ReadTensor": OP_NOOP,
        "ReadTensorRow": OP_NOOP,
        "SetBlock": OP_NOOP,
        "ReadTensorBlock": OP_NOOP,
        "MatVecI8Block": OP_NOOP,
    },
    "Kv": {
        "SetShape": OP_NOOP,
        "WriteK": OP_NOOP,
        "WriteV": OP_NOOP,
        "WriteKH": OP_NOOP,
        "WriteVH": OP_NOOP,
        "ReadK": OP_NOOP,
        "ReadV": OP_NOOP,
        "ReadKH": OP_NOOP,
        "ReadVH": OP_NOOP,
        "SetHead": OP_NOOP,
        "Len": OP_NOOP,
        "Clear": OP_NOOP,
    },
    "Sampling": {
        "ArgMax": OP_NOOP,
        "ArgMaxRows": OP_NOOP,
        "TopK": OP_NOOP,
        "Temperature": OP_NOOP,
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
        "Bool": OP_NOOP, "Null": OP_NOOP, "Raw": OP_NOOP, "Parse": OP_NOOP,
    },
    # Binary: PicoBinarySerializer (PSC1) card <-> Map, plus schema-driven BSO1
    # (BareMetalJsTools BareMetal.Binary) entity <-> Map for BMJS interop. See docs/MAP.md.
    "Binary": {
        "ParseCard": OP_NOOP, "SerializeCard": OP_NOOP,
        "ParseEntity": OP_NOOP, "SerializeEntity": OP_NOOP, "SetKey": OP_NOOP, "Verify": OP_NOOP,
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
        "Listen":  OP_NOOP,  # Raw socket: bind + listen
        "Accept":  OP_NOOP,  # Raw socket: accept connection
        "Read":    OP_NOOP,  # Raw socket: recv bytes
        "Write":   OP_NOOP,  # Raw socket: send bytes
        "Shutdown": OP_NOOP, # Raw socket: shutdown connection
        "PoolSize": OP_NOOP, # Configure worker pool size
        "Register": OP_NOOP, # Register ON event handler
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
    "TextRender": {
        "Raw": OP_NOOP,
        "Text": OP_NOOP,
        "Open": OP_NOOP,
        "Attr": OP_NOOP,
        "OpenEnd": OP_NOOP,
        "Close": OP_NOOP,
        "Empty": OP_NOOP,
        "Hole": OP_NOOP,
        "Br": OP_NOOP,
    },
    # OS-worker process lifecycle (0x0280-0x028B)
    "Process": {
        "Self": OP_NOOP,
        "Parent": OP_NOOP,
        "Spawn": OP_NOOP,
        "Exit": OP_NOOP,
        "Kill": OP_NOOP,
        "Status": OP_NOOP,
        "Wait": OP_NOOP,
        "Args": OP_NOOP,
    },
    "Env": {
        "Get": OP_NOOP,
        "Set": OP_NOOP,
        "Count": OP_NOOP,
        "Key": OP_NOOP,
    },
    # Timers and scheduler (0x0290-0x0294)
    "Timer": {
        "After": OP_NOOP,
        "Every": OP_NOOP,
        "Cancel": OP_NOOP,
        "Elapsed": OP_NOOP,
    },
    "Scheduler": {
        "Tick": OP_NOOP,
    },
    # Principal / Capability / Sandbox (0x02A0-0x02A6)
    "Principal": {
        "Current": OP_NOOP,
        "HasRole": OP_NOOP,
        "Claims": OP_NOOP,
    },
    "Capability": {
        "Has": OP_NOOP,
        "Request": OP_NOOP,
        "Drop": OP_NOOP,
    },
    "Sandbox": {
        "Deny": OP_NOOP,
    },
    # Error handling (0x02B0-0x02B5)
    "Error": {
        "Code": OP_NOOP,
        "Detail": OP_NOOP,
        "Resume": OP_NOOP,
        "Clear": OP_NOOP,
        "SetHandler": OP_NOOP,
        "HasHandler": OP_NOOP,
        "Raise": OP_NOOP,
        "PopHandler": OP_NOOP,
    },
    # Capsule execution / inter-card module switch (0x02C0-0x02C4)
    "Capsule": {
        "Call": OP_NOOP,
        "Schedule": OP_NOOP,
        "Jump": OP_NOOP,
        "LoadModule": OP_NOOP,
        "RunModule": OP_NOOP,
    },
    # PicoForge host hooks (GAP 4)
    "Base64": {
        "Encode": OP_NOOP,
        "Decode": OP_NOOP,
        "UrlEncode": OP_NOOP,
        "UrlDecode": OP_NOOP,
    },
    "Encoding": {
        "AsciiEncode": OP_NOOP, "AsciiDecode": OP_NOOP,
        "Utf8Encode": OP_NOOP, "Utf8Decode": OP_NOOP,
        "Utf16LEEncode": OP_NOOP, "Utf16LEDecode": OP_NOOP,
        "Utf16BEEncode": OP_NOOP, "Utf16BEDecode": OP_NOOP,
        "Utf7Encode": OP_NOOP, "Utf7Decode": OP_NOOP,
        "HexEncode": OP_NOOP, "HexDecode": OP_NOOP,
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
        "DiffDays":       OP_NOOP,
        "Year":           OP_NOOP,
        "Month":          OP_NOOP,
        "Day":            OP_NOOP,
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
    "Log": {
        "Write":   OP_NOOP,
        "Count":   OP_NOOP,
        "Level":   OP_NOOP,
        "Message": OP_NOOP,
        "Clear":   OP_NOOP,
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
        "Request":          OP_NOOP,
        "RespStatus":       OP_NOOP,
        "RespHeaders":      OP_NOOP,
        "RespBody":         OP_NOOP,
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
    # Map: first-class host-managed dictionary (int handle). Keys: int / string
    # (byte span) / hash (FNV-1a of a span). Values: int / string (v1). Nullable;
    # enumeration is insertion order. See docs/MAP.md.
    "Map": {
        "New":          OP_NOOP,
        "Use":          OP_NOOP,
        "Free":         OP_NOOP,
        "Clear":        OP_NOOP,
        "Count":        OP_NOOP,
        "Hash":         OP_NOOP,
        "PutII":        OP_NOOP,
        "GetII":        OP_NOOP,
        "HasI":         OP_NOOP,
        "DelI":         OP_NOOP,
        "PutIS":        OP_NOOP,
        "GetIS":        OP_NOOP,
        "PutNullI":     OP_NOOP,
        "IsNullI":      OP_NOOP,
        "PutSI":        OP_NOOP,
        "GetSI":        OP_NOOP,
        "HasS":         OP_NOOP,
        "DelS":         OP_NOOP,
        "PutSS":        OP_NOOP,
        "GetSS":        OP_NOOP,
        "PutNullS":     OP_NOOP,
        "IsNullS":      OP_NOOP,
        "KeyAt":        OP_NOOP,
        "KeySpanAt":    OP_NOOP,
        "ValAt":        OP_NOOP,
        "ValSpanAt":    OP_NOOP,
        "ValIsSpan":    OP_NOOP,
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
    # Forge read-only data binding (host-registered, RBAC-projected). Lookup a
    # record then read its fields; lets validation/hooks "load related" data.
    ("Data", "Lookup"):         0x0300,   # rs1=entity span rs2=id span -> rd=handle (0=none)
    ("Data", "FieldNum"):       0x0301,   # rs1=handle rs2=field span   -> rd=int
    ("Data", "FieldStr"):       0x0302,   # rs1=handle rs2=field span   -> rd=span
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
    ("Search", "Configure"):    0x01DB,
    ("Search", "Compatible"):   0x01DC,
    ("Search", "Rebuild"):      0x01DD,
    ("Search", "SetFacet"):     0x01DE,
    ("Search", "SetNumber"):    0x01DF,
    ("Search", "ClearFields"):  0x0200,
    ("Search", "Facets"):       0x0201,
    ("Search", "FacetValue"):   0x0202,
    ("Search", "FacetCount"):   0x0203,
    ("Search", "Range"):        0x0204,
    ("Search", "Save"):         0x0205,
    ("Search", "Load"):         0x0206,
    ("Search", "JournalUpsert"):0x0207,
    ("Search", "JournalDelete"):0x0208,
    ("Search", "JournalFacet"): 0x0209,
    ("Search", "JournalNumber"):0x020A,
    ("Search", "JournalReplay"):0x020B,
    # Tensor/matrix primitives for deterministic inference kernels.
    ("Tensor", "HasAccel"):     0x01EB,   # rs1=name-span                  rd=1 if host advertises accel
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
    ("BitLinear", "HasFormat"): 0x01F4,   # rs1=format id/name             rd=1 if supported
    ("BitLinear", "SetShape"):  0x01F0,   # rs1=rows rs2=cols              rd=ok
    ("BitLinear", "MatVecTernary"): 0x01F1,# rs1=packed weights rs2=i8 vec rd=span<int32_be>
    ("BitLinear", "MatVecBitmap"): 0x01F2, # rs1=zero/minus bitmaps rs2=i8  rd=span<int32_be>
    ("BitLinear", "MatVecBase3"): 0x01F3,  # rs1=base3 rows rs2=i8 vec      rd=span<int32_be>
    # Tokenizer / model / KV / sampling primitive surface for PicoScript AI harnesses.
    ("Tokenizer", "SetVocab"):   0x0210,
    ("Tokenizer", "EncodeBytes"): 0x0211,
    ("Tokenizer", "EncodeTrie"): 0x0212,
    ("Tokenizer", "DecodeBytes"): 0x0213,
    ("Tokenizer", "DecodeTrie"): 0x0214,
    ("Tokenizer", "Count"):      0x0215,
    ("Tokenizer", "Token"):      0x0216,
    ("Model", "SetConfig"):      0x0220,
    ("Model", "GetConfig"):      0x0221,
    ("Model", "TensorView"):     0x0222,
    ("Model", "TensorOffset"):   0x0223,
    ("Model", "TensorRows"):     0x0224,
    ("Model", "TensorCols"):     0x0225,
    ("Model", "TensorFormat"):   0x0226,
    ("Model", "ReadTensor"):     0x0227,
    ("Model", "ReadTensorRow"):  0x0270,
    ("Model", "SetBlock"):       0x0271,  # rs1=rowStart rs2=rowCount       rd=ok
    ("Model", "ReadTensorBlock"): 0x0272, # rs1=tensor rs2=(start<<16)|cnt rd=span rows
    ("Model", "MatVecI8Block"):  0x0273,  # rs1=tensor rs2=i8 vec           rd=span<int32_be>
    ("BitLinear", "MatVecTernaryBlock"): 0x0274, # rs1=tensor rs2=i8 vec    rd=span<int32_be>
    ("BitLinear", "MatVecBitmapBlock"):  0x0275, # rs1=tensor rs2=i8 vec    rd=span<int32_be>
    ("BitLinear", "MatVecBase3Block"):   0x0276, # rs1=tensor rs2=i8 vec    rd=span<int32_be>
    ("Quant", "AbsMax"):         0x0228,
    ("Quant", "QuantI8"):        0x0229,
    ("Quant", "DequantI8"):      0x022A,
    ("Quant", "ApplyScale"):     0x022B,
    ("Quant", "GroupScale"):     0x022C,
    ("Kv", "SetShape"):          0x0230,
    ("Kv", "WriteK"):            0x0231,
    ("Kv", "WriteV"):            0x0232,
    ("Kv", "ReadK"):             0x0233,
    ("Kv", "ReadV"):             0x0234,
    ("Kv", "Len"):               0x0235,
    ("Kv", "Clear"):             0x0236,
    ("Kv", "SetHead"):           0x0237,
    ("Kv", "WriteKH"):           0x0238,
    ("Kv", "WriteVH"):           0x0239,
    ("Kv", "ReadKH"):            0x023A,
    ("Kv", "ReadVH"):            0x023B,
    # Map: first-class dictionary primitive (0x0320-0x033A). Host-managed handle.
    # ABI note: the host-call convention is 2 inputs (rs1,rs2) + 1 result (rd), so
    # Map uses an ACTIVE-HANDLE model: New()/Use(h) select the active map and every
    # other op acts on it (keeping all ops <=2 args => no compiler changes, all
    # dialects lower Map.X(...) through the generic host-call path). Keys int/string/
    # hash; values int/string (v1). Nullable; insertion-order enumeration. See docs/MAP.md.
    ("Map", "New"):              0x0320,   # New() -> handle   (also sets active)
    ("Map", "Free"):             0x0321,   # Free(h)
    ("Map", "Clear"):            0x0322,   # Clear()           (active map)
    ("Map", "Count"):            0x0323,   # Count() -> n
    ("Map", "Hash"):             0x0324,   # Hash(span) -> int (FNV-1a 32-bit)
    ("Map", "PutII"):            0x0325,   # PutII(k, v)
    ("Map", "GetII"):            0x0326,   # GetII(k) -> v (0 if absent)
    ("Map", "HasI"):             0x0327,   # HasI(k) -> 0|1
    ("Map", "DelI"):             0x0328,   # DelI(k)
    ("Map", "PutIS"):            0x0329,   # PutIS(k, vSpan)
    ("Map", "GetIS"):            0x032A,   # GetIS(k) -> span
    ("Map", "PutNullI"):         0x032B,   # PutNullI(k)
    ("Map", "IsNullI"):          0x032C,   # IsNullI(k) -> 0|1
    ("Map", "PutSI"):            0x032D,   # PutSI(kSpan, v)
    ("Map", "GetSI"):            0x032E,   # GetSI(kSpan) -> v
    ("Map", "HasS"):             0x032F,   # HasS(kSpan) -> 0|1
    ("Map", "DelS"):             0x0330,   # DelS(kSpan)
    ("Map", "PutSS"):            0x0331,   # PutSS(kSpan, vSpan)
    ("Map", "GetSS"):            0x0332,   # GetSS(kSpan) -> span
    ("Map", "PutNullS"):         0x0333,   # PutNullS(kSpan)
    ("Map", "IsNullS"):          0x0334,   # IsNullS(kSpan) -> 0|1
    ("Map", "KeyAt"):            0x0335,   # KeyAt(i) -> int key
    ("Map", "KeySpanAt"):        0x0336,   # KeySpanAt(i) -> key span
    ("Map", "ValAt"):            0x0337,   # ValAt(i) -> int value
    ("Map", "ValSpanAt"):        0x0338,   # ValSpanAt(i) -> value span
    ("Map", "ValIsSpan"):        0x0339,   # ValIsSpan(i) -> 0|1
    ("Map", "Use"):              0x033A,   # Use(h)  (select active map)
    # Parsing: string/bytes -> structured Map (docs/MAP.md). Complements the Json.*
    # writer + Utf8Reader scanner with high-level deserialization.
    ("Json", "Parse"):           0x0340,   # Parse(jsonSpan) -> mapHandle (flat object)
    ("Binary", "ParseCard"):     0x0341,   # ParseCard(psc1Span) -> mapHandle
    ("Binary", "SerializeCard"): 0x0342,   # SerializeCard() -> span (active map -> PSC1)
    # BSO1 (BareMetal.Binary): schema-driven, little-endian, HMAC-SHA256 signed.
    # Schema is an ordered Map (field name -> wireType code; +256 = nullable). 64-bit
    # and float values are stored as their raw LE bytes (string) in the Map.
    ("Binary", "ParseEntity"):     0x0343, # ParseEntity(blobSpan, schemaMap) -> mapHandle
    ("Binary", "SerializeEntity"): 0x0344, # SerializeEntity(dataMap, schemaMap) -> blobSpan
    ("Binary", "SetKey"):          0x0345, # SetKey(keySpan)  (HMAC-SHA256 signing key; empty = unsigned)
    ("Binary", "Verify"):          0x0346, # Verify(blobSpan) -> 0|1  (HMAC check with the set key)
    ("Sampling", "ArgMax"):      0x0240,
    ("Sampling", "TopK"):        0x0241,
    ("Sampling", "Temperature"): 0x0242,
    ("Sampling", "ArgMaxRows"):  0x0243,
    ("Attention", "SetShape"):   0x0250,
    ("Attention", "Scores"):     0x0251,
    ("Attention", "Mix"):        0x0252,
    ("Attention", "Attend"):     0x0253,
    # Thread hints (0x70)
    ("Thread", "YieldCounted"): 0x70,
    # Io / output (0x71-0x72)
    ("Io", "Write"):            0x71,
    ("Io", "WriteByte"):        0x72,
    # Template engine (0x7A-0x7B): AOT-compiled-at-save, holes rendered at run
    ("Template", "Compile"):    0x7A,
    ("Template", "Render"):     0x7B,
    # TextRender (0x0260-0x0268): streaming HTML/template helpers on Utf8Writer.
    ("TextRender", "Raw"):      0x0260,   # rs1=writer rs2=span          append unescaped
    ("TextRender", "Text"):     0x0261,   # rs1=writer rs2=span          HTML-escape text
    ("TextRender", "Open"):     0x0262,   # rs1=writer rs2=tag           <tag
    ("TextRender", "Attr"):     0x0263,   # rs1=writer rs2=name=value    attr escaped
    ("TextRender", "OpenEnd"):  0x0264,   # rs1=writer                   >
    ("TextRender", "Close"):    0x0265,   # rs1=writer rs2=tag           </tag>
    ("TextRender", "Empty"):    0x0266,   # rs1=writer                   />
    ("TextRender", "Hole"):     0x0267,   # rs1=model key=value rs2=key   escaped value
    ("TextRender", "Br"):       0x0268,   # rs1=writer                   <br/>
    # Process lifecycle (0x0280-0x0287): OS-worker process table.
    ("Process", "Self"):         0x0280,   # rd=pid of this process
    ("Process", "Parent"):       0x0281,   # rd=pid of parent process
    ("Process", "Spawn"):        0x0282,   # rs1=pack rs2=entry           rd=pid
    ("Process", "Exit"):         0x0283,   # rs1=exit code                (terminates current)
    ("Process", "Kill"):         0x0284,   # rs1=pid                      rd=ok
    ("Process", "Status"):       0x0285,   # rs1=pid                      rd=0 running/1 exited/2 faulted
    ("Process", "Wait"):         0x0286,   # rs1=pid                      rd=exit code
    ("Process", "Args"):         0x0287,   # rd=span (launch arguments)
    # Env key-value (0x0288-0x028B): process environment variables.
    ("Env", "Get"):              0x0288,   # rs1=key-span                 rd=value-span (0=not found)
    ("Env", "Set"):              0x0289,   # rs1=key-span rs2=value-span  rd=ok
    ("Env", "Count"):            0x028A,   # rd=number of env vars
    ("Env", "Key"):              0x028B,   # rs1=index                    rd=key-span
    # Timer (0x0290-0x0293): one-shot and repeating timers.
    ("Timer", "After"):          0x0290,   # rs1=ms                       rd=handle
    ("Timer", "Every"):          0x0291,   # rs1=ms                       rd=handle
    ("Timer", "Cancel"):         0x0292,   # rs1=handle                   rd=ok
    ("Timer", "Elapsed"):        0x0293,   # rd=simulated elapsed ms
    # Scheduler (0x0294): deterministic time advancement (test helper).
    ("Scheduler", "Tick"):       0x0294,   # rs1=ms delta                 rd=number of timers fired
    # Principal (0x02A0-0x02A2): identity/role queries.
    ("Principal", "Current"):    0x02A0,   # rd=span (principal name)
    ("Principal", "HasRole"):    0x02A1,   # rs1=role-span                rd=0/1
    ("Principal", "Claims"):     0x02A2,   # rd=span (key=value pairs)
    # Capability (0x02A3-0x02A5): dynamic cap query/request/drop.
    ("Capability", "Has"):       0x02A3,   # rs1=cap-id                   rd=0/1
    ("Capability", "Request"):   0x02A4,   # rs1=cap-id                   rd=ok/0
    ("Capability", "Drop"):      0x02A5,   # rs1=cap-id                   rd=ok
    # Sandbox (0x02A6): revoke a capability for the current process.
    ("Sandbox", "Deny"):         0x02A6,   # rs1=cap-id                   rd=ok
    # Error handling (0x02B0-0x02B7): global error handler + fault inspection.
    ("Error", "SetHandler"):     0x02B0,   # rs1=handler PC (label addr)  rd=ok; PUSHES onto a
                                            # handler stack so nested try/except is correct
    ("Error", "HasHandler"):     0x02B1,   # rd=1 if the handler stack is non-empty
    ("Error", "Code"):           0x02B2,   # rd=last fault code (0=none)
    ("Error", "Detail"):         0x02B3,   # rd=last fault detail value
    ("Error", "Resume"):         0x02B4,   # rd=ok (clear fault and continue at fault PC+1)
    ("Error", "Clear"):          0x02B5,   # rd=ok (clear fault code without resuming)
    ("Error", "Raise"):          0x02B6,   # rs1=code; if the handler stack is non-empty, jumps
                                            # to its top entry (Code() reads back rs1) like a
                                            # caught fault, rd=1; if empty, propagates as a real
                                            # uncaught PicoFault (crashes / an outer handler sees it)
    ("Error", "PopHandler"):     0x02B7,   # rd=1 if something was popped, 0 if already empty;
                                            # pairs with SetHandler to restore the enclosing
                                            # try's handler once this try's body/except/finally
                                            # is done running
    # Capsule execution (0x02C0-0x02C4): inter-card module switching.
    ("Capsule", "Call"):         0x02C0,   # rs1=pack rs2=card            rd=result
    ("Capsule", "Schedule"):     0x02C1,   # rs1=pack rs2=card            rd=ok (bind to event)
    ("Capsule", "Jump"):         0x02C2,   # rs1=pack rs2=card            (transfer execution)
    ("Capsule", "LoadModule"):   0x02C3,   # rs1=pack rs2=card            rd=moduleHandle
    ("Capsule", "RunModule"):    0x02C4,   # rs1=handle                   rd=result
    # Base64 encode/decode (0x02D0-0x02D3): pure/deterministic string transform.
    ("Base64", "Encode"):        0x02D0,   # rs1=span                     rd=base64 span
    ("Base64", "Decode"):        0x02D1,   # rs1=base64 span              rd=decoded span
    ("Base64", "UrlDecode"):     0x02D2,   # rs1=base64url span           rd=decoded span
    ("Base64", "UrlEncode"):     0x02D3,   # rs1=span                     rd=base64url span
    # Encoding transforms (0x0310-0x031B). Decoders normalize to UTF-8 spans.
    ("Encoding", "AsciiEncode"): 0x0310,
    ("Encoding", "AsciiDecode"): 0x0311,
    ("Encoding", "Utf8Encode"):  0x0312,
    ("Encoding", "Utf8Decode"):  0x0313,
    ("Encoding", "Utf16LEEncode"): 0x0314,
    ("Encoding", "Utf16LEDecode"): 0x0315,
    ("Encoding", "Utf16BEEncode"): 0x0316,
    ("Encoding", "Utf16BEDecode"): 0x0317,
    ("Encoding", "Utf7Encode"): 0x0318,
    ("Encoding", "Utf7Decode"): 0x0319,
    ("Encoding", "HexEncode"):  0x031A,
    ("Encoding", "HexDecode"):  0x031B,
    # DateTime extended (0xBB-0xBE): pure given input.
    ("DateTime", "DiffDays"):    0xBB,     # rs1=millis_a rs2=millis_b    rd=days
    ("DateTime", "Year"):        0xBC,     # rs1=millis                   rd=year
    ("DateTime", "Month"):       0xBD,     # rs1=millis                   rd=month (1-12)
    ("DateTime", "Day"):         0xBE,     # rs1=millis                   rd=day (1-31)
    # Req path parameter extraction (0x01B6-0x01B7)
    ("Req", "Param"):            0x01B6,   # rs1=index                    rd=span (path segment)
    ("Req", "ParamCount"):       0x01B7,   #                              rd=segment count
    # Net socket I/O (0x02E0-0x02E6): raw socket primitives for native server.
    ("Net", "Listen"):           0x02E0,   # rs1=port                     rd=server_fd
    ("Net", "Accept"):           0x02E1,   # rs1=server_fd                rd=conn_fd
    ("Net", "Read"):             0x02E2,   # rs1=conn_fd rs2=max_bytes    rd=span
    ("Net", "Write"):            0x02E3,   # rs1=conn_fd rs2=span         rd=bytes_written
    ("Net", "Shutdown"):         0x02E4,   # rs1=conn_fd                  rd=ok
    ("Net", "PoolSize"):         0x02E5,   # rs1=n                        rd=ok
    ("Net", "Register"):         0x02E6,   # rs1=event_id                 rd=ok (bind ON handler)
    # Log.* (0x02F0-0x02F4): deterministic, script-visible tracing/audit log.
    # See docs/LOGGING.md. Append-only {level, message span} table keyed by a
    # monotonic sequence id -- no wall-clock timestamp (host-injected/
    # non-deterministic by this VM's convention; order IS the timeline).
    ("Log", "Write"):            0x02F0,   # rs1=level rs2=message span   rd=logId (sequence, 1-based)
    ("Log", "Count"):            0x02F1,   #                              rd=entry count
    ("Log", "Level"):            0x02F2,   # rs1=logId                    rd=level (0 if unknown id)
    ("Log", "Message"):          0x02F3,   # rs1=logId                    rd=message span (0 if unknown id)
    ("Log", "Clear"):            0x02F4,   #                              rd=1 (discards all entries)
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
    ("String", "Eq"):           0x8D,
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
    # Http transport (0x0138-0x013B): request + response accessors. Request/response
    # headers are Map handles (Map<string,string>).
    ("Http", "Request"):        0x0138,   # Request(method, urlSpan, reqHeadersMap, bodySpan) -> respHandle
    ("Http", "RespStatus"):     0x0139,   # RespStatus(resp) -> int
    ("Http", "RespHeaders"):    0x013A,   # RespHeaders(resp) -> mapHandle (enumerable)
    ("Http", "RespBody"):       0x013B,   # RespBody(resp, outDesc) -> span
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

# Named constants for readable request/response code in all frontends.
# Keys are canonical uppercase spellings; lookup is case-insensitive.
HTTP_NAMED_CONSTANTS = {
    # HTTP methods (Req.Method)
    "HTTP_METHOD_GET": 1,
    "HTTP_METHOD_POST": 2,
    "HTTP_METHOD_PUT": 3,
    "HTTP_METHOD_DELETE": 4,
    "HTTP_METHOD_HEAD": 5,
    "HTTP_METHOD_PATCH": 6,
    "HTTP_METHOD_OPTIONS": 7,
    "HTTP_METHOD_CONNECT": 8,
    "HTTP_METHOD_TRACE": 9,
    # Short aliases
    "METHOD_GET": 1,
    "METHOD_POST": 2,
    "METHOD_PUT": 3,
    "METHOD_DELETE": 4,
    "METHOD_HEAD": 5,
    "METHOD_PATCH": 6,
    "METHOD_OPTIONS": 7,
    "METHOD_CONNECT": 8,
    "METHOD_TRACE": 9,
    # Enum-style aliases (C frontends can use HttpMethod.POST style)
    "HTTPMETHOD.GET": 1,
    "HTTPMETHOD.POST": 2,
    "HTTPMETHOD.PUT": 3,
    "HTTPMETHOD.DELETE": 4,
    "HTTPMETHOD.HEAD": 5,
    "HTTPMETHOD.PATCH": 6,
    "HTTPMETHOD.OPTIONS": 7,
    "HTTPMETHOD.CONNECT": 8,
    "HTTPMETHOD.TRACE": 9,
    # HTTP statuses (Resp.Status)
    "HTTP_STATUS_OK": 200,
    "HTTP_STATUS_CREATED": 201,
    "HTTP_STATUS_ACCEPTED": 202,
    "HTTP_STATUS_NO_CONTENT": 204,
    "HTTP_STATUS_BAD_REQUEST": 400,
    "HTTP_STATUS_UNAUTHORIZED": 401,
    "HTTP_STATUS_FORBIDDEN": 403,
    "HTTP_STATUS_NOT_FOUND": 404,
    "HTTP_STATUS_CONFLICT": 409,
    "HTTP_STATUS_UNPROCESSABLE_ENTITY": 422,
    "HTTP_STATUS_TOO_MANY_REQUESTS": 429,
    "HTTP_STATUS_INTERNAL_SERVER_ERROR": 500,
    "HTTP_STATUS_NOT_IMPLEMENTED": 501,
    "HTTP_STATUS_BAD_GATEWAY": 502,
    "HTTP_STATUS_SERVICE_UNAVAILABLE": 503,
    # Short aliases
    "STATUS_OK": 200,
    "STATUS_CREATED": 201,
    "STATUS_ACCEPTED": 202,
    "STATUS_NO_CONTENT": 204,
    "STATUS_BAD_REQUEST": 400,
    "STATUS_UNAUTHORIZED": 401,
    "STATUS_FORBIDDEN": 403,
    "STATUS_NOT_FOUND": 404,
    "STATUS_CONFLICT": 409,
    "STATUS_UNPROCESSABLE_ENTITY": 422,
    "STATUS_TOO_MANY_REQUESTS": 429,
    "STATUS_INTERNAL_SERVER_ERROR": 500,
    "STATUS_NOT_IMPLEMENTED": 501,
    "STATUS_BAD_GATEWAY": 502,
    "STATUS_SERVICE_UNAVAILABLE": 503,
    # Enum-style aliases (C frontends can use HttpStatus.OK style)
    "HTTPSTATUS.OK": 200,
    "HTTPSTATUS.CREATED": 201,
    "HTTPSTATUS.ACCEPTED": 202,
    "HTTPSTATUS.NO_CONTENT": 204,
    "HTTPSTATUS.BAD_REQUEST": 400,
    "HTTPSTATUS.UNAUTHORIZED": 401,
    "HTTPSTATUS.FORBIDDEN": 403,
    "HTTPSTATUS.NOT_FOUND": 404,
    "HTTPSTATUS.CONFLICT": 409,
    "HTTPSTATUS.UNPROCESSABLE_ENTITY": 422,
    "HTTPSTATUS.TOO_MANY_REQUESTS": 429,
    "HTTPSTATUS.INTERNAL_SERVER_ERROR": 500,
    "HTTPSTATUS.NOT_IMPLEMENTED": 501,
    "HTTPSTATUS.BAD_GATEWAY": 502,
    "HTTPSTATUS.SERVICE_UNAVAILABLE": 503,
}


SYSTEM_NAMED_CONSTANTS = {
    # Day-of-week (ISO-style, Monday=1..Sunday=7)
    "DAY_MONDAY": 1,
    "DAY_TUESDAY": 2,
    "DAY_WEDNESDAY": 3,
    "DAY_THURSDAY": 4,
    "DAY_FRIDAY": 5,
    "DAY_SATURDAY": 6,
    "DAY_SUNDAY": 7,
    "DAY.MONDAY": 1,
    "DAY.TUESDAY": 2,
    "DAY.WEDNESDAY": 3,
    "DAY.THURSDAY": 4,
    "DAY.FRIDAY": 5,
    "DAY.SATURDAY": 6,
    "DAY.SUNDAY": 7,
    # Months
    "MONTH_JANUARY": 1,
    "MONTH_FEBRUARY": 2,
    "MONTH_MARCH": 3,
    "MONTH_APRIL": 4,
    "MONTH_MAY": 5,
    "MONTH_JUNE": 6,
    "MONTH_JULY": 7,
    "MONTH_AUGUST": 8,
    "MONTH_SEPTEMBER": 9,
    "MONTH_OCTOBER": 10,
    "MONTH_NOVEMBER": 11,
    "MONTH_DECEMBER": 12,
    "MONTH.JANUARY": 1,
    "MONTH.FEBRUARY": 2,
    "MONTH.MARCH": 3,
    "MONTH.APRIL": 4,
    "MONTH.MAY": 5,
    "MONTH.JUNE": 6,
    "MONTH.JULY": 7,
    "MONTH.AUGUST": 8,
    "MONTH.SEPTEMBER": 9,
    "MONTH.OCTOBER": 10,
    "MONTH.NOVEMBER": 11,
    "MONTH.DECEMBER": 12,
    # 32-bit integer sizing constants
    "BITS_PER_BYTE": 8,
    "UINT8_MAX": 255,
    "UINT16_MAX": 65535,
    "UINT24_MAX": 16777215,
    "UINT32_MAX": 4294967295,
    "INT8_MIN": -128,
    "INT8_MAX": 127,
    "INT16_MIN": -32768,
    "INT16_MAX": 32767,
    "INT24_MIN": -8388608,
    "INT24_MAX": 8388607,
    "INT32_MIN": -2147483648,
    "INT32_MAX": 2147483647,
    "MASK8": 0xFF,
    "MASK16": 0xFFFF,
    "MASK24": 0xFFFFFF,
    "MASK32": 0xFFFFFFFF,
    "SIGN8": 0x80,
    "SIGN16": 0x8000,
    "SIGN24": 0x800000,
    "SIGN32": 0x80000000,
    # Time zones (stable enum IDs; host maps these to tzdb rules)
    "TZ_UTC": 0,
    "TZ_EUROPE_LONDON": 1,
    "TZ_EUROPE_PARIS": 2,
    "TZ_AMERICA_NEW_YORK": 3,
    "TZ_AMERICA_CHICAGO": 4,
    "TZ_AMERICA_DENVER": 5,
    "TZ_AMERICA_LOS_ANGELES": 6,
    "TZ_ASIA_TOKYO": 7,
    "TZ_ASIA_SINGAPORE": 8,
    "TZ_ASIA_HONG_KONG": 9,
    "TZ_AUSTRALIA_SYDNEY": 10,
    "TZ_ASIA_DUBAI": 11,
    "TZ.UTC": 0,
    "TZ.EUROPE_LONDON": 1,
    "TZ.EUROPE_PARIS": 2,
    "TZ.AMERICA_NEW_YORK": 3,
    "TZ.AMERICA_CHICAGO": 4,
    "TZ.AMERICA_DENVER": 5,
    "TZ.AMERICA_LOS_ANGELES": 6,
    "TZ.ASIA_TOKYO": 7,
    "TZ.ASIA_SINGAPORE": 8,
    "TZ.ASIA_HONG_KONG": 9,
    "TZ.AUSTRALIA_SYDNEY": 10,
    "TZ.ASIA_DUBAI": 11,
    "TIMEZONE.UTC": 0,
    "TIMEZONE.EUROPE_LONDON": 1,
    "TIMEZONE.EUROPE_PARIS": 2,
    "TIMEZONE.AMERICA_NEW_YORK": 3,
    "TIMEZONE.AMERICA_CHICAGO": 4,
    "TIMEZONE.AMERICA_DENVER": 5,
    "TIMEZONE.AMERICA_LOS_ANGELES": 6,
    "TIMEZONE.ASIA_TOKYO": 7,
    "TIMEZONE.ASIA_SINGAPORE": 8,
    "TIMEZONE.ASIA_HONG_KONG": 9,
    "TIMEZONE.AUSTRALIA_SYDNEY": 10,
    "TIMEZONE.ASIA_DUBAI": 11,
    # DST status
    "DST_NONE": 0,
    "DST_OBSERVED": 1,
    "DST_ACTIVE": 2,
    "DST.NONE": 0,
    "DST.OBSERVED": 1,
    "DST.ACTIVE": 2,
    # Currencies (ISO-4217 numeric)
    "CURRENCY_USD": 840,
    "CURRENCY_EUR": 978,
    "CURRENCY_GBP": 826,
    "CURRENCY_JPY": 392,
    "CURRENCY_CNY": 156,
    "CURRENCY_AUD": 36,
    "CURRENCY_CAD": 124,
    "CURRENCY_CHF": 756,
    "CURRENCY_SEK": 752,
    "CURRENCY_NOK": 578,
    "CURRENCY_NZD": 554,
    "CURRENCY_INR": 356,
    "CURRENCY_SGD": 702,
    "CURRENCY_HKD": 344,
    "CURRENCY_AED": 784,
    "CURRENCY_BRL": 986,
    "CURRENCY_ZAR": 710,
    "CURRENCY_KRW": 410,
    "CURRENCY_MXN": 484,
    "CURRENCY.USD": 840,
    "CURRENCY.EUR": 978,
    "CURRENCY.GBP": 826,
    "CURRENCY.JPY": 392,
    "CURRENCY.CNY": 156,
    "CURRENCY.AUD": 36,
    "CURRENCY.CAD": 124,
    "CURRENCY.CHF": 756,
    "CURRENCY.SEK": 752,
    "CURRENCY.NOK": 578,
    "CURRENCY.NZD": 554,
    "CURRENCY.INR": 356,
    "CURRENCY.SGD": 702,
    "CURRENCY.HKD": 344,
    "CURRENCY.AED": 784,
    "CURRENCY.BRL": 986,
    "CURRENCY.ZAR": 710,
    "CURRENCY.KRW": 410,
    "CURRENCY.MXN": 484,
    # Currency minor units (decimal places)
    "CURRENCY_MINOR_USD": 2,
    "CURRENCY_MINOR_EUR": 2,
    "CURRENCY_MINOR_GBP": 2,
    "CURRENCY_MINOR_JPY": 0,
    "CURRENCY_MINOR_CNY": 2,
    "CURRENCY_MINOR_AUD": 2,
    "CURRENCY_MINOR_CAD": 2,
    "CURRENCY_MINOR_CHF": 2,
    "CURRENCY_MINOR_SEK": 2,
    "CURRENCY_MINOR_NOK": 2,
    "CURRENCY_MINOR_NZD": 2,
    "CURRENCY_MINOR_INR": 2,
    "CURRENCY_MINOR_SGD": 2,
    "CURRENCY_MINOR_HKD": 2,
    "CURRENCY_MINOR_AED": 2,
    "CURRENCY_MINOR_BRL": 2,
    "CURRENCY_MINOR_ZAR": 2,
    "CURRENCY_MINOR_KRW": 0,
    "CURRENCY_MINOR_MXN": 2,
    "CURRENCYMINOR.USD": 2,
    "CURRENCYMINOR.EUR": 2,
    "CURRENCYMINOR.GBP": 2,
    "CURRENCYMINOR.JPY": 0,
    "CURRENCYMINOR.CNY": 2,
    "CURRENCYMINOR.AUD": 2,
    "CURRENCYMINOR.CAD": 2,
    "CURRENCYMINOR.CHF": 2,
    "CURRENCYMINOR.SEK": 2,
    "CURRENCYMINOR.NOK": 2,
    "CURRENCYMINOR.NZD": 2,
    "CURRENCYMINOR.INR": 2,
    "CURRENCYMINOR.SGD": 2,
    "CURRENCYMINOR.HKD": 2,
    "CURRENCYMINOR.AED": 2,
    "CURRENCYMINOR.BRL": 2,
    "CURRENCYMINOR.ZAR": 2,
    "CURRENCYMINOR.KRW": 0,
    "CURRENCYMINOR.MXN": 2,
    # Countries (ISO-3166-1 numeric)
    "COUNTRY_US": 840,
    "COUNTRY_GB": 826,
    "COUNTRY_FR": 250,
    "COUNTRY_DE": 276,
    "COUNTRY_ES": 724,
    "COUNTRY_IT": 380,
    "COUNTRY_NL": 528,
    "COUNTRY_SE": 752,
    "COUNTRY_NO": 578,
    "COUNTRY_DK": 208,
    "COUNTRY_FI": 246,
    "COUNTRY_CH": 756,
    "COUNTRY_IE": 372,
    "COUNTRY_PL": 616,
    "COUNTRY_PT": 620,
    "COUNTRY_AU": 36,
    "COUNTRY_NZ": 554,
    "COUNTRY_JP": 392,
    "COUNTRY_CN": 156,
    "COUNTRY_HK": 344,
    "COUNTRY_SG": 702,
    "COUNTRY_IN": 356,
    "COUNTRY_AE": 784,
    "COUNTRY_BR": 76,
    "COUNTRY_ZA": 710,
    "COUNTRY_KR": 410,
    "COUNTRY_MX": 484,
    "COUNTRY_CA": 124,
    "COUNTRY.US": 840,
    "COUNTRY.GB": 826,
    "COUNTRY.FR": 250,
    "COUNTRY.DE": 276,
    "COUNTRY.ES": 724,
    "COUNTRY.IT": 380,
    "COUNTRY.NL": 528,
    "COUNTRY.SE": 752,
    "COUNTRY.NO": 578,
    "COUNTRY.DK": 208,
    "COUNTRY.FI": 246,
    "COUNTRY.CH": 756,
    "COUNTRY.IE": 372,
    "COUNTRY.PL": 616,
    "COUNTRY.PT": 620,
    "COUNTRY.AU": 36,
    "COUNTRY.NZ": 554,
    "COUNTRY.JP": 392,
    "COUNTRY.CN": 156,
    "COUNTRY.HK": 344,
    "COUNTRY.SG": 702,
    "COUNTRY.IN": 356,
    "COUNTRY.AE": 784,
    "COUNTRY.BR": 76,
    "COUNTRY.ZA": 710,
    "COUNTRY.KR": 410,
    "COUNTRY.MX": 484,
    "COUNTRY.CA": 124,
    # Base units
    "UOM_METER": 1,
    "UOM_KILOGRAM": 2,
    "UOM_SECOND": 3,
    "UOM_AMPERE": 4,
    "UOM_KELVIN": 5,
    "UOM_MOLE": 6,
    "UOM_CANDELA": 7,
    "UOM_LITER": 100,
    "UOM_GRAM": 101,
    "UOM_CELSIUS": 102,
    "UOM.METER": 1,
    "UOM.KILOGRAM": 2,
    "UOM.SECOND": 3,
    "UOM.AMPERE": 4,
    "UOM.KELVIN": 5,
    "UOM.MOLE": 6,
    "UOM.CANDELA": 7,
    "UOM.LITER": 100,
    "UOM.GRAM": 101,
    "UOM.CELSIUS": 102,
    # Colours (24-bit RGB)
    "COLOR_BLACK": 0x000000,
    "COLOR_WHITE": 0xFFFFFF,
    "COLOR_RED": 0xFF0000,
    "COLOR_GREEN": 0x00FF00,
    "COLOR_BLUE": 0x0000FF,
    "COLOR_YELLOW": 0xFFFF00,
    "COLOR_CYAN": 0x00FFFF,
    "COLOR_MAGENTA": 0xFF00FF,
    "COLOR_ORANGE": 0xFFA500,
    "COLOR_GRAY": 0x808080,
    "COLOR_GREY": 0x808080,
    "COLOR.BLACK": 0x000000,
    "COLOR.WHITE": 0xFFFFFF,
    "COLOR.RED": 0xFF0000,
    "COLOR.GREEN": 0x00FF00,
    "COLOR.BLUE": 0x0000FF,
    "COLOR.YELLOW": 0xFFFF00,
    "COLOR.CYAN": 0x00FFFF,
    "COLOR.MAGENTA": 0xFF00FF,
    "COLOR.ORANGE": 0xFFA500,
    "COLOR.GRAY": 0x808080,
    "COLOR.GREY": 0x808080,
    # Conversion constants
    "MS_PER_SECOND": 1000,
    "SECONDS_PER_MINUTE": 60,
    "MINUTES_PER_HOUR": 60,
    "HOURS_PER_DAY": 24,
    "DAYS_PER_WEEK": 7,
    "BYTES_PER_KIB": 1024,
    "BYTES_PER_MIB": 1048576,
    "MM_PER_METER": 1000,
    "CM_PER_METER": 100,
    "GRAMS_PER_KILOGRAM": 1000,
    "PI_Q16": 205887,
    "RAD_PER_DEG_Q16": 1144,
    "DEG_PER_RAD_Q16": 3754936,
}

NAMED_CONSTANTS = {}
NAMED_CONSTANTS.update(HTTP_NAMED_CONSTANTS)
NAMED_CONSTANTS.update(SYSTEM_NAMED_CONSTANTS)

# Pretty-print metadata is localizable by locale key.
NAMED_CONSTANT_I18N = {
    "en": {
        # Day/month names
        "DAY_MONDAY": {"label": "Monday", "description": "ISO weekday index 1 (Monday)."},
        "DAY_TUESDAY": {"label": "Tuesday", "description": "ISO weekday index 2 (Tuesday)."},
        "DAY_WEDNESDAY": {"label": "Wednesday", "description": "ISO weekday index 3 (Wednesday)."},
        "DAY_THURSDAY": {"label": "Thursday", "description": "ISO weekday index 4 (Thursday)."},
        "DAY_FRIDAY": {"label": "Friday", "description": "ISO weekday index 5 (Friday)."},
        "DAY_SATURDAY": {"label": "Saturday", "description": "ISO weekday index 6 (Saturday)."},
        "DAY_SUNDAY": {"label": "Sunday", "description": "ISO weekday index 7 (Sunday)."},
        "MONTH_JANUARY": {"label": "January", "description": "Month index 1 (January)."},
        "MONTH_FEBRUARY": {"label": "February", "description": "Month index 2 (February)."},
        "MONTH_MARCH": {"label": "March", "description": "Month index 3 (March)."},
        "MONTH_APRIL": {"label": "April", "description": "Month index 4 (April)."},
        "MONTH_MAY": {"label": "May", "description": "Month index 5 (May)."},
        "MONTH_JUNE": {"label": "June", "description": "Month index 6 (June)."},
        "MONTH_JULY": {"label": "July", "description": "Month index 7 (July)."},
        "MONTH_AUGUST": {"label": "August", "description": "Month index 8 (August)."},
        "MONTH_SEPTEMBER": {"label": "September", "description": "Month index 9 (September)."},
        "MONTH_OCTOBER": {"label": "October", "description": "Month index 10 (October)."},
        "MONTH_NOVEMBER": {"label": "November", "description": "Month index 11 (November)."},
        "MONTH_DECEMBER": {"label": "December", "description": "Month index 12 (December)."},
        # Time zones / DST
        "TZ_UTC": {"label": "UTC", "description": "Coordinated Universal Time (no daylight saving transition)."},
        "TZ_EUROPE_LONDON": {"label": "Europe/London", "description": "IANA zone Europe/London (GMT/BST)."},
        "TZ_EUROPE_PARIS": {"label": "Europe/Paris", "description": "IANA zone Europe/Paris (CET/CEST)."},
        "TZ_AMERICA_NEW_YORK": {"label": "America/New_York", "description": "IANA zone America/New_York (EST/EDT)."},
        "TZ_AMERICA_CHICAGO": {"label": "America/Chicago", "description": "IANA zone America/Chicago (CST/CDT)."},
        "TZ_AMERICA_DENVER": {"label": "America/Denver", "description": "IANA zone America/Denver (MST/MDT)."},
        "TZ_AMERICA_LOS_ANGELES": {"label": "America/Los_Angeles", "description": "IANA zone America/Los_Angeles (PST/PDT)."},
        "TZ_ASIA_TOKYO": {"label": "Asia/Tokyo", "description": "IANA zone Asia/Tokyo (JST)."},
        "TZ_ASIA_SINGAPORE": {"label": "Asia/Singapore", "description": "IANA zone Asia/Singapore (SGT)."},
        "TZ_ASIA_HONG_KONG": {"label": "Asia/Hong_Kong", "description": "IANA zone Asia/Hong_Kong (HKT)."},
        "TZ_AUSTRALIA_SYDNEY": {"label": "Australia/Sydney", "description": "IANA zone Australia/Sydney (AEST/AEDT)."},
        "TZ_ASIA_DUBAI": {"label": "Asia/Dubai", "description": "IANA zone Asia/Dubai (GST)."},
        "DST_NONE": {"label": "No DST", "description": "Zone does not observe daylight saving time."},
        "DST_OBSERVED": {"label": "DST observed", "description": "Zone has daylight saving rules in its calendar."},
        "DST_ACTIVE": {"label": "DST active now", "description": "Current instant is inside the daylight saving period."},
        # Currencies (ISO-4217)
        "CURRENCY_USD": {"label": "US Dollar (USD)", "description": "ISO-4217 numeric code 840."},
        "CURRENCY_EUR": {"label": "Euro (EUR)", "description": "ISO-4217 numeric code 978."},
        "CURRENCY_GBP": {"label": "Pound Sterling (GBP)", "description": "ISO-4217 numeric code 826."},
        "CURRENCY_JPY": {"label": "Japanese Yen (JPY)", "description": "ISO-4217 numeric code 392."},
        "CURRENCY_CNY": {"label": "Chinese Yuan (CNY)", "description": "ISO-4217 numeric code 156."},
        "CURRENCY_AUD": {"label": "Australian Dollar (AUD)", "description": "ISO-4217 numeric code 36."},
        "CURRENCY_CAD": {"label": "Canadian Dollar (CAD)", "description": "ISO-4217 numeric code 124."},
        "CURRENCY_CHF": {"label": "Swiss Franc (CHF)", "description": "ISO-4217 numeric code 756."},
        "CURRENCY_SEK": {"label": "Swedish Krona (SEK)", "description": "ISO-4217 numeric code 752."},
        "CURRENCY_NOK": {"label": "Norwegian Krone (NOK)", "description": "ISO-4217 numeric code 578."},
        "CURRENCY_NZD": {"label": "New Zealand Dollar (NZD)", "description": "ISO-4217 numeric code 554."},
        "CURRENCY_INR": {"label": "Indian Rupee (INR)", "description": "ISO-4217 numeric code 356."},
        "CURRENCY_SGD": {"label": "Singapore Dollar (SGD)", "description": "ISO-4217 numeric code 702."},
        "CURRENCY_HKD": {"label": "Hong Kong Dollar (HKD)", "description": "ISO-4217 numeric code 344."},
        "CURRENCY_AED": {"label": "UAE Dirham (AED)", "description": "ISO-4217 numeric code 784."},
        "CURRENCY_BRL": {"label": "Brazilian Real (BRL)", "description": "ISO-4217 numeric code 986."},
        "CURRENCY_ZAR": {"label": "South African Rand (ZAR)", "description": "ISO-4217 numeric code 710."},
        "CURRENCY_KRW": {"label": "South Korean Won (KRW)", "description": "ISO-4217 numeric code 410."},
        "CURRENCY_MXN": {"label": "Mexican Peso (MXN)", "description": "ISO-4217 numeric code 484."},
        # Countries (ISO-3166-1)
        "COUNTRY_US": {"label": "United States", "description": "ISO-3166-1 numeric country code 840."},
        "COUNTRY_GB": {"label": "United Kingdom", "description": "ISO-3166-1 numeric country code 826."},
        "COUNTRY_FR": {"label": "France", "description": "ISO-3166-1 numeric country code 250."},
        "COUNTRY_DE": {"label": "Germany", "description": "ISO-3166-1 numeric country code 276."},
        "COUNTRY_ES": {"label": "Spain", "description": "ISO-3166-1 numeric country code 724."},
        "COUNTRY_IT": {"label": "Italy", "description": "ISO-3166-1 numeric country code 380."},
        "COUNTRY_NL": {"label": "Netherlands", "description": "ISO-3166-1 numeric country code 528."},
        "COUNTRY_SE": {"label": "Sweden", "description": "ISO-3166-1 numeric country code 752."},
        "COUNTRY_NO": {"label": "Norway", "description": "ISO-3166-1 numeric country code 578."},
        "COUNTRY_DK": {"label": "Denmark", "description": "ISO-3166-1 numeric country code 208."},
        "COUNTRY_FI": {"label": "Finland", "description": "ISO-3166-1 numeric country code 246."},
        "COUNTRY_CH": {"label": "Switzerland", "description": "ISO-3166-1 numeric country code 756."},
        "COUNTRY_IE": {"label": "Ireland", "description": "ISO-3166-1 numeric country code 372."},
        "COUNTRY_PL": {"label": "Poland", "description": "ISO-3166-1 numeric country code 616."},
        "COUNTRY_PT": {"label": "Portugal", "description": "ISO-3166-1 numeric country code 620."},
        "COUNTRY_AU": {"label": "Australia", "description": "ISO-3166-1 numeric country code 36."},
        "COUNTRY_NZ": {"label": "New Zealand", "description": "ISO-3166-1 numeric country code 554."},
        "COUNTRY_JP": {"label": "Japan", "description": "ISO-3166-1 numeric country code 392."},
        "COUNTRY_CN": {"label": "China", "description": "ISO-3166-1 numeric country code 156."},
        "COUNTRY_HK": {"label": "Hong Kong", "description": "ISO-3166-1 numeric country code 344."},
        "COUNTRY_SG": {"label": "Singapore", "description": "ISO-3166-1 numeric country code 702."},
        "COUNTRY_IN": {"label": "India", "description": "ISO-3166-1 numeric country code 356."},
        "COUNTRY_AE": {"label": "United Arab Emirates", "description": "ISO-3166-1 numeric country code 784."},
        "COUNTRY_BR": {"label": "Brazil", "description": "ISO-3166-1 numeric country code 76."},
        "COUNTRY_ZA": {"label": "South Africa", "description": "ISO-3166-1 numeric country code 710."},
        "COUNTRY_KR": {"label": "South Korea", "description": "ISO-3166-1 numeric country code 410."},
        "COUNTRY_MX": {"label": "Mexico", "description": "ISO-3166-1 numeric country code 484."},
        "COUNTRY_CA": {"label": "Canada", "description": "ISO-3166-1 numeric country code 124."},
        # Units / colours / conversion / integer sizing
        "UOM_METER": {"label": "meter (m)", "description": "SI base unit for length."},
        "UOM_KILOGRAM": {"label": "kilogram (kg)", "description": "SI base unit for mass."},
        "UOM_SECOND": {"label": "second (s)", "description": "SI base unit for time."},
        "UOM_AMPERE": {"label": "ampere (A)", "description": "SI base unit for electric current."},
        "UOM_KELVIN": {"label": "kelvin (K)", "description": "SI base unit for temperature."},
        "UOM_MOLE": {"label": "mole (mol)", "description": "SI base unit for amount of substance."},
        "UOM_CANDELA": {"label": "candela (cd)", "description": "SI base unit for luminous intensity."},
        "UOM_LITER": {"label": "liter (L)", "description": "Metric derived unit for volume."},
        "UOM_GRAM": {"label": "gram (g)", "description": "Metric unit for mass (1/1000 kilogram)."},
        "UOM_CELSIUS": {"label": "degree Celsius (°C)", "description": "Metric temperature scale in degrees Celsius."},
        "COLOR_BLACK": {"label": "Black", "description": "24-bit RGB colour 0x000000."},
        "COLOR_WHITE": {"label": "White", "description": "24-bit RGB colour 0xFFFFFF."},
        "COLOR_RED": {"label": "Red", "description": "24-bit RGB colour 0xFF0000."},
        "COLOR_GREEN": {"label": "Green", "description": "24-bit RGB colour 0x00FF00."},
        "COLOR_BLUE": {"label": "Blue", "description": "24-bit RGB colour 0x0000FF."},
        "COLOR_YELLOW": {"label": "Yellow", "description": "24-bit RGB colour 0xFFFF00."},
        "COLOR_CYAN": {"label": "Cyan", "description": "24-bit RGB colour 0x00FFFF."},
        "COLOR_MAGENTA": {"label": "Magenta", "description": "24-bit RGB colour 0xFF00FF."},
        "COLOR_ORANGE": {"label": "Orange", "description": "24-bit RGB colour 0xFFA500."},
        "COLOR_GRAY": {"label": "Gray", "description": "24-bit RGB colour 0x808080."},
        "BITS_PER_BYTE": {"label": "Bits per byte", "description": "Number of bits in one byte (8)."},
        "UINT8_MAX": {"label": "u8 max", "description": "Maximum unsigned 8-bit integer value."},
        "UINT16_MAX": {"label": "u16 max", "description": "Maximum unsigned 16-bit integer value."},
        "UINT24_MAX": {"label": "u24 max", "description": "Maximum unsigned 24-bit integer value."},
        "UINT32_MAX": {"label": "u32 max", "description": "Maximum unsigned 32-bit integer value."},
        "INT8_MIN": {"label": "i8 min", "description": "Minimum signed 8-bit integer value."},
        "INT8_MAX": {"label": "i8 max", "description": "Maximum signed 8-bit integer value."},
        "INT16_MIN": {"label": "i16 min", "description": "Minimum signed 16-bit integer value."},
        "INT16_MAX": {"label": "i16 max", "description": "Maximum signed 16-bit integer value."},
        "INT24_MIN": {"label": "i24 min", "description": "Minimum signed 24-bit integer value."},
        "INT24_MAX": {"label": "i24 max", "description": "Maximum signed 24-bit integer value."},
        "INT32_MIN": {"label": "i32 min", "description": "Minimum signed 32-bit integer value."},
        "INT32_MAX": {"label": "i32 max", "description": "Maximum signed 32-bit integer value."},
        "MASK8": {"label": "8-bit mask", "description": "Bit mask for the low 8 bits (0xFF)."},
        "MASK16": {"label": "16-bit mask", "description": "Bit mask for the low 16 bits (0xFFFF)."},
        "MASK24": {"label": "24-bit mask", "description": "Bit mask for the low 24 bits (0xFFFFFF)."},
        "MASK32": {"label": "32-bit mask", "description": "Bit mask for the low 32 bits (0xFFFFFFFF)."},
        "SIGN8": {"label": "8-bit sign bit", "description": "Sign bit flag for signed 8-bit values (0x80)."},
        "SIGN16": {"label": "16-bit sign bit", "description": "Sign bit flag for signed 16-bit values (0x8000)."},
        "SIGN24": {"label": "24-bit sign bit", "description": "Sign bit flag for signed 24-bit values (0x800000)."},
        "SIGN32": {"label": "32-bit sign bit", "description": "Sign bit flag for signed 32-bit values (0x80000000)."},
        "MS_PER_SECOND": {"label": "milliseconds per second", "description": "Unit conversion constant (1000)."},
        "SECONDS_PER_MINUTE": {"label": "seconds per minute", "description": "Unit conversion constant (60)."},
        "MINUTES_PER_HOUR": {"label": "minutes per hour", "description": "Unit conversion constant (60)."},
        "HOURS_PER_DAY": {"label": "hours per day", "description": "Unit conversion constant (24)."},
        "DAYS_PER_WEEK": {"label": "days per week", "description": "Unit conversion constant (7)."},
        "BYTES_PER_KIB": {"label": "bytes per KiB", "description": "Binary-size conversion constant (1024)."},
        "BYTES_PER_MIB": {"label": "bytes per MiB", "description": "Binary-size conversion constant (1,048,576)."},
        "MM_PER_METER": {"label": "millimetres per metre", "description": "Metric conversion constant (1000)."},
        "CM_PER_METER": {"label": "centimetres per metre", "description": "Metric conversion constant (100)."},
        "GRAMS_PER_KILOGRAM": {"label": "grams per kilogram", "description": "Metric conversion constant (1000)."},
        "PI_Q16": {"label": "pi (Q16.16)", "description": "Pi in fixed-point Q16.16 format."},
        "RAD_PER_DEG_Q16": {"label": "radians per degree (Q16.16)", "description": "Radians-per-degree in Q16.16 format."},
        "DEG_PER_RAD_Q16": {"label": "degrees per radian (Q16.16)", "description": "Degrees-per-radian in Q16.16 format."},
    },
}


def _canonical_named_constant_key(name: str):
    if not name:
        return None
    key = str(name).strip().upper()
    if key.startswith("METHOD_"):
        return "HTTP_" + key
    if key.startswith("STATUS_"):
        return "HTTP_" + key
    if key.startswith("HTTPMETHOD."):
        return "HTTP_METHOD_" + key.split(".", 1)[1]
    if key.startswith("HTTPSTATUS."):
        return "HTTP_STATUS_" + key.split(".", 1)[1]
    if key.startswith("DAY."):
        return "DAY_" + key.split(".", 1)[1]
    if key.startswith("MONTH."):
        return "MONTH_" + key.split(".", 1)[1]
    if key.startswith("TZ."):
        return "TZ_" + key.split(".", 1)[1]
    if key.startswith("TIMEZONE."):
        return "TZ_" + key.split(".", 1)[1]
    if key.startswith("DST."):
        return "DST_" + key.split(".", 1)[1]
    if key.startswith("CURRENCY."):
        return "CURRENCY_" + key.split(".", 1)[1]
    if key.startswith("CURRENCYMINOR."):
        return "CURRENCY_MINOR_" + key.split(".", 1)[1]
    if key.startswith("COUNTRY."):
        return "COUNTRY_" + key.split(".", 1)[1]
    if key.startswith("UOM."):
        return "UOM_" + key.split(".", 1)[1]
    if key.startswith("COLOR."):
        return "COLOR_" + key.split(".", 1)[1]
    return key


def resolve_named_constant(name: str):
    """Resolve a language-level named constant to int, or None."""
    if not name:
        return None
    return NAMED_CONSTANTS.get(str(name).strip().upper())


def _split_constant_words(canonical: str):
    return [w for w in str(canonical).strip().replace(".", "_").split("_") if w]


def _title_constant(canonical: str):
    words = _split_constant_words(canonical)
    return " ".join(w.capitalize() for w in words) if words else str(canonical)


def _default_en_constant_meta(canonical: str, value: int):
    if canonical.startswith("CURRENCY_MINOR_"):
        code = canonical.split("_", 2)[2]
        return {
            "label": f"{code} minor units",
            "description": f"Decimal places used by {code} currency amounts.",
        }
    if canonical.startswith("CURRENCY_"):
        code = canonical.split("_", 1)[1]
        return {
            "label": f"{code} currency",
            "description": f"ISO-4217 numeric currency code for {code}.",
        }
    if canonical.startswith("COUNTRY_"):
        code = canonical.split("_", 1)[1]
        return {
            "label": f"{code} country",
            "description": f"ISO-3166-1 numeric country code for {code}.",
        }
    if canonical.startswith("TZ_"):
        zone = canonical.split("_", 1)[1]
        return {
            "label": zone.replace("_", "/"),
            "description": "Stable timezone enum ID (host maps this to tzdb rules).",
        }
    if canonical.startswith("DST_"):
        return {
            "label": _title_constant(canonical.replace("DST_", "DST ")),
            "description": "Daylight-saving state enum value.",
        }
    return {
        "label": _title_constant(canonical),
        "description": f"Named constant value {int(value)}.",
    }


def _normalize_meta_entry(entry):
    if isinstance(entry, dict):
        out = {}
        if "label" in entry and entry["label"] is not None:
            out["label"] = str(entry["label"])
        if "description" in entry and entry["description"] is not None:
            out["description"] = str(entry["description"])
        return out
    if isinstance(entry, str):
        return {"label": entry}
    return {}


def _resolve_user_locale_entries(user_dictionary, locale_key: str):
    if not isinstance(user_dictionary, dict):
        return {}
    out = {}
    for key, value in user_dictionary.items():
        if isinstance(value, dict) and ("label" in value or "description" in value):
            out[str(key).strip().upper()] = _normalize_meta_entry(value)
        elif isinstance(value, str):
            out[str(key).strip().upper()] = _normalize_meta_entry(value)
    base = locale_key.split("-", 1)[0]
    for lk in (locale_key, base):
        scoped = user_dictionary.get(lk)
        if not isinstance(scoped, dict):
            continue
        for key, value in scoped.items():
            out[str(key).strip().upper()] = _normalize_meta_entry(value)
    return out


def describe_named_constant(name: str, locale: str = "en", user_dictionary=None):
    """Return localizable pretty-print metadata for a named constant."""
    if not name:
        return None
    raw = str(name).strip().upper()
    value = NAMED_CONSTANTS.get(raw)
    if value is None:
        return None
    canonical = _canonical_named_constant_key(raw)
    locale_key = str(locale or "en")
    locale_entries = (
        NAMED_CONSTANT_I18N.get(locale_key)
        or NAMED_CONSTANT_I18N.get(locale_key.split("-", 1)[0])
        or NAMED_CONSTANT_I18N.get("en", {})
    )
    meta = dict(_default_en_constant_meta(canonical, value))
    meta.update(NAMED_CONSTANT_I18N.get("en", {}).get(canonical, {}))
    meta.update(locale_entries.get(canonical, {}))
    user_entries = _resolve_user_locale_entries(user_dictionary, locale_key)
    meta.update(user_entries.get(canonical, {}))
    meta.update(user_entries.get(raw, {}))
    label = meta.get("label") or canonical
    description = meta.get("description") or f"Named constant value {int(value)}."
    return {
        "name": canonical,
        "value": int(value),
        "label": label,
        "description": description,
        "locale": locale_key,
    }


def to_locale(name: str, locale: str = "en", user_dictionary=None, include_description: bool = True):
    """Format a named constant using built-in English metadata plus optional locale overrides."""
    meta = describe_named_constant(name, locale=locale, user_dictionary=user_dictionary)
    if meta is None:
        return None
    if include_description:
        return f"{meta['label']} ({meta['value']}): {meta['description']}"
    return f"{meta['label']} ({meta['value']})"


def toLocale(name: str, locale: str = "en", user_dictionary=None, include_description: bool = True):
    """camelCase alias for to_locale()."""
    return to_locale(
        name,
        locale=locale,
        user_dictionary=user_dictionary,
        include_description=include_description,
    )


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
        # Invariant: _compile_flow is only called for methods in NAMESPACE_MAP["Flow"],
        # which is exactly {Return, Jump, Call, Branch} — all handled above.
        assert False, f"_compile_flow: unreachable — unhandled method {method!r}"

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
        # Invariant: _compile_basic_statement is only called when _looks_basic_statement()
        # returns True, which requires at least one non-empty token. parts is never empty.
        assert parts, f"_compile_basic_statement: unreachable empty parts from {line!r}"
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
        # Invariant: only called from _compile_basic_statement when method_token in
        # ("LOAD", "SAVE", "PIPE"), so opcode is never None.
        assert opcode is not None, f"_compile_basic_storage: unreachable — unexpected method {method!r}"
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
        # Invariant: only called when _looks_basic_statement() returned True, which
        # means namespace_token is in _basic_namespaces() ⊆ NAMESPACE_MAP keys.
        # _canonical_namespace always finds it — namespace is never None here.
        assert namespace is not None, (
            f"_compile_basic_host_hook: unreachable — {namespace_token!r} not in NAMESPACE_MAP"
        )
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
            else:
                # Invariant: all Span methods in HOST_HOOK_CODES are handled above.
                assert False, f"_compile_host_hook: unhandled Span method {method!r}"
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
            else:
                assert False, f"_compile_host_hook: unhandled Descriptor method {method!r}"
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
            else:
                assert False, f"_compile_host_hook: unhandled Lease method {method!r}"
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
            else:
                assert False, f"_compile_host_hook: unhandled Storage method {method!r}"

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
        elif opcode == OP_RAISE:  # pragma: no branch — exhaustive: all 16 opcodes handled above
            lines.append(f"    Thread.Raise({imm16});")

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
            # Invariant: all 16 opcodes (0-15) are handled above.
            assert False, f"decompile_basic: unhandled opcode {opcode:#x}"

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
            # Invariant: all 16 opcodes (0-15) are handled above.
            assert False, f"decompile_python: unhandled opcode {opcode:#x}"

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
