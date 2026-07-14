# PicoScript Method Reference

**Version:** v0.3 (Lease-first, Case-insensitive v2 Language)

## Overview

This document provides a comprehensive reference for all PicoScript methods, organized by namespace. Each method shows:

- **Opcode**: Internal bytecode instruction
- **Hook Code**: Hexadecimal encoding for host hooks (reserved imm16 range)
- **v2 Syntax**: Case-insensitive, block-structured syntax example
- **Conformance Level**: L0 (minimal) through L6 (full security/crypto)

## Table of Contents

- [Attention](#attention)
- [Auth](#auth)
- [Base64](#base64)
- [Binary](#binary)
- [BitLinear](#bitlinear)
- [Bits](#bits)
- [Capability](#capability)
- [Capsule](#capsule)
- [Compress](#compress)
- [Context](#context)
- [Crypto](#crypto)
- [DateTime](#datetime)
- [Descriptor](#descriptor)
- [Dot8](#dot8)
- [Dsp](#dsp)
- [Encoding](#encoding)
- [Env](#env)
- [Environment](#environment)
- [Error](#error)
- [Flow](#flow)
- [Html](#html)
- [Http](#http)
- [Io](#io)
- [Json](#json)
- [Kernel](#kernel)
- [Kv](#kv)
- [Lease](#lease)
- [Locale](#locale)
- [Map](#map)
- [Math](#math)
- [Maths](#maths)
- [Memory](#memory)
- [Model](#model)
- [Net](#net)
- [Number](#number)
- [Principal](#principal)
- [Process](#process)
- [Quant](#quant)
- [Query](#query)
- [Queue](#queue)
- [Random](#random)
- [Sampling](#sampling)
- [Sandbox](#sandbox)
- [Scheduler](#scheduler)
- [Search](#search)
- [Span](#span)
- [Storage](#storage)
- [String](#string)
- [Template](#template)
- [Tensor](#tensor)
- [TextRender](#textrender)
- [Thread](#thread)
- [Timer](#timer)
- [Tokenizer](#tokenizer)
- [Utf8Reader](#utf8reader)
- [Utf8Writer](#utf8writer)
- [X509](#x509)
- [Xml](#xml)

---

## Attention

**Conformance Level:** L0  
**Methods:** 4

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Attend | 0x00 | 0x7253 | `Attention.Attend(...)` |
| Mix | 0x00 | 0x7252 | `Attention.Mix(...)` |
| Scores | 0x00 | 0x7251 | `Attention.Scores(...)` |
| SetShape | 0x00 | 0x7250 | `Attention.SetShape(...)` |

## Auth

**Conformance Level:** L0  
**Methods:** 10

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| GetToken | 0x00 | 0x7125 | `Auth.GetToken(...)` |
| GetUserCredentials | 0x00 | 0x7120 | `Auth.GetUserCredentials(...)` |
| GetUserPermissions | 0x00 | 0x7123 | `Auth.GetUserPermissions(...)` |
| RefreshToken | 0x00 | 0x7128 | `Auth.RefreshToken(...)` |
| RequestToken | 0x00 | 0x7124 | `Auth.RequestToken(...)` |
| RevokeToken | 0x00 | 0x7129 | `Auth.RevokeToken(...)` |
| SwitchTokenContext | 0x00 | 0x7127 | `Auth.SwitchTokenContext(...)` |
| SwitchUserContext | 0x00 | 0x7122 | `Auth.SwitchUserContext(...)` |
| ValidateCredentials | 0x00 | 0x7121 | `Auth.ValidateCredentials(...)` |
| ValidateToken | 0x00 | 0x7126 | `Auth.ValidateToken(...)` |

## Base64

**Conformance Level:** L0  
**Methods:** 4

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Decode | 0x00 | 0x72D1 | `Base64.Decode(...)` |
| Encode | 0x00 | 0x72D0 | `Base64.Encode(...)` |
| UrlDecode | 0x00 | 0x72D2 | `Base64.UrlDecode(...)` |
| UrlEncode | 0x00 | 0x72D3 | `Base64.UrlEncode(...)` |

## Binary

**Conformance Level:** L0  
**Methods:** 6

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| ParseCard | 0x00 | 0x7341 | `Binary.ParseCard(...)` |
| ParseEntity | 0x00 | 0x7343 | `Binary.ParseEntity(...)` |
| SerializeCard | 0x00 | 0x7342 | `Binary.SerializeCard(...)` |
| SerializeEntity | 0x00 | 0x7344 | `Binary.SerializeEntity(...)` |
| SetKey | 0x00 | 0x7345 | `Binary.SetKey(...)` |
| Verify | 0x00 | 0x7346 | `Binary.Verify(...)` |

## BitLinear

**Conformance Level:** L0  
**Methods:** 8

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| HasFormat | 0x00 | 0x71F4 | `BitLinear.HasFormat(...)` |
| MatVecBase3 | 0x00 | 0x71F3 | `BitLinear.MatVecBase3(...)` |
| MatVecBase3Block | 0x00 | 0x7276 | `BitLinear.MatVecBase3Block(...)` |
| MatVecBitmap | 0x00 | 0x71F2 | `BitLinear.MatVecBitmap(...)` |
| MatVecBitmapBlock | 0x00 | 0x7275 | `BitLinear.MatVecBitmapBlock(...)` |
| MatVecTernary | 0x00 | 0x71F1 | `BitLinear.MatVecTernary(...)` |
| MatVecTernaryBlock | 0x00 | 0x7274 | `BitLinear.MatVecTernaryBlock(...)` |
| SetShape | 0x00 | 0x71F0 | `BitLinear.SetShape(...)` |

## Bits

**Conformance Level:** L0  
**Methods:** 7

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| And | 0x00 | 0x703A | `Bits.And(...)` |
| Not | 0x00 | 0x704F | `Bits.Not(...)` |
| Or | 0x00 | 0x703B | `Bits.Or(...)` |
| Sar | 0x00 | 0x703F | `Bits.Sar(...)` |
| Shl | 0x00 | 0x703D | `Bits.Shl(...)` |
| Shr | 0x00 | 0x703E | `Bits.Shr(...)` |
| Xor | 0x00 | 0x703C | `Bits.Xor(...)` |

## Capability

**Conformance Level:** L0  
**Methods:** 3

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Drop | 0x00 | 0x72A5 | `Capability.Drop(...)` |
| Has | 0x00 | 0x72A3 | `Capability.Has(...)` |
| Request | 0x00 | 0x72A4 | `Capability.Request(...)` |

## Capsule

**Conformance Level:** L0  
**Methods:** 5

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Call | 0x00 | 0x72C0 | `Capsule.Call(...)` |
| Jump | 0x00 | 0x72C2 | `Capsule.Jump(...)` |
| LoadModule | 0x00 | 0x72C3 | `Capsule.LoadModule(...)` |
| RunModule | 0x00 | 0x72C4 | `Capsule.RunModule(...)` |
| Schedule | 0x00 | 0x72C1 | `Capsule.Schedule(...)` |

## Compress

**Conformance Level:** L0  
**Methods:** 8

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| BrotliCompress | 0x00 | 0x7100 | `Compress.BrotliCompress(...)` |
| BrotliDecompress | 0x00 | 0x7101 | `Compress.BrotliDecompress(...)` |
| DeflateCompress | 0x00 | 0x7106 | `Compress.DeflateCompress(...)` |
| DeflateDecompress | 0x00 | 0x7107 | `Compress.DeflateDecompress(...)` |
| GzipCompress | 0x00 | 0x7104 | `Compress.GzipCompress(...)` |
| GzipDecompress | 0x00 | 0x7105 | `Compress.GzipDecompress(...)` |
| PicoCompress | 0x00 | 0x7102 | `Compress.PicoCompress(...)` |
| PicoDecompress | 0x00 | 0x7103 | `Compress.PicoDecompress(...)` |

## Context

**Conformance Level:** L3  
**Methods:** 15

Execution context: user, permissions, request metadata, scratch.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| GetBody | 0x00 | 0x70E9 | `Context.GetBody(...)` |
| GetClientCert | 0x00 | 0x70ED | `Context.GetClientCert(...)` |
| GetHeaders | 0x00 | 0x70E7 | `Context.GetHeaders(...)` |
| GetHost | 0x00 | 0x70E2 | `Context.GetHost(...)` |
| GetPath | 0x00 | 0x70E1 | `Context.GetPath(...)` |
| GetPermissions | 0x00 | 0x70E6 | `Context.GetPermissions(...)` |
| GetPort | 0x00 | 0x70E3 | `Context.GetPort(...)` |
| GetQueryString | 0x00 | 0x70E8 | `Context.GetQueryString(...)` |
| GetRemoteAddr | 0x00 | 0x70E4 | `Context.GetRemoteAddr(...)` |
| GetRequestId | 0x00 | 0x70EC | `Context.GetRequestId(...)` |
| GetScratchValue | 0x00 | 0x70EB | `Context.GetScratchValue(...)` |
| GetTraceId | 0x00 | 0x70EE | `Context.GetTraceId(...)` |
| GetUser | 0x00 | 0x70E5 | `Context.GetUser(...)` |
| GetVerb | 0x00 | 0x70E0 | `Context.GetVerb(...)` |
| SetScratchValue | 0x00 | 0x70EA | `Context.SetScratchValue(...)` |

## Crypto

**Conformance Level:** L6  
**Methods:** 15

Cryptography: userland hashing, kernel-wrapped keyed ops.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Blake2b | 0x00 | 0x70F2 | `Crypto.Blake2b(...)` |
| Blake3 | 0x00 | 0x70F3 | `Crypto.Blake3(...)` |
| Decrypt | 0x00 | 0x70F9 | `Crypto.Decrypt(...)` |
| DeriveKey | 0x00 | 0x70FB | `Crypto.DeriveKey(...)` |
| Encrypt | 0x00 | 0x70F8 | `Crypto.Encrypt(...)` |
| GenerateKeyPair | 0x00 | 0x70FA | `Crypto.GenerateKeyPair(...)` |
| HmacSha256 | 0x00 | 0x70F4 | `Crypto.HmacSha256(...)` |
| HmacSha512 | 0x00 | 0x70F5 | `Crypto.HmacSha512(...)` |
| Md5 | 0x00 | 0x70FD | `Crypto.Md5(...)` |
| RandomBytes | 0x00 | 0x70FC | `Crypto.RandomBytes(...)` |
| Sha1 | 0x00 | 0x70FE | `Crypto.Sha1(...)` |
| Sha256 | 0x00 | 0x70F0 | `Crypto.Sha256(...)` |
| Sha512 | 0x00 | 0x70F1 | `Crypto.Sha512(...)` |
| Sign | 0x00 | 0x70F6 | `Crypto.Sign(...)` |
| Verify | 0x00 | 0x70F7 | `Crypto.Verify(...)` |

## DateTime

**Conformance Level:** L2  
**Methods:** 15

Date/time: current, components, timestamp, formatting.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| AddDays | 0x00 | 0x70B7 | `DateTime.AddDays(...)` |
| AddHours | 0x00 | 0x70B6 | `DateTime.AddHours(...)` |
| AddMinutes | 0x00 | 0x70B5 | `DateTime.AddMinutes(...)` |
| AddSeconds | 0x00 | 0x70B4 | `DateTime.AddSeconds(...)` |
| Day | 0x00 | 0x70BE | `DateTime.Day(...)` |
| DiffDays | 0x00 | 0x70BB | `DateTime.DiffDays(...)` |
| Format | 0x00 | 0x70B3 | `DateTime.Format(...)` |
| GetDayOfWeek | 0x00 | 0x70B8 | `DateTime.GetDayOfWeek(...)` |
| GetDayOfYear | 0x00 | 0x70B9 | `DateTime.GetDayOfYear(...)` |
| Month | 0x00 | 0x70BD | `DateTime.Month(...)` |
| Now | 0x00 | 0x70B0 | `DateTime.Now(...)` |
| Parse | 0x00 | 0x70B2 | `DateTime.Parse(...)` |
| UnixTimestamp | 0x00 | 0x70BA | `DateTime.UnixTimestamp(...)` |
| UtcNow | 0x00 | 0x70B1 | `DateTime.UtcNow(...)` |
| Year | 0x00 | 0x70BC | `DateTime.Year(...)` |

## Descriptor

**Conformance Level:** L4  
**Methods:** 6

Data descriptor with flags, TTL, reference counting.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| CopyBatch | 0x00 | 0x7055 | `Descriptor.CopyBatch(...)` |
| GetFlags | 0x00 | 0x7054 | `Descriptor.GetFlags(...)` |
| GetLen | 0x00 | 0x7053 | `Descriptor.GetLen(...)` |
| GetPtr | 0x00 | 0x7052 | `Descriptor.GetPtr(...)` |
| Make | 0x00 | 0x7050 | `Descriptor.Make(...)` |
| SetFlags | 0x00 | 0x7051 | `Descriptor.SetFlags(...)` |

## Dot8

**Conformance Level:** L0  
**Methods:** 2

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Len | 0x00 | 0x7056 | `Dot8.Len(...)` |
| Of | 0x00 | 0x7057 | `Dot8.Of(...)` |

## Dsp

**Conformance Level:** L0  
**Methods:** 16

Digital signal processing: neural network ops, matrix operations.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Concat | 0x0F+0x0E | - | `Dsp.Concat(...)` |
| Dequant | 0x0F+0x0C | - | `Dsp.Dequant(...)` |
| Dot | 0x0F+0x02 | - | `Dsp.Dot(...)` |
| Embed | 0x0F+0x0A | - | `Dsp.Embed(...)` |
| Gelu | 0x0F+0x07 | - | `Dsp.Gelu(...)` |
| Mask | 0x0F+0x0D | - | `Dsp.Mask(...)` |
| MatMul | 0x0F+0x00 | - | `Dsp.MatMul(...)` |
| Norm | 0x0F+0x05 | - | `Dsp.Norm(...)` |
| Quant | 0x0F+0x0B | - | `Dsp.Quant(...)` |
| Relu | 0x0F+0x04 | - | `Dsp.Relu(...)` |
| Scale | 0x0F+0x03 | - | `Dsp.Scale(...)` |
| Softmax | 0x0F+0x01 | - | `Dsp.Softmax(...)` |
| Split | 0x0F+0x0F | - | `Dsp.Split(...)` |
| TopK | 0x0F+0x06 | - | `Dsp.TopK(...)` |
| Transpose | 0x0F+0x08 | - | `Dsp.Transpose(...)` |
| VAdd | 0x0F+0x09 | - | `Dsp.VAdd(...)` |

## Encoding

**Conformance Level:** L0  
**Methods:** 12

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| AsciiDecode | 0x00 | 0x7311 | `Encoding.AsciiDecode(...)` |
| AsciiEncode | 0x00 | 0x7310 | `Encoding.AsciiEncode(...)` |
| HexDecode | 0x00 | 0x731B | `Encoding.HexDecode(...)` |
| HexEncode | 0x00 | 0x731A | `Encoding.HexEncode(...)` |
| Utf16BEDecode | 0x00 | 0x7317 | `Encoding.Utf16BEDecode(...)` |
| Utf16BEEncode | 0x00 | 0x7316 | `Encoding.Utf16BEEncode(...)` |
| Utf16LEDecode | 0x00 | 0x7315 | `Encoding.Utf16LEDecode(...)` |
| Utf16LEEncode | 0x00 | 0x7314 | `Encoding.Utf16LEEncode(...)` |
| Utf7Decode | 0x00 | 0x7319 | `Encoding.Utf7Decode(...)` |
| Utf7Encode | 0x00 | 0x7318 | `Encoding.Utf7Encode(...)` |
| Utf8Decode | 0x00 | 0x7313 | `Encoding.Utf8Decode(...)` |
| Utf8Encode | 0x00 | 0x7312 | `Encoding.Utf8Encode(...)` |

## Env

**Conformance Level:** L0  
**Methods:** 4

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Count | 0x00 | 0x728A | `Env.Count(...)` |
| Get | 0x00 | 0x7288 | `Env.Get(...)` |
| Key | 0x00 | 0x728B | `Env.Key(...)` |
| Set | 0x00 | 0x7289 | `Env.Set(...)` |

## Environment

**Conformance Level:** L3  
**Methods:** 9

System: env vars, time, memory/CPU load, hostname, version.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| GetCpuCount | 0x00 | 0x70D1 | `Environment.GetCpuCount(...)` |
| GetElapsedTime | 0x00 | 0x70D8 | `Environment.GetElapsedTime(...)` |
| GetHostname | 0x00 | 0x70D4 | `Environment.GetHostname(...)` |
| GetMemoryFree | 0x00 | 0x70D3 | `Environment.GetMemoryFree(...)` |
| GetMemoryTotal | 0x00 | 0x70D2 | `Environment.GetMemoryTotal(...)` |
| GetOsVersion | 0x00 | 0x70D0 | `Environment.GetOsVersion(...)` |
| GetProcessId | 0x00 | 0x70D6 | `Environment.GetProcessId(...)` |
| GetThreadId | 0x00 | 0x70D7 | `Environment.GetThreadId(...)` |
| GetTimeZone | 0x00 | 0x70D5 | `Environment.GetTimeZone(...)` |

## Error

**Conformance Level:** L0  
**Methods:** 6

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Clear | 0x00 | 0x72B5 | `Error.Clear(...)` |
| Code | 0x00 | 0x72B2 | `Error.Code(...)` |
| Detail | 0x00 | 0x72B3 | `Error.Detail(...)` |
| HasHandler | 0x00 | 0x72B1 | `Error.HasHandler(...)` |
| Resume | 0x00 | 0x72B4 | `Error.Resume(...)` |
| SetHandler | 0x00 | 0x72B0 | `Error.SetHandler(...)` |

## Flow

**Conformance Level:** L1  
**Methods:** 4

Control flow: jumps, branches, function calls, returns.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Branch | 0x0A | - | `Flow.Branch(...)` |
| Call | 0x0B | - | `Flow.Call(...)` |
| Jump | 0x09 | - | `Flow.Jump(...)` |
| Return | 0x0C | - | `Flow.Return(...)` |

## Html

**Conformance Level:** L0  
**Methods:** 10

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| AddChildNode | 0x00 | 0x7141 | `Html.AddChildNode(...)` |
| CreateNode | 0x00 | 0x7140 | `Html.CreateNode(...)` |
| Decode | 0x00 | 0x7147 | `Html.Decode(...)` |
| Encode | 0x00 | 0x7146 | `Html.Encode(...)` |
| GetAttribute | 0x00 | 0x7144 | `Html.GetAttribute(...)` |
| ParseTree | 0x00 | 0x7145 | `Html.ParseTree(...)` |
| QuerySelector | 0x00 | 0x7149 | `Html.QuerySelector(...)` |
| RemoveChildNode | 0x00 | 0x7142 | `Html.RemoveChildNode(...)` |
| Serialize | 0x00 | 0x7148 | `Html.Serialize(...)` |
| SetAttribute | 0x00 | 0x7143 | `Html.SetAttribute(...)` |

## Http

**Conformance Level:** L0  
**Methods:** 12

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| EncodeJson | 0x00 | 0x7137 | `Http.EncodeJson(...)` |
| GenerateHeaders | 0x00 | 0x7132 | `Http.GenerateHeaders(...)` |
| GenerateResponse | 0x00 | 0x7133 | `Http.GenerateResponse(...)` |
| ParseForm | 0x00 | 0x7135 | `Http.ParseForm(...)` |
| ParseJson | 0x00 | 0x7136 | `Http.ParseJson(...)` |
| ParseQuery | 0x00 | 0x7134 | `Http.ParseQuery(...)` |
| ReadBody | 0x00 | 0x7131 | `Http.ReadBody(...)` |
| ReadHeader | 0x00 | 0x7130 | `Http.ReadHeader(...)` |
| Request | 0x00 | 0x7138 | `Http.Request(...)` |
| RespBody | 0x00 | 0x713B | `Http.RespBody(...)` |
| RespHeaders | 0x00 | 0x713A | `Http.RespHeaders(...)` |
| RespStatus | 0x00 | 0x7139 | `Http.RespStatus(...)` |

## Io

**Conformance Level:** L0  
**Methods:** 2

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Write | 0x00 | 0x7071 | `Io.Write(...)` |
| WriteByte | 0x00 | 0x7072 | `Io.WriteByte(...)` |

## Json

**Conformance Level:** L0  
**Methods:** 11

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| BeginArray | 0x00 | 0x7047 | `Json.BeginArray(...)` |
| BeginObject | 0x00 | 0x7045 | `Json.BeginObject(...)` |
| Bool | 0x00 | 0x704C | `Json.Bool(...)` |
| EndArray | 0x00 | 0x7048 | `Json.EndArray(...)` |
| EndObject | 0x00 | 0x7046 | `Json.EndObject(...)` |
| Int | 0x00 | 0x704B | `Json.Int(...)` |
| Key | 0x00 | 0x7049 | `Json.Key(...)` |
| Null | 0x00 | 0x704D | `Json.Null(...)` |
| Parse | 0x00 | 0x7340 | `Json.Parse(...)` |
| Raw | 0x00 | 0x704E | `Json.Raw(...)` |
| Str | 0x00 | 0x704A | `Json.Str(...)` |

## Kernel

**Conformance Level:** L6  
**Methods:** 6

Core kernel interaction: process management, IPC, system control.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| FireSWIRQ | 0x00 | 0x7003 | `Kernel.FireSWIRQ(...)` |
| ProfileEnd | 0x00 | 0x7005 | `Kernel.ProfileEnd(...)` |
| ProfileStart | 0x00 | 0x7004 | `Kernel.ProfileStart(...)` |
| TracePoint | 0x00 | 0x7006 | `Kernel.TracePoint(...)` |
| WaitIRQ | 0x00 | 0x7001 | `Kernel.WaitIRQ(...)` |
| WaitSWIRQ | 0x00 | 0x7002 | `Kernel.WaitSWIRQ(...)` |

## Kv

**Conformance Level:** L0  
**Methods:** 12

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Clear | 0x00 | 0x7236 | `Kv.Clear(...)` |
| Len | 0x00 | 0x7235 | `Kv.Len(...)` |
| ReadK | 0x00 | 0x7233 | `Kv.ReadK(...)` |
| ReadKH | 0x00 | 0x723A | `Kv.ReadKH(...)` |
| ReadV | 0x00 | 0x7234 | `Kv.ReadV(...)` |
| ReadVH | 0x00 | 0x723B | `Kv.ReadVH(...)` |
| SetHead | 0x00 | 0x7237 | `Kv.SetHead(...)` |
| SetShape | 0x00 | 0x7230 | `Kv.SetShape(...)` |
| WriteK | 0x00 | 0x7231 | `Kv.WriteK(...)` |
| WriteKH | 0x00 | 0x7238 | `Kv.WriteKH(...)` |
| WriteV | 0x00 | 0x7232 | `Kv.WriteV(...)` |
| WriteVH | 0x00 | 0x7239 | `Kv.WriteVH(...)` |

## Lease

**Conformance Level:** L4  
**Methods:** 6

Lease lifecycle: acquire, validate, release, stats.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Acquire | 0x00 | 0x7058 | `Lease.Acquire(...)` |
| CachedValidate | 0x00 | 0x705B | `Lease.CachedValidate(...)` |
| GetSpan | 0x00 | 0x705C | `Lease.GetSpan(...)` |
| GetTypeHint | 0x00 | 0x705D | `Lease.GetTypeHint(...)` |
| Release | 0x00 | 0x7059 | `Lease.Release(...)` |
| Validate | 0x00 | 0x705A | `Lease.Validate(...)` |

## Locale

**Conformance Level:** L2  
**Methods:** 7

Locale management: get/set, format/parse, language/region.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| FormatCurrency | 0x00 | 0x70C2 | `Locale.FormatCurrency(...)` |
| FormatDate | 0x00 | 0x70C4 | `Locale.FormatDate(...)` |
| FormatNumber | 0x00 | 0x70C3 | `Locale.FormatNumber(...)` |
| FormatTime | 0x00 | 0x70C5 | `Locale.FormatTime(...)` |
| GetCurrentLocale | 0x00 | 0x70C0 | `Locale.GetCurrentLocale(...)` |
| SetLocale | 0x00 | 0x70C1 | `Locale.SetLocale(...)` |
| Translate | 0x00 | 0x70C6 | `Locale.Translate(...)` |

## Map

**Conformance Level:** L0  
**Methods:** 27

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Clear | 0x00 | 0x7322 | `Map.Clear(...)` |
| Count | 0x00 | 0x7323 | `Map.Count(...)` |
| DelI | 0x00 | 0x7328 | `Map.DelI(...)` |
| DelS | 0x00 | 0x7330 | `Map.DelS(...)` |
| Free | 0x00 | 0x7321 | `Map.Free(...)` |
| GetII | 0x00 | 0x7326 | `Map.GetII(...)` |
| GetIS | 0x00 | 0x732A | `Map.GetIS(...)` |
| GetSI | 0x00 | 0x732E | `Map.GetSI(...)` |
| GetSS | 0x00 | 0x7332 | `Map.GetSS(...)` |
| HasI | 0x00 | 0x7327 | `Map.HasI(...)` |
| HasS | 0x00 | 0x732F | `Map.HasS(...)` |
| Hash | 0x00 | 0x7324 | `Map.Hash(...)` |
| IsNullI | 0x00 | 0x732C | `Map.IsNullI(...)` |
| IsNullS | 0x00 | 0x7334 | `Map.IsNullS(...)` |
| KeyAt | 0x00 | 0x7335 | `Map.KeyAt(...)` |
| KeySpanAt | 0x00 | 0x7336 | `Map.KeySpanAt(...)` |
| New | 0x00 | 0x7320 | `Map.New(...)` |
| PutII | 0x00 | 0x7325 | `Map.PutII(...)` |
| PutIS | 0x00 | 0x7329 | `Map.PutIS(...)` |
| PutNullI | 0x00 | 0x732B | `Map.PutNullI(...)` |
| PutNullS | 0x00 | 0x7333 | `Map.PutNullS(...)` |
| PutSI | 0x00 | 0x732D | `Map.PutSI(...)` |
| PutSS | 0x00 | 0x7331 | `Map.PutSS(...)` |
| Use | 0x00 | 0x733A | `Map.Use(...)` |
| ValAt | 0x00 | 0x7337 | `Map.ValAt(...)` |
| ValIsSpan | 0x00 | 0x7339 | `Map.ValIsSpan(...)` |
| ValSpanAt | 0x00 | 0x7338 | `Map.ValSpanAt(...)` |

## Math

**Conformance Level:** L1  
**Methods:** 5

Mathematical ALU operations: add, subtract, multiply, divide.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Add | 0x04 | - | `Math.Add(...)` |
| Div | 0x07 | - | `Math.Div(...)` |
| Inc | 0x08 | - | `Math.Inc(...)` |
| Mul | 0x06 | - | `Math.Mul(...)` |
| Sub | 0x05 | - | `Math.Sub(...)` |

## Maths

**Conformance Level:** L2  
**Methods:** 12

Mathematical functions: sqrt, trig, log, GCD, LCM.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Clamp | 0x00 | 0x70AA | `Maths.Clamp(...)` |
| Cos | 0x00 | 0x70A1 | `Maths.Cos(...)` |
| Exp | 0x00 | 0x70A7 | `Maths.Exp(...)` |
| Lerp | 0x00 | 0x70AB | `Maths.Lerp(...)` |
| Log | 0x00 | 0x70A5 | `Maths.Log(...)` |
| Log10 | 0x00 | 0x70A6 | `Maths.Log10(...)` |
| Power | 0x00 | 0x70A4 | `Maths.Power(...)` |
| Random | 0x00 | 0x70A8 | `Maths.Random(...)` |
| RandomRange | 0x00 | 0x70A9 | `Maths.RandomRange(...)` |
| Sin | 0x00 | 0x70A0 | `Maths.Sin(...)` |
| Sqrt | 0x00 | 0x70A3 | `Maths.Sqrt(...)` |
| Tan | 0x00 | 0x70A2 | `Maths.Tan(...)` |

## Memory

**Conformance Level:** L4  
**Methods:** 8

Arena allocation and lease-based typed access primitives.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| ArenaAlloc | 0x00 | 0x7031 | `Memory.ArenaAlloc(...)` |
| ArenaInit | 0x00 | 0x7030 | `Memory.ArenaInit(...)` |
| ArenaReset | 0x00 | 0x7032 | `Memory.ArenaReset(...)` |
| ArenaStats | 0x00 | 0x7033 | `Memory.ArenaStats(...)` |
| Get | 0x00 | 0x7037 | `Memory.Get(...)` |
| Peek | 0x00 | 0x7034 | `Memory.Peek(...)` |
| Poke | 0x00 | 0x7035 | `Memory.Poke(...)` |
| Set | 0x00 | 0x7036 | `Memory.Set(...)` |

## Model

**Conformance Level:** L0  
**Methods:** 12

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| GetConfig | 0x00 | 0x7221 | `Model.GetConfig(...)` |
| MatVecI8Block | 0x00 | 0x7273 | `Model.MatVecI8Block(...)` |
| ReadTensor | 0x00 | 0x7227 | `Model.ReadTensor(...)` |
| ReadTensorBlock | 0x00 | 0x7272 | `Model.ReadTensorBlock(...)` |
| ReadTensorRow | 0x00 | 0x7270 | `Model.ReadTensorRow(...)` |
| SetBlock | 0x00 | 0x7271 | `Model.SetBlock(...)` |
| SetConfig | 0x00 | 0x7220 | `Model.SetConfig(...)` |
| TensorCols | 0x00 | 0x7225 | `Model.TensorCols(...)` |
| TensorFormat | 0x00 | 0x7226 | `Model.TensorFormat(...)` |
| TensorOffset | 0x00 | 0x7223 | `Model.TensorOffset(...)` |
| TensorRows | 0x00 | 0x7224 | `Model.TensorRows(...)` |
| TensorView | 0x00 | 0x7222 | `Model.TensorView(...)` |

## Net

**Conformance Level:** L1  
**Methods:** 12

HTTP response framing: status, headers, body, close.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Accept | 0x00 | 0x72E1 | `Net.Accept(...)` |
| Body | 0x00 | - | `Net.Body(...)` |
| Close | 0x00 | - | `Net.Close(...)` |
| Header | 0x00 | - | `Net.Header(...)` |
| Listen | 0x00 | 0x72E0 | `Net.Listen(...)` |
| PoolSize | 0x00 | 0x72E5 | `Net.PoolSize(...)` |
| Read | 0x00 | 0x72E2 | `Net.Read(...)` |
| Register | 0x00 | 0x72E6 | `Net.Register(...)` |
| Shutdown | 0x00 | 0x72E4 | `Net.Shutdown(...)` |
| Status | 0x00 | - | `Net.Status(...)` |
| Type | 0x00 | - | `Net.Type(...)` |
| Write | 0x00 | 0x72E3 | `Net.Write(...)` |

## Number

**Conformance Level:** L2  
**Methods:** 11

Numeric parsing, formatting, and conversion.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Abs | 0x00 | 0x7095 | `Number.Abs(...)` |
| Ceiling | 0x00 | 0x7097 | `Number.Ceiling(...)` |
| Floor | 0x00 | 0x7096 | `Number.Floor(...)` |
| Max | 0x00 | 0x709A | `Number.Max(...)` |
| Min | 0x00 | 0x7099 | `Number.Min(...)` |
| Parse | 0x00 | 0x7090 | `Number.Parse(...)` |
| Round | 0x00 | 0x7098 | `Number.Round(...)` |
| ToBinary | 0x00 | 0x7094 | `Number.ToBinary(...)` |
| ToHex | 0x00 | 0x7092 | `Number.ToHex(...)` |
| ToOctal | 0x00 | 0x7093 | `Number.ToOctal(...)` |
| ToString | 0x00 | 0x7091 | `Number.ToString(...)` |

## Principal

**Conformance Level:** L0  
**Methods:** 3

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Claims | 0x00 | 0x72A2 | `Principal.Claims(...)` |
| Current | 0x00 | 0x72A0 | `Principal.Current(...)` |
| HasRole | 0x00 | 0x72A1 | `Principal.HasRole(...)` |

## Process

**Conformance Level:** L0  
**Methods:** 8

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Args | 0x00 | 0x7287 | `Process.Args(...)` |
| Exit | 0x00 | 0x7283 | `Process.Exit(...)` |
| Kill | 0x00 | 0x7284 | `Process.Kill(...)` |
| Parent | 0x00 | 0x7281 | `Process.Parent(...)` |
| Self | 0x00 | 0x7280 | `Process.Self(...)` |
| Spawn | 0x00 | 0x7282 | `Process.Spawn(...)` |
| Status | 0x00 | 0x7285 | `Process.Status(...)` |
| Wait | 0x00 | 0x7286 | `Process.Wait(...)` |

## Quant

**Conformance Level:** L0  
**Methods:** 5

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| AbsMax | 0x00 | 0x7228 | `Quant.AbsMax(...)` |
| ApplyScale | 0x00 | 0x722B | `Quant.ApplyScale(...)` |
| DequantI8 | 0x00 | 0x722A | `Quant.DequantI8(...)` |
| GroupScale | 0x00 | 0x722C | `Quant.GroupScale(...)` |
| QuantI8 | 0x00 | 0x7229 | `Quant.QuantI8(...)` |

## Query

**Conformance Level:** L0  
**Methods:** 2

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| BuildLookupFilter | 0x00 | 0x71C0 | `Query.BuildLookupFilter(...)` |
| BuildManyToManyMap | 0x00 | 0x71C1 | `Query.BuildManyToManyMap(...)` |

## Queue

**Conformance Level:** L5  
**Methods:** 5

Queue operations: async task enqueue/dequeue, batch operations.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Depth | 0x00 | 0x7012 | `Queue.Depth(...)` |
| Dequeue | 0x00 | 0x7010 | `Queue.Dequeue(...)` |
| DequeueBatch | 0x00 | 0x7013 | `Queue.DequeueBatch(...)` |
| Enqueue | 0x00 | 0x7011 | `Queue.Enqueue(...)` |
| EnqueueBatch | 0x00 | 0x7014 | `Queue.EnqueueBatch(...)` |

## Random

**Conformance Level:** L4  
**Methods:** 1

Cryptographically-seeded randomness from host startup.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| U32 | 0x00 | 0x7020 | `Random.U32(...)` |

## Sampling

**Conformance Level:** L0  
**Methods:** 4

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| ArgMax | 0x00 | 0x7240 | `Sampling.ArgMax(...)` |
| ArgMaxRows | 0x00 | 0x7243 | `Sampling.ArgMaxRows(...)` |
| Temperature | 0x00 | 0x7242 | `Sampling.Temperature(...)` |
| TopK | 0x00 | 0x7241 | `Sampling.TopK(...)` |

## Sandbox

**Conformance Level:** L0  
**Methods:** 1

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Deny | 0x00 | 0x72A6 | `Sandbox.Deny(...)` |

## Scheduler

**Conformance Level:** L0  
**Methods:** 1

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Tick | 0x00 | 0x7294 | `Scheduler.Tick(...)` |

## Search

**Conformance Level:** L0  
**Methods:** 28

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Clear | 0x00 | 0x71D0 | `Search.Clear(...)` |
| ClearFields | 0x00 | 0x7200 | `Search.ClearFields(...)` |
| Compatible | 0x00 | 0x71DC | `Search.Compatible(...)` |
| Configure | 0x00 | 0x71DB | `Search.Configure(...)` |
| Delete | 0x00 | 0x71D2 | `Search.Delete(...)` |
| FacetCount | 0x00 | 0x7203 | `Search.FacetCount(...)` |
| FacetValue | 0x00 | 0x7202 | `Search.FacetValue(...)` |
| Facets | 0x00 | 0x7201 | `Search.Facets(...)` |
| IndexPack | 0x00 | 0x71D3 | `Search.IndexPack(...)` |
| JournalDelete | 0x00 | 0x7208 | `Search.JournalDelete(...)` |
| JournalFacet | 0x00 | 0x7209 | `Search.JournalFacet(...)` |
| JournalNumber | 0x00 | 0x720A | `Search.JournalNumber(...)` |
| JournalReplay | 0x00 | 0x720B | `Search.JournalReplay(...)` |
| JournalUpsert | 0x00 | 0x7207 | `Search.JournalUpsert(...)` |
| Load | 0x00 | 0x7206 | `Search.Load(...)` |
| Plan | 0x00 | 0x71D9 | `Search.Plan(...)` |
| QueryHybrid | 0x00 | 0x71D6 | `Search.QueryHybrid(...)` |
| QueryText | 0x00 | 0x71D4 | `Search.QueryText(...)` |
| Range | 0x00 | 0x7204 | `Search.Range(...)` |
| Rebuild | 0x00 | 0x71DD | `Search.Rebuild(...)` |
| Result | 0x00 | 0x71D7 | `Search.Result(...)` |
| Save | 0x00 | 0x7205 | `Search.Save(...)` |
| Score | 0x00 | 0x71D8 | `Search.Score(...)` |
| SetFacet | 0x00 | 0x71DE | `Search.SetFacet(...)` |
| SetNumber | 0x00 | 0x71DF | `Search.SetNumber(...)` |
| SetSemanticWeight | 0x00 | 0x71DA | `Search.SetSemanticWeight(...)` |
| SetVector | 0x00 | 0x71D5 | `Search.SetVector(...)` |
| UpsertText | 0x00 | 0x71D1 | `Search.UpsertText(...)` |

## Span

**Conformance Level:** L4  
**Methods:** 5

Span descriptor (offset + length) for zero-copy access.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Get | 0x00 | 0x7044 | `Span.Get(...)` |
| Len | 0x00 | 0x7043 | `Span.Len(...)` |
| Make | 0x00 | 0x7040 | `Span.Make(...)` |
| Materialize | 0x00 | 0x7042 | `Span.Materialize(...)` |
| Slice | 0x00 | 0x7041 | `Span.Slice(...)` |

## Storage

**Conformance Level:** L5  
**Methods:** 24

Persistent storage: pack/card schema, CRUD, query.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| AddCard | 0x00 | 0x7062 | `Storage.AddCard(...)` |
| CardLen | 0x00 | 0x71A1 | `Storage.CardLen(...)` |
| DeleteCard | 0x00 | 0x7064 | `Storage.DeleteCard(...)` |
| EditCard | 0x00 | 0x7069 | `Storage.EditCard(...)` |
| GetField | 0x00 | 0x706A | `Storage.GetField(...)` |
| GetFieldStr | 0x00 | 0x706D | `Storage.GetFieldStr(...)` |
| GetSchemaForPack | 0x00 | 0x7060 | `Storage.GetSchemaForPack(...)` |
| IsUserPack | 0x00 | 0x71A4 | `Storage.IsUserPack(...)` |
| Load | 0x01 | - | `Storage.Load(...)` |
| PatchCard | 0x00 | 0x7065 | `Storage.PatchCard(...)` |
| Pipe | 0x03 | - | `Storage.Pipe(...)` |
| QueryCard | 0x00 | 0x7067 | `Storage.QueryCard(...)` |
| QueryResult | 0x00 | 0x706E | `Storage.QueryResult(...)` |
| ReadCard | 0x00 | 0x7066 | `Storage.ReadCard(...)` |
| ReadSlice | 0x00 | 0x71A2 | `Storage.ReadSlice(...)` |
| Ready | 0x00 | 0x706F | `Storage.Ready(...)` |
| Save | 0x02 | - | `Storage.Save(...)` |
| SetField | 0x00 | 0x706B | `Storage.SetField(...)` |
| SetFieldStr | 0x00 | 0x706C | `Storage.SetFieldStr(...)` |
| SetSchemaForPack | 0x00 | 0x7061 | `Storage.SetSchemaForPack(...)` |
| SetSlice | 0x00 | 0x71A0 | `Storage.SetSlice(...)` |
| UpdateCard | 0x00 | 0x7063 | `Storage.UpdateCard(...)` |
| UsePack | 0x00 | 0x7068 | `Storage.UsePack(...)` |
| WriteSlice | 0x00 | 0x71A3 | `Storage.WriteSlice(...)` |

## String

**Conformance Level:** L2  
**Methods:** 13

String manipulation: concat, substring, split, trim, case conversion.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Concat | 0x00 | 0x7080 | `String.Concat(...)` |
| EndsWith | 0x00 | 0x708B | `String.EndsWith(...)` |
| IndexOf | 0x00 | 0x7083 | `String.IndexOf(...)` |
| Join | 0x00 | 0x7089 | `String.Join(...)` |
| Length | 0x00 | 0x7081 | `String.Length(...)` |
| Replace | 0x00 | 0x7084 | `String.Replace(...)` |
| SetReplace | 0x00 | 0x708C | `String.SetReplace(...)` |
| Split | 0x00 | 0x7088 | `String.Split(...)` |
| StartsWith | 0x00 | 0x708A | `String.StartsWith(...)` |
| Substring | 0x00 | 0x7082 | `String.Substring(...)` |
| ToLower | 0x00 | 0x7086 | `String.ToLower(...)` |
| ToUpper | 0x00 | 0x7085 | `String.ToUpper(...)` |
| Trim | 0x00 | 0x7087 | `String.Trim(...)` |

## Template

**Conformance Level:** L0  
**Methods:** 2

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Compile | 0x00 | 0x707A | `Template.Compile(...)` |
| Render | 0x00 | 0x707B | `Template.Render(...)` |

## Tensor

**Conformance Level:** L0  
**Methods:** 12

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| AddI32 | 0x00 | 0x71E3 | `Tensor.AddI32(...)` |
| ArgMaxI32 | 0x00 | 0x71EA | `Tensor.ArgMaxI32(...)` |
| DotI8 | 0x00 | 0x71E1 | `Tensor.DotI8(...)` |
| HasAccel | 0x00 | 0x71EB | `Tensor.HasAccel(...)` |
| MatVecI8 | 0x00 | 0x71E2 | `Tensor.MatVecI8(...)` |
| MulI32 | 0x00 | 0x71E4 | `Tensor.MulI32(...)` |
| ReluI32 | 0x00 | 0x71E6 | `Tensor.ReluI32(...)` |
| RmsNormI32 | 0x00 | 0x71E7 | `Tensor.RmsNormI32(...)` |
| RoPEI32 | 0x00 | 0x71E8 | `Tensor.RoPEI32(...)` |
| ScaleI32 | 0x00 | 0x71E5 | `Tensor.ScaleI32(...)` |
| SetShape | 0x00 | 0x71E0 | `Tensor.SetShape(...)` |
| SoftmaxI32 | 0x00 | 0x71E9 | `Tensor.SoftmaxI32(...)` |

## TextRender

**Conformance Level:** L0  
**Methods:** 9

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Attr | 0x00 | 0x7263 | `TextRender.Attr(...)` |
| Br | 0x00 | 0x7268 | `TextRender.Br(...)` |
| Close | 0x00 | 0x7265 | `TextRender.Close(...)` |
| Empty | 0x00 | 0x7266 | `TextRender.Empty(...)` |
| Hole | 0x00 | 0x7267 | `TextRender.Hole(...)` |
| Open | 0x00 | 0x7262 | `TextRender.Open(...)` |
| OpenEnd | 0x00 | 0x7264 | `TextRender.OpenEnd(...)` |
| Raw | 0x00 | 0x7260 | `TextRender.Raw(...)` |
| Text | 0x00 | 0x7261 | `TextRender.Text(...)` |

## Thread

**Conformance Level:** L5  
**Methods:** 4

Thread preemption hints and cooperative yielding.

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Raise | 0x0E | - | `Thread.Raise(...)` |
| Skip | 0x00 | - | `Thread.Skip(...)` |
| Wait | 0x0D | - | `Thread.Wait(...)` |
| YieldCounted | 0x00 | 0x7070 | `Thread.YieldCounted(...)` |

## Timer

**Conformance Level:** L0  
**Methods:** 4

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| After | 0x00 | 0x7290 | `Timer.After(...)` |
| Cancel | 0x00 | 0x7292 | `Timer.Cancel(...)` |
| Elapsed | 0x00 | 0x7293 | `Timer.Elapsed(...)` |
| Every | 0x00 | 0x7291 | `Timer.Every(...)` |

## Tokenizer

**Conformance Level:** L0  
**Methods:** 7

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Count | 0x00 | 0x7215 | `Tokenizer.Count(...)` |
| DecodeBytes | 0x00 | 0x7213 | `Tokenizer.DecodeBytes(...)` |
| DecodeTrie | 0x00 | 0x7214 | `Tokenizer.DecodeTrie(...)` |
| EncodeBytes | 0x00 | 0x7211 | `Tokenizer.EncodeBytes(...)` |
| EncodeTrie | 0x00 | 0x7212 | `Tokenizer.EncodeTrie(...)` |
| SetVocab | 0x00 | 0x7210 | `Tokenizer.SetVocab(...)` |
| Token | 0x00 | 0x7216 | `Tokenizer.Token(...)` |

## Utf8Reader

**Conformance Level:** L0  
**Methods:** 8

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Eof | 0x00 | 0x702D | `Utf8Reader.Eof(...)` |
| Int | 0x00 | 0x702B | `Utf8Reader.Int(...)` |
| Match | 0x00 | 0x702F | `Utf8Reader.Match(...)` |
| New | 0x00 | 0x7028 | `Utf8Reader.New(...)` |
| Next | 0x00 | 0x702A | `Utf8Reader.Next(...)` |
| Peek | 0x00 | 0x7029 | `Utf8Reader.Peek(...)` |
| Pos | 0x00 | 0x702E | `Utf8Reader.Pos(...)` |
| SkipWs | 0x00 | 0x702C | `Utf8Reader.SkipWs(...)` |

## Utf8Writer

**Conformance Level:** L0  
**Methods:** 7

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| Byte | 0x00 | 0x7022 | `Utf8Writer.Byte(...)` |
| Int | 0x00 | 0x7023 | `Utf8Writer.Int(...)` |
| Len | 0x00 | 0x7026 | `Utf8Writer.Len(...)` |
| New | 0x00 | 0x7021 | `Utf8Writer.New(...)` |
| Reset | 0x00 | 0x7027 | `Utf8Writer.Reset(...)` |
| Span | 0x00 | 0x7024 | `Utf8Writer.Span(...)` |
| ToSpan | 0x00 | 0x7025 | `Utf8Writer.ToSpan(...)` |

## X509

**Conformance Level:** L0  
**Methods:** 8

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| FetchCertificate | 0x00 | 0x7110 | `X509.FetchCertificate(...)` |
| GenerateCSR | 0x00 | 0x7112 | `X509.GenerateCSR(...)` |
| GenerateKeyPair | 0x00 | 0x7113 | `X509.GenerateKeyPair(...)` |
| GetCertInfo | 0x00 | 0x7115 | `X509.GetCertInfo(...)` |
| GetKeyHandle | 0x00 | 0x7117 | `X509.GetKeyHandle(...)` |
| IsCertValid | 0x00 | 0x7116 | `X509.IsCertValid(...)` |
| StoreCertificate | 0x00 | 0x7111 | `X509.StoreCertificate(...)` |
| VerifyCertChain | 0x00 | 0x7114 | `X509.VerifyCertChain(...)` |

## Xml

**Conformance Level:** L0  
**Methods:** 7

| Method | Opcode | Hook Code | v2 Example |
|--------|--------|-----------|----------|
| AttrName | 0x00 | 0x7074 | `Xml.AttrName(...)` |
| AttrValue | 0x00 | 0x7075 | `Xml.AttrValue(...)` |
| Close | 0x00 | 0x7078 | `Xml.Close(...)` |
| Empty | 0x00 | 0x7079 | `Xml.Empty(...)` |
| Open | 0x00 | 0x7073 | `Xml.Open(...)` |
| OpenEnd | 0x00 | 0x7076 | `Xml.OpenEnd(...)` |
| Text | 0x00 | 0x7077 | `Xml.Text(...)` |

---

## Summary by Conformance Level

### L0: 297 methods

- Attention.Attend (0x7253)
- Attention.Mix (0x7252)
- Attention.Scores (0x7251)
- Attention.SetShape (0x7250)
- Auth.GetToken (0x7125)
- Auth.GetUserCredentials (0x7120)
- Auth.GetUserPermissions (0x7123)
- Auth.RefreshToken (0x7128)
- Auth.RequestToken (0x7124)
- Auth.RevokeToken (0x7129)
- Auth.SwitchTokenContext (0x7127)
- Auth.SwitchUserContext (0x7122)
- Auth.ValidateCredentials (0x7121)
- Auth.ValidateToken (0x7126)
- Base64.Decode (0x72D1)
- Base64.Encode (0x72D0)
- Base64.UrlDecode (0x72D2)
- Base64.UrlEncode (0x72D3)
- Binary.ParseCard (0x7341)
- Binary.ParseEntity (0x7343)
- Binary.SerializeCard (0x7342)
- Binary.SerializeEntity (0x7344)
- Binary.SetKey (0x7345)
- Binary.Verify (0x7346)
- BitLinear.HasFormat (0x71F4)
- BitLinear.MatVecBase3 (0x71F3)
- BitLinear.MatVecBase3Block (0x7276)
- BitLinear.MatVecBitmap (0x71F2)
- BitLinear.MatVecBitmapBlock (0x7275)
- BitLinear.MatVecTernary (0x71F1)
- BitLinear.MatVecTernaryBlock (0x7274)
- BitLinear.SetShape (0x71F0)
- Bits.And (0x703A)
- Bits.Not (0x704F)
- Bits.Or (0x703B)
- Bits.Sar (0x703F)
- Bits.Shl (0x703D)
- Bits.Shr (0x703E)
- Bits.Xor (0x703C)
- Capability.Drop (0x72A5)
- Capability.Has (0x72A3)
- Capability.Request (0x72A4)
- Capsule.Call (0x72C0)
- Capsule.Jump (0x72C2)
- Capsule.LoadModule (0x72C3)
- Capsule.RunModule (0x72C4)
- Capsule.Schedule (0x72C1)
- Compress.BrotliCompress (0x7100)
- Compress.BrotliDecompress (0x7101)
- Compress.DeflateCompress (0x7106)
- Compress.DeflateDecompress (0x7107)
- Compress.GzipCompress (0x7104)
- Compress.GzipDecompress (0x7105)
- Compress.PicoCompress (0x7102)
- Compress.PicoDecompress (0x7103)
- Dot8.Len (0x7056)
- Dot8.Of (0x7057)
- Dsp.Concat (core)
- Dsp.Dequant (core)
- Dsp.Dot (core)
- Dsp.Embed (core)
- Dsp.Gelu (core)
- Dsp.Mask (core)
- Dsp.MatMul (core)
- Dsp.Norm (core)
- Dsp.Quant (core)
- Dsp.Relu (core)
- Dsp.Scale (core)
- Dsp.Softmax (core)
- Dsp.Split (core)
- Dsp.TopK (core)
- Dsp.Transpose (core)
- Dsp.VAdd (core)
- Encoding.AsciiDecode (0x7311)
- Encoding.AsciiEncode (0x7310)
- Encoding.HexDecode (0x731B)
- Encoding.HexEncode (0x731A)
- Encoding.Utf16BEDecode (0x7317)
- Encoding.Utf16BEEncode (0x7316)
- Encoding.Utf16LEDecode (0x7315)
- Encoding.Utf16LEEncode (0x7314)
- Encoding.Utf7Decode (0x7319)
- Encoding.Utf7Encode (0x7318)
- Encoding.Utf8Decode (0x7313)
- Encoding.Utf8Encode (0x7312)
- Env.Count (0x728A)
- Env.Get (0x7288)
- Env.Key (0x728B)
- Env.Set (0x7289)
- Error.Clear (0x72B5)
- Error.Code (0x72B2)
- Error.Detail (0x72B3)
- Error.HasHandler (0x72B1)
- Error.Resume (0x72B4)
- Error.SetHandler (0x72B0)
- Html.AddChildNode (0x7141)
- Html.CreateNode (0x7140)
- Html.Decode (0x7147)
- Html.Encode (0x7146)
- Html.GetAttribute (0x7144)
- Html.ParseTree (0x7145)
- Html.QuerySelector (0x7149)
- Html.RemoveChildNode (0x7142)
- Html.Serialize (0x7148)
- Html.SetAttribute (0x7143)
- Http.EncodeJson (0x7137)
- Http.GenerateHeaders (0x7132)
- Http.GenerateResponse (0x7133)
- Http.ParseForm (0x7135)
- Http.ParseJson (0x7136)
- Http.ParseQuery (0x7134)
- Http.ReadBody (0x7131)
- Http.ReadHeader (0x7130)
- Http.Request (0x7138)
- Http.RespBody (0x713B)
- Http.RespHeaders (0x713A)
- Http.RespStatus (0x7139)
- Io.Write (0x7071)
- Io.WriteByte (0x7072)
- Json.BeginArray (0x7047)
- Json.BeginObject (0x7045)
- Json.Bool (0x704C)
- Json.EndArray (0x7048)
- Json.EndObject (0x7046)
- Json.Int (0x704B)
- Json.Key (0x7049)
- Json.Null (0x704D)
- Json.Parse (0x7340)
- Json.Raw (0x704E)
- Json.Str (0x704A)
- Kv.Clear (0x7236)
- Kv.Len (0x7235)
- Kv.ReadK (0x7233)
- Kv.ReadKH (0x723A)
- Kv.ReadV (0x7234)
- Kv.ReadVH (0x723B)
- Kv.SetHead (0x7237)
- Kv.SetShape (0x7230)
- Kv.WriteK (0x7231)
- Kv.WriteKH (0x7238)
- Kv.WriteV (0x7232)
- Kv.WriteVH (0x7239)
- Map.Clear (0x7322)
- Map.Count (0x7323)
- Map.DelI (0x7328)
- Map.DelS (0x7330)
- Map.Free (0x7321)
- Map.GetII (0x7326)
- Map.GetIS (0x732A)
- Map.GetSI (0x732E)
- Map.GetSS (0x7332)
- Map.HasI (0x7327)
- Map.HasS (0x732F)
- Map.Hash (0x7324)
- Map.IsNullI (0x732C)
- Map.IsNullS (0x7334)
- Map.KeyAt (0x7335)
- Map.KeySpanAt (0x7336)
- Map.New (0x7320)
- Map.PutII (0x7325)
- Map.PutIS (0x7329)
- Map.PutNullI (0x732B)
- Map.PutNullS (0x7333)
- Map.PutSI (0x732D)
- Map.PutSS (0x7331)
- Map.Use (0x733A)
- Map.ValAt (0x7337)
- Map.ValIsSpan (0x7339)
- Map.ValSpanAt (0x7338)
- Model.GetConfig (0x7221)
- Model.MatVecI8Block (0x7273)
- Model.ReadTensor (0x7227)
- Model.ReadTensorBlock (0x7272)
- Model.ReadTensorRow (0x7270)
- Model.SetBlock (0x7271)
- Model.SetConfig (0x7220)
- Model.TensorCols (0x7225)
- Model.TensorFormat (0x7226)
- Model.TensorOffset (0x7223)
- Model.TensorRows (0x7224)
- Model.TensorView (0x7222)
- Principal.Claims (0x72A2)
- Principal.Current (0x72A0)
- Principal.HasRole (0x72A1)
- Process.Args (0x7287)
- Process.Exit (0x7283)
- Process.Kill (0x7284)
- Process.Parent (0x7281)
- Process.Self (0x7280)
- Process.Spawn (0x7282)
- Process.Status (0x7285)
- Process.Wait (0x7286)
- Quant.AbsMax (0x7228)
- Quant.ApplyScale (0x722B)
- Quant.DequantI8 (0x722A)
- Quant.GroupScale (0x722C)
- Quant.QuantI8 (0x7229)
- Query.BuildLookupFilter (0x71C0)
- Query.BuildManyToManyMap (0x71C1)
- Sampling.ArgMax (0x7240)
- Sampling.ArgMaxRows (0x7243)
- Sampling.Temperature (0x7242)
- Sampling.TopK (0x7241)
- Sandbox.Deny (0x72A6)
- Scheduler.Tick (0x7294)
- Search.Clear (0x71D0)
- Search.ClearFields (0x7200)
- Search.Compatible (0x71DC)
- Search.Configure (0x71DB)
- Search.Delete (0x71D2)
- Search.FacetCount (0x7203)
- Search.FacetValue (0x7202)
- Search.Facets (0x7201)
- Search.IndexPack (0x71D3)
- Search.JournalDelete (0x7208)
- Search.JournalFacet (0x7209)
- Search.JournalNumber (0x720A)
- Search.JournalReplay (0x720B)
- Search.JournalUpsert (0x7207)
- Search.Load (0x7206)
- Search.Plan (0x71D9)
- Search.QueryHybrid (0x71D6)
- Search.QueryText (0x71D4)
- Search.Range (0x7204)
- Search.Rebuild (0x71DD)
- Search.Result (0x71D7)
- Search.Save (0x7205)
- Search.Score (0x71D8)
- Search.SetFacet (0x71DE)
- Search.SetNumber (0x71DF)
- Search.SetSemanticWeight (0x71DA)
- Search.SetVector (0x71D5)
- Search.UpsertText (0x71D1)
- Template.Compile (0x707A)
- Template.Render (0x707B)
- Tensor.AddI32 (0x71E3)
- Tensor.ArgMaxI32 (0x71EA)
- Tensor.DotI8 (0x71E1)
- Tensor.HasAccel (0x71EB)
- Tensor.MatVecI8 (0x71E2)
- Tensor.MulI32 (0x71E4)
- Tensor.ReluI32 (0x71E6)
- Tensor.RmsNormI32 (0x71E7)
- Tensor.RoPEI32 (0x71E8)
- Tensor.ScaleI32 (0x71E5)
- Tensor.SetShape (0x71E0)
- Tensor.SoftmaxI32 (0x71E9)
- TextRender.Attr (0x7263)
- TextRender.Br (0x7268)
- TextRender.Close (0x7265)
- TextRender.Empty (0x7266)
- TextRender.Hole (0x7267)
- TextRender.Open (0x7262)
- TextRender.OpenEnd (0x7264)
- TextRender.Raw (0x7260)
- TextRender.Text (0x7261)
- Timer.After (0x7290)
- Timer.Cancel (0x7292)
- Timer.Elapsed (0x7293)
- Timer.Every (0x7291)
- Tokenizer.Count (0x7215)
- Tokenizer.DecodeBytes (0x7213)
- Tokenizer.DecodeTrie (0x7214)
- Tokenizer.EncodeBytes (0x7211)
- Tokenizer.EncodeTrie (0x7212)
- Tokenizer.SetVocab (0x7210)
- Tokenizer.Token (0x7216)
- Utf8Reader.Eof (0x702D)
- Utf8Reader.Int (0x702B)
- Utf8Reader.Match (0x702F)
- Utf8Reader.New (0x7028)
- Utf8Reader.Next (0x702A)
- Utf8Reader.Peek (0x7029)
- Utf8Reader.Pos (0x702E)
- Utf8Reader.SkipWs (0x702C)
- Utf8Writer.Byte (0x7022)
- Utf8Writer.Int (0x7023)
- Utf8Writer.Len (0x7026)
- Utf8Writer.New (0x7021)
- Utf8Writer.Reset (0x7027)
- Utf8Writer.Span (0x7024)
- Utf8Writer.ToSpan (0x7025)
- X509.FetchCertificate (0x7110)
- X509.GenerateCSR (0x7112)
- X509.GenerateKeyPair (0x7113)
- X509.GetCertInfo (0x7115)
- X509.GetKeyHandle (0x7117)
- X509.IsCertValid (0x7116)
- X509.StoreCertificate (0x7111)
- X509.VerifyCertChain (0x7114)
- Xml.AttrName (0x7074)
- Xml.AttrValue (0x7075)
- Xml.Close (0x7078)
- Xml.Empty (0x7079)
- Xml.Open (0x7073)
- Xml.OpenEnd (0x7076)
- Xml.Text (0x7077)

### L1: 21 methods

- Flow.Branch (core)
- Flow.Call (core)
- Flow.Jump (core)
- Flow.Return (core)
- Math.Add (core)
- Math.Div (core)
- Math.Inc (core)
- Math.Mul (core)
- Math.Sub (core)
- Net.Accept (0x72E1)
- Net.Body (core)
- Net.Close (core)
- Net.Header (core)
- Net.Listen (0x72E0)
- Net.PoolSize (0x72E5)
- Net.Read (0x72E2)
- Net.Register (0x72E6)
- Net.Shutdown (0x72E4)
- Net.Status (core)
- Net.Type (core)
- Net.Write (0x72E3)

### L2: 58 methods

- DateTime.AddDays (0x70B7)
- DateTime.AddHours (0x70B6)
- DateTime.AddMinutes (0x70B5)
- DateTime.AddSeconds (0x70B4)
- DateTime.Day (0x70BE)
- DateTime.DiffDays (0x70BB)
- DateTime.Format (0x70B3)
- DateTime.GetDayOfWeek (0x70B8)
- DateTime.GetDayOfYear (0x70B9)
- DateTime.Month (0x70BD)
- DateTime.Now (0x70B0)
- DateTime.Parse (0x70B2)
- DateTime.UnixTimestamp (0x70BA)
- DateTime.UtcNow (0x70B1)
- DateTime.Year (0x70BC)
- Locale.FormatCurrency (0x70C2)
- Locale.FormatDate (0x70C4)
- Locale.FormatNumber (0x70C3)
- Locale.FormatTime (0x70C5)
- Locale.GetCurrentLocale (0x70C0)
- Locale.SetLocale (0x70C1)
- Locale.Translate (0x70C6)
- Maths.Clamp (0x70AA)
- Maths.Cos (0x70A1)
- Maths.Exp (0x70A7)
- Maths.Lerp (0x70AB)
- Maths.Log (0x70A5)
- Maths.Log10 (0x70A6)
- Maths.Power (0x70A4)
- Maths.Random (0x70A8)
- Maths.RandomRange (0x70A9)
- Maths.Sin (0x70A0)
- Maths.Sqrt (0x70A3)
- Maths.Tan (0x70A2)
- Number.Abs (0x7095)
- Number.Ceiling (0x7097)
- Number.Floor (0x7096)
- Number.Max (0x709A)
- Number.Min (0x7099)
- Number.Parse (0x7090)
- Number.Round (0x7098)
- Number.ToBinary (0x7094)
- Number.ToHex (0x7092)
- Number.ToOctal (0x7093)
- Number.ToString (0x7091)
- String.Concat (0x7080)
- String.EndsWith (0x708B)
- String.IndexOf (0x7083)
- String.Join (0x7089)
- String.Length (0x7081)
- String.Replace (0x7084)
- String.SetReplace (0x708C)
- String.Split (0x7088)
- String.StartsWith (0x708A)
- String.Substring (0x7082)
- String.ToLower (0x7086)
- String.ToUpper (0x7085)
- String.Trim (0x7087)

### L3: 24 methods

- Context.GetBody (0x70E9)
- Context.GetClientCert (0x70ED)
- Context.GetHeaders (0x70E7)
- Context.GetHost (0x70E2)
- Context.GetPath (0x70E1)
- Context.GetPermissions (0x70E6)
- Context.GetPort (0x70E3)
- Context.GetQueryString (0x70E8)
- Context.GetRemoteAddr (0x70E4)
- Context.GetRequestId (0x70EC)
- Context.GetScratchValue (0x70EB)
- Context.GetTraceId (0x70EE)
- Context.GetUser (0x70E5)
- Context.GetVerb (0x70E0)
- Context.SetScratchValue (0x70EA)
- Environment.GetCpuCount (0x70D1)
- Environment.GetElapsedTime (0x70D8)
- Environment.GetHostname (0x70D4)
- Environment.GetMemoryFree (0x70D3)
- Environment.GetMemoryTotal (0x70D2)
- Environment.GetOsVersion (0x70D0)
- Environment.GetProcessId (0x70D6)
- Environment.GetThreadId (0x70D7)
- Environment.GetTimeZone (0x70D5)

### L4: 26 methods

- Descriptor.CopyBatch (0x7055)
- Descriptor.GetFlags (0x7054)
- Descriptor.GetLen (0x7053)
- Descriptor.GetPtr (0x7052)
- Descriptor.Make (0x7050)
- Descriptor.SetFlags (0x7051)
- Lease.Acquire (0x7058)
- Lease.CachedValidate (0x705B)
- Lease.GetSpan (0x705C)
- Lease.GetTypeHint (0x705D)
- Lease.Release (0x7059)
- Lease.Validate (0x705A)
- Memory.ArenaAlloc (0x7031)
- Memory.ArenaInit (0x7030)
- Memory.ArenaReset (0x7032)
- Memory.ArenaStats (0x7033)
- Memory.Get (0x7037)
- Memory.Peek (0x7034)
- Memory.Poke (0x7035)
- Memory.Set (0x7036)
- Random.U32 (0x7020)
- Span.Get (0x7044)
- Span.Len (0x7043)
- Span.Make (0x7040)
- Span.Materialize (0x7042)
- Span.Slice (0x7041)

### L5: 33 methods

- Queue.Depth (0x7012)
- Queue.Dequeue (0x7010)
- Queue.DequeueBatch (0x7013)
- Queue.Enqueue (0x7011)
- Queue.EnqueueBatch (0x7014)
- Storage.AddCard (0x7062)
- Storage.CardLen (0x71A1)
- Storage.DeleteCard (0x7064)
- Storage.EditCard (0x7069)
- Storage.GetField (0x706A)
- Storage.GetFieldStr (0x706D)
- Storage.GetSchemaForPack (0x7060)
- Storage.IsUserPack (0x71A4)
- Storage.Load (core)
- Storage.PatchCard (0x7065)
- Storage.Pipe (core)
- Storage.QueryCard (0x7067)
- Storage.QueryResult (0x706E)
- Storage.ReadCard (0x7066)
- Storage.ReadSlice (0x71A2)
- Storage.Ready (0x706F)
- Storage.Save (core)
- Storage.SetField (0x706B)
- Storage.SetFieldStr (0x706C)
- Storage.SetSchemaForPack (0x7061)
- Storage.SetSlice (0x71A0)
- Storage.UpdateCard (0x7063)
- Storage.UsePack (0x7068)
- Storage.WriteSlice (0x71A3)
- Thread.Raise (core)
- Thread.Skip (core)
- Thread.Wait (core)
- Thread.YieldCounted (0x7070)

### L6: 21 methods

- Crypto.Blake2b (0x70F2)
- Crypto.Blake3 (0x70F3)
- Crypto.Decrypt (0x70F9)
- Crypto.DeriveKey (0x70FB)
- Crypto.Encrypt (0x70F8)
- Crypto.GenerateKeyPair (0x70FA)
- Crypto.HmacSha256 (0x70F4)
- Crypto.HmacSha512 (0x70F5)
- Crypto.Md5 (0x70FD)
- Crypto.RandomBytes (0x70FC)
- Crypto.Sha1 (0x70FE)
- Crypto.Sha256 (0x70F0)
- Crypto.Sha512 (0x70F1)
- Crypto.Sign (0x70F6)
- Crypto.Verify (0x70F7)
- Kernel.FireSWIRQ (0x7003)
- Kernel.ProfileEnd (0x7005)
- Kernel.ProfileStart (0x7004)
- Kernel.TracePoint (0x7006)
- Kernel.WaitIRQ (0x7001)
- Kernel.WaitSWIRQ (0x7002)

---

## Hook Code Allocation

Host hooks use reserved imm16 range 0x7000-0x7FFF:

| Range | Namespace | Count | Purpose |
|-------|-----------|-------|---------|
| 0x7001-0x7006 | Kernel | 6 | Process, IPC |
| 0x7010-0x7014 | Queue | 5 | Task queue |
| 0x7020 | Random | 1 | RNG |
| 0x7030-0x7033 | Memory | 4 | Arena |
| 0x7040-0x7041 | Span | 2 | Spans |
| 0x7050-0x7055 | Descriptor | 6 | Descriptors |
| 0x7058-0x705D | Lease | 6 | Leases |
| 0x7060-0x7067 | Storage | 8 | Cards/packs |
| 0x7070 | Thread | 1 | Preemption |
| 0x7080-0x708B | String | 12 | Strings |
| 0x7090-0x709A | Number | 11 | Numbers |
| 0x70A0-0x70AB | Maths | 12 | Math |
| 0x70B0-0x70BA | DateTime | 11 | Date/time |
| 0x70C0-0x70C6 | Locale | 7 | Locale |
| 0x70D0-0x70D8 | Environment | 9 | Environment |
| 0x70E0-0x70EE | Context | 15 | Context |
| 0x70F0-0x70FE | Crypto | 15 | Crypto |

**Total:** 480 methods across 58 namespaces.

## IDE Code Completion

### When User Types Namespace Dot:

```
String.<COMPLETIONS>
  .Concat(s1, s2) -> string
  .Length(s) -> int
  .Substring(s, start, len) -> string
  .IndexOf(s, substr) -> int
  .Split(s, delim) -> array
  ...
```

### Syntax Highlighting

```
KEYWORDS:        IF THEN ELSE ENDIF WHILE ENDWHILE FOREACH AS IN 
                 ENDFOREACH SWITCH CASE ENDSWITCH LET RETURN
NAMESPACES:      String Number Maths DateTime Locale Environment 
                 Context Crypto Kernel Queue Memory Span Descriptor Lease Storage
METHODS:         .MethodName(...) via dot notation
IDENTIFIERS:     Case-insensitive (all normalized to lowercase)
LITERALS:        "string" 123 3.14 true false
OPERATORS:       = + - * / % < > <= >= == != AND OR NOT
COMMENTS:        // rest of line
```

### Performance Annotations

Editors may color-code methods:

- **Green (FAST)**: O(1) userland - String.Length, Number.Parse
- **Yellow (LAZY)**: Cached on first call - Context.GetHeaders
- **Red (FIFO)**: Kernel IPC needed - Crypto.Sign, Kernel operations

## v2 Syntax Examples

### String Operations

```
LET s1 = "Hello"
LET s2 = "World"
LET combined = String.Concat(s1, " ", s2)
LET len = String.Length(combined)
IF len > 10 THEN
  LET upper = String.ToUpper(combined)
ENDIF
```

### Context Access (Lazy-Decoded)

```
LET user = Context.GetUser()        -- Expensive on first call
LET verb = Context.GetVerb()        -- Fast: pre-cached
LET headers = Context.GetHeaders()  -- Expensive on first call
```

### Control Flow

```
FOREACH item AS x IN items THEN
  IF String.Length(x) > 0 THEN
    Queue.Enqueue(x)
  ENDIF
ENDFOREACH
```

### Memory/Lease

```
LET lease = Memory.ArenaAlloc(1024)
LET handle = Lease.Acquire(lease)
Lease.Validate(handle)
Lease.Release(handle)
```

### Cryptography

```
-- Userland key (fast, no kernel mediation)
LET key = "my-session-token"
LET sig = Crypto.HmacSha256(key, "data")

-- System key (kernel FIFO, audit-logged)
LET sys_sig = Crypto.Sign(system_handle, "data")
```

---

Generated from `picoscript_lang.py` (v0.3)
