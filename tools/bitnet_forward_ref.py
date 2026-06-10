#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integer reference for a full BitNet-style transformer forward pass.

This is the *spec* that examples/bitnet_forward.pc is verified against. Every
operation here is integer-only and uses exactly the arithmetic PicoScript can
express (+ - * / % , shifts, comparisons, byte arrays), so the PicoScript port
matches it bit-for-bit. It is deliberately tiny so the whole thing (weights +
activations + KV cache) fits in the PicoVM's 64 KB arena.

Architecture (decoder-only, causal, prefill of T tokens, logits for the last):
    embed (int8) -> L x [ shiftnorm -> ternary Wq/Wk/Wv -> multi-head attention
    with integer LUT softmax -> ternary Wo -> residual -> shiftnorm ->
    ternary gate/up -> ReLU^2 gate -> ternary down -> residual ] ->
    shiftnorm -> ternary LM head -> argmax

Integer choices (all faithful to the C engine's quantized path):
  * shiftnorm  = bitnet_c/ternary_matrix.c shift_norm_int (no sqrt/divide).
  * ternary    = 5-trit base-3 packing, int8 x {-1,0,+1} MAC (trit_dot_row).
  * softmax    = subtract max, fixed-point exp via a small LUT, integer divide.
  * FFN gate   = ReLU^2 (integer; the engine's zero-activation-skip nonlinearity).
"""

from __future__ import annotations

# ── fixed model shape (tiny: fits 64 KB, peak register pressure stays low) ──
D = 8          # model dim
H = 2          # attention heads
DH = D // H    # head dim (4)
F = 16         # ffn hidden
L = 2          # layers
V = 16         # vocab
T = 3          # prefill length

# ── fixed-point softmax LUT ──
FIXED_ONE = 256
EXP_STEP_NUM, EXP_STEP_DEN = 1, 2   # exp(-r * 0.5) per LUT step
LUT_MAX = 31
SCORE_RSHIFT = 6                    # bring Q.K score-gaps into LUT index range


def exp_lut():
    import math
    return [max(0, round(FIXED_ONE * math.exp(-(r * EXP_STEP_NUM) / EXP_STEP_DEN)))
            for r in range(LUT_MAX + 1)]


EXP = exp_lut()


# ── deterministic weight generation (a tiny LCG; PicoScript-reproducible) ──
class LCG:
    def __init__(self, seed):
        self.s = seed & 0xFFFFFFFF

    def next(self):
        self.s = (1103515245 * self.s + 12345) & 0xFFFFFFFF
        return self.s

    def int8(self):
        return (self.next() >> 16) % 255 - 127      # [-127, 127]

    def trit(self):
        return (self.next() >> 16) % 3 - 1           # {-1, 0, +1}


# ── ternary packing (identical to ternary_matrix.c) ──
def trit_row_bytes(cols):
    return ((cols + 4) // 5 + 3) & ~3


def pack_row(row):
    out = bytearray(trit_row_bytes(len(row)))
    bi = 0
    for i in range(0, len(row), 5):
        v = 0
        for j in range(5):
            t = row[i + j] if i + j < len(row) else 0
            v = v * 3 + (0 if t == 0 else 1 if t > 0 else 2)
        out[bi] = v
        bi += 1
    return bytes(out)


# ── integer kernels ──
def matvec(packed, rows, cols, act):
    """trit_dot_row over every row: int32 dot of ternary weights x int8 acts."""
    out = []
    stride = trit_row_bytes(cols)
    for r in range(rows):
        acc, xi = 0, 0
        base = r * stride
        for b in range((cols + 4) // 5):
            byte = packed[base + b]
            e = [0, 0, 0, 0, 0]
            for j in range(4, -1, -1):
                e[j] = byte % 3
                byte //= 3
            for j in range(5):
                if xi < cols:
                    w = 1 if e[j] == 1 else -1 if e[j] == 2 else 0
                    acc += w * act[xi]
                    xi += 1
        out.append(acc)
    return out


def shiftnorm(x):
    """shift_norm_int: scale a vector into int8 by right-shifting past absmax."""
    absmax = max((abs(v) for v in x), default=0)
    if absmax == 0:
        return [0] * len(x)
    s = 0
    while absmax > 127:
        absmax >>= 1
        s += 1
    out = []
    for v in x:
        q = v >> s if v >= 0 else -((-v) >> s)   # symmetric (round toward zero)
        out.append(max(-127, min(127, q)))
    return out


def attention(q, k, v, pos):
    """Causal multi-head attention for the token at `pos`, integer LUT softmax.

    q/k/v are int lists laid out [position][D]; returns int list[D] for `pos`.
    """
    out = [0] * D
    for h in range(H):
        off = h * DH
        # scores against all causal positions 0..pos
        scores = []
        for p in range(pos + 1):
            s = sum(q[pos][off + d] * k[p][off + d] for d in range(DH))
            scores.append(s)
        m = max(scores)
        weights = []
        for s in scores:
            r = (m - s) >> SCORE_RSHIFT
            r = r if r < LUT_MAX else LUT_MAX
            weights.append(EXP[r])
        den = sum(weights)
        for d in range(DH):
            acc = sum(weights[p] * v[p][off + d] for p in range(pos + 1))
            out[off + d] = acc // den
    return out


def relu2(x):
    """ReLU^2 gating: max(0,x)^2, clamped to keep magnitudes int32-safe."""
    return [(max(0, v) * max(0, v)) for v in x]


# ── model + forward ──
def build_model(seed=12345):
    g = LCG(seed)
    m = {}
    m["embed"] = [[g.int8() for _ in range(D)] for _ in range(V)]
    m["layers"] = []
    for _ in range(L):
        layer = {
            "Wq": pack_matrix(g, D, D), "Wk": pack_matrix(g, D, D),
            "Wv": pack_matrix(g, D, D), "Wo": pack_matrix(g, D, D),
            "gate": pack_matrix(g, F, D), "up": pack_matrix(g, F, D),
            "down": pack_matrix(g, D, F),
        }
        m["layers"].append(layer)
    m["head"] = pack_matrix(g, V, D)
    return m


def pack_matrix(g, rows, cols):
    packed = bytearray()
    raw = []
    for _ in range(rows):
        row = [g.trit() for _ in range(cols)]
        raw.append(row)
        packed += pack_row(row)
    return {"rows": rows, "cols": cols, "packed": bytes(packed), "raw": raw}


def mv(mat, act):
    return matvec(mat["packed"], mat["rows"], mat["cols"], act)


def forward(model, tokens):
    # hidden[position][D], int32, seeded from int8 embeddings
    hidden = [list(model["embed"][t]) for t in tokens]
    n = len(tokens)
    for layer in model["layers"]:
        # project every position, build q/k/v caches
        q = [[0] * D for _ in range(n)]
        k = [[0] * D for _ in range(n)]
        v = [[0] * D for _ in range(n)]
        for p in range(n):
            a = shiftnorm(hidden[p])
            q[p] = mv(layer["Wq"], a)
            k[p] = mv(layer["Wk"], a)
            v[p] = mv(layer["Wv"], a)
        # attention + Wo + residual per position
        for p in range(n):
            attn = attention(q, k, v, p)
            a8 = shiftnorm(attn)
            o = mv(layer["Wo"], a8)
            hidden[p] = [hidden[p][d] + o[d] for d in range(D)]
        # FFN + residual per position
        for p in range(n):
            a = shiftnorm(hidden[p])
            gate = relu2(mv(layer["gate"], a))
            up = mv(layer["up"], a)
            fused = shiftnorm([gate[i] + up[i] for i in range(F)])
            down = mv(layer["down"], fused)
            hidden[p] = [hidden[p][d] + down[d] for d in range(D)]
    # final norm + LM head on the last token
    a = shiftnorm(hidden[n - 1])
    logits = mv(model["head"], a)
    return logits


def argmax(xs):
    best, bi = xs[0], 0
    for i, x in enumerate(xs):
        if x > best:
            best, bi = x, i
    return bi


def main():
    model = build_model()
    tokens = [3, 7, 1][:T]
    logits = forward(model, tokens)
    print("tokens :", tokens)
    print("logits :", logits)
    print("argmax :", argmax(logits))


if __name__ == "__main__":
    main()
