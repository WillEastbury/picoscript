#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supervised fine-tune a tiny (Bit)Net-Llama on the verified PicoScript corpus.

This is the training half of the "tiny coding harness": it teaches a small
model to emit PicoScript from natural-language instructions, using the
machine-verified chat dataset produced by tools/gen_dataset.py.

Pipeline:
    HF float base (e.g. ../bitnet_tiny/bitnet_llama_70m)
        --> SFT on data/{train,val}.chat.jsonl  (assistant-only loss)
        --> save fine-tuned float model
        --> (separately) `bitnet convert` packs it to 1.58-bit ternary, which
            the C engine OR examples/bitnet_ternary_matvec.pc then runs.

Designed for an 8 GB GPU (RTX A2000): full fine-tune of a 70M model fits with
room to spare. Use --smoke for a 2-step end-to-end sanity run on the GPU.

Examples:
    python tools/finetune_picoscript.py --smoke
    python tools/finetune_picoscript.py --base ../bitnet_tiny/bitnet_llama_70m \
        --epochs 3 --batch 8 --out ../bitnet_tiny/model_picoscript_70m
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import torch
from torch.utils.data import DataLoader, Dataset

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DEFAULT_BASE = os.path.join(ROOT, "..", "bitnet_tiny", "bitnet_llama_70m")


def ensure_dataset() -> tuple[str, str]:
    """Generate the (gitignored) train/val chat splits if they're missing."""
    train = os.path.join(DATA_DIR, "train.chat.jsonl")
    val = os.path.join(DATA_DIR, "val.chat.jsonl")
    if not (os.path.exists(train) and os.path.exists(val)):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import gen_dataset  # noqa: WPS433
        gen_dataset.main([])
    return train, val


def load_chat(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def by_role(messages: list[dict], role: str) -> str:
    for m in messages:
        if m["role"] == role:
            return m["content"]
    return ""


class ChatSFT(Dataset):
    """Tokenizes chat triples with loss masked to the assistant completion."""

    def __init__(self, rows, tok, max_len):
        self.examples = []
        for row in rows:
            msgs = row["messages"]
            prompt = (
                f"<|system|>\n{by_role(msgs, 'system')}\n"
                f"<|user|>\n{by_role(msgs, 'user')}\n"
                f"<|assistant|>\n"
            )
            completion = by_role(msgs, "assistant") + (tok.eos_token or "")
            p_ids = tok(prompt, add_special_tokens=False).input_ids
            c_ids = tok(completion, add_special_tokens=False).input_ids
            ids = ([tok.bos_token_id] if tok.bos_token_id is not None else []) + p_ids + c_ids
            labels = ([-100] if tok.bos_token_id is not None else []) + [-100] * len(p_ids) + c_ids
            ids = ids[:max_len]
            labels = labels[:max_len]
            self.examples.append((ids, labels))

    def __len__(self):
        return len(self.examples)

    def __getitem__(self, i):
        return self.examples[i]


def make_collate(pad_id):
    def collate(batch):
        n = max(len(ids) for ids, _ in batch)
        input_ids, attn, labels = [], [], []
        for ids, lab in batch:
            pad = n - len(ids)
            input_ids.append(ids + [pad_id] * pad)
            attn.append([1] * len(ids) + [0] * pad)
            labels.append(lab + [-100] * pad)
        return (
            torch.tensor(input_ids, dtype=torch.long),
            torch.tensor(attn, dtype=torch.long),
            torch.tensor(labels, dtype=torch.long),
        )
    return collate


@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    tot, ntok = 0.0, 0
    for input_ids, attn, labels in loader:
        out = model(input_ids=input_ids.to(device), attention_mask=attn.to(device),
                    labels=labels.to(device))
        valid = (labels != -100).sum().item()
        tot += out.loss.item() * valid
        ntok += valid
    return tot / max(ntok, 1)


@torch.no_grad()
def sample(model, tok, device, instruction, dialect="C-style", max_new=96):
    model.eval()
    sys_prompt = (
        "You write PicoScript, a deterministic integer-only language that compiles "
        "to a frozen 16-opcode bytecode. Output only the code, no prose."
    )
    prompt = f"<|system|>\n{sys_prompt}\n<|user|>\n{instruction}\n<|assistant|>\n"
    ids = torch.tensor([[tok.bos_token_id] + tok(prompt, add_special_tokens=False).input_ids],
                       device=device)
    out = model.generate(ids, max_new_tokens=max_new, do_sample=False,
                         pad_token_id=tok.pad_token_id)
    return tok.decode(out[0, ids.shape[1]:], skip_special_tokens=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default=DEFAULT_BASE, help="HF base model dir")
    p.add_argument("--out", default=os.path.join(ROOT, "..", "bitnet_tiny", "model_picoscript_70m"))
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--max-len", type=int, default=384)
    p.add_argument("--grad-accum", type=int, default=1)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--smoke", action="store_true", help="2-step GPU sanity run, no save")
    args = p.parse_args(argv)

    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = torch.device(args.device)
    dtype = torch.bfloat16 if (device.type == "cuda" and torch.cuda.is_bf16_supported()) else torch.float32

    train_path, val_path = ensure_dataset()
    print(f"[data] {train_path}  +  {val_path}")

    tok = AutoTokenizer.from_pretrained(args.base, use_fast=True)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    print(f"[model] loading {args.base}  (dtype={dtype}, device={device})")
    model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=dtype)
    model.to(device)
    model.gradient_checkpointing_enable()
    model.config.use_cache = False

    train_rows = load_chat(train_path)
    val_rows = load_chat(val_path)
    if args.smoke:
        train_rows, val_rows = train_rows[:16], val_rows[:8]

    train_ds = ChatSFT(train_rows, tok, args.max_len)
    val_ds = ChatSFT(val_rows, tok, args.max_len)
    collate = make_collate(tok.pad_token_id)
    train_loader = DataLoader(train_ds, batch_size=args.batch, shuffle=True, collate_fn=collate)
    val_loader = DataLoader(val_ds, batch_size=args.batch, shuffle=False, collate_fn=collate)
    print(f"[data] train={len(train_ds)}  val={len(val_ds)}  "
          f"params={sum(p.numel() for p in model.parameters())/1e6:.1f}M")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.0)
    max_steps = 2 if args.smoke else None

    step = 0
    for epoch in range(args.epochs):
        model.train()
        opt.zero_grad()
        for i, (input_ids, attn, labels) in enumerate(train_loader):
            out = model(input_ids=input_ids.to(device), attention_mask=attn.to(device),
                        labels=labels.to(device))
            (out.loss / args.grad_accum).backward()
            if (i + 1) % args.grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                opt.zero_grad()
                step += 1
            if step and step % 10 == 0:
                print(f"  epoch {epoch} step {step} loss {out.loss.item():.4f}")
            if max_steps and step >= max_steps:
                break
        vloss = evaluate(model, val_loader, device)
        print(f"[eval] epoch {epoch}: val loss {vloss:.4f}  ppl {math.exp(min(vloss, 20)):.2f}")
        if max_steps and step >= max_steps:
            break

    print("[sample] instruction='Set x to 7; print x times 3.'")
    print(sample(model, tok, device, "Set x to 7; print x times 3."))

    if args.smoke:
        print("[smoke] OK -- model loaded, trained 2 steps, evaluated and sampled on GPU.")
        return

    os.makedirs(args.out, exist_ok=True)
    model.config.use_cache = True
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"[save] fine-tuned model -> {args.out}")
    print("[next] convert to 1.58-bit ternary:  bitnet convert "
          f"{args.out} {os.path.join(args.out, 'packed')}")


if __name__ == "__main__":
    main()
