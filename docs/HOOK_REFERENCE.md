# PicoScript Hook Reference (533 hooks, 70 namespaces)

Complete reference for all host hooks in the PicoScript 16-opcode ISA.
Each hook is a deterministic primitive callable from any of the 7 language surfaces.

## Summary

| Metric | Value |
|--------|-------|
| Total hooks | 533 |
| Namespaces | 70 |
| Language surfaces | 7 (C, BASIC, Python, English, COBOL, Report, Functional) |
| Execution paths | 5 (Python VM, JS VM, C VM, native C, native JS) |

---

## Core ISA

### Thread.* (1 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Thread.YieldCounted() | 0x0070 | |

### Net.* (7 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Net.Listen() | 0x02E0 | |
| Net.Accept() | 0x02E1 | |
| Net.Read() | 0x02E2 | |
| Net.Write() | 0x02E3 | |
| Net.Shutdown() | 0x02E4 | |
| Net.PoolSize() | 0x02E5 | |
| Net.Register() | 0x02E6 | |

---

## Memory & Spans

### Memory.* (9 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Memory.ArenaInit() | 0x0030 | |
| Memory.ArenaAlloc() | 0x0031 | |
| Memory.ArenaReset() | 0x0032 | |
| Memory.ArenaStats() | 0x0033 | |
| Memory.Peek() | 0x0034 | |
| Memory.Poke() | 0x0035 | |
| Memory.Set() | 0x0036 | |
| Memory.Get() | 0x0037 | |
| Memory.SetConst() | 0x005F | |

### Span.* (5 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Span.Make() | 0x0040 | |
| Span.Slice() | 0x0041 | |
| Span.Materialize() | 0x0042 | |
| Span.Len() | 0x0043 | |
| Span.Get() | 0x0044 | |

### Descriptor.* (6 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Descriptor.Make() | 0x0050 | |
| Descriptor.SetFlags() | 0x0051 | |
| Descriptor.GetPtr() | 0x0052 | |
| Descriptor.GetLen() | 0x0053 | |
| Descriptor.GetFlags() | 0x0054 | |
| Descriptor.CopyBatch() | 0x0055 | |

### Arena.* (3 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Arena.Mark() | 0x007C | |
| Arena.Rewind() | 0x007D | |
| Arena.Reset() | 0x007E | |

### Lease.* (6 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Lease.Acquire() | 0x0058 | |
| Lease.Release() | 0x0059 | |
| Lease.Validate() | 0x005A | |
| Lease.CachedValidate() | 0x005B | |
| Lease.GetSpan() | 0x005C | |
| Lease.GetTypeHint() | 0x005D | |

### Dot8.* (2 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Dot8.Len() | 0x0056 | |
| Dot8.Of() | 0x0057 | |

---

## I/O & Text

### Io.* (2 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Io.Write() | 0x0071 | |
| Io.WriteByte() | 0x0072 | |

### Utf8Writer.* (7 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Utf8Writer.New() | 0x0021 | |
| Utf8Writer.Byte() | 0x0022 | |
| Utf8Writer.Int() | 0x0023 | |
| Utf8Writer.Span() | 0x0024 | |
| Utf8Writer.ToSpan() | 0x0025 | |
| Utf8Writer.Len() | 0x0026 | |
| Utf8Writer.Reset() | 0x0027 | |

### Utf8Reader.* (8 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Utf8Reader.New() | 0x0028 | |
| Utf8Reader.Peek() | 0x0029 | |
| Utf8Reader.Next() | 0x002A | |
| Utf8Reader.Int() | 0x002B | |
| Utf8Reader.SkipWs() | 0x002C | |
| Utf8Reader.Eof() | 0x002D | |
| Utf8Reader.Pos() | 0x002E | |
| Utf8Reader.Match() | 0x002F | |

### Json.* (10 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Json.BeginObject() | 0x0045 | |
| Json.EndObject() | 0x0046 | |
| Json.BeginArray() | 0x0047 | |
| Json.EndArray() | 0x0048 | |
| Json.Key() | 0x0049 | |
| Json.Str() | 0x004A | |
| Json.Int() | 0x004B | |
| Json.Bool() | 0x004C | |
| Json.Null() | 0x004D | |
| Json.Raw() | 0x004E | |

### Xml.* (7 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Xml.Open() | 0x0073 | |
| Xml.AttrName() | 0x0074 | |
| Xml.AttrValue() | 0x0075 | |
| Xml.OpenEnd() | 0x0076 | |
| Xml.Text() | 0x0077 | |
| Xml.Close() | 0x0078 | |
| Xml.Empty() | 0x0079 | |

### TextRender.* (9 hooks)

| Method | Code | Description |
|--------|------|-------------|
| TextRender.Raw() | 0x0260 | |
| TextRender.Text() | 0x0261 | |
| TextRender.Open() | 0x0262 | |
| TextRender.Attr() | 0x0263 | |
| TextRender.OpenEnd() | 0x0264 | |
| TextRender.Close() | 0x0265 | |
| TextRender.Empty() | 0x0266 | |
| TextRender.Hole() | 0x0267 | |
| TextRender.Br() | 0x0268 | |

### Template.* (2 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Template.Compile() | 0x007A | |
| Template.Render() | 0x007B | |

---

## Storage & Query

### Storage.* (21 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Storage.GetSchemaForPack() | 0x0060 | |
| Storage.SetSchemaForPack() | 0x0061 | |
| Storage.AddCard() | 0x0062 | |
| Storage.UpdateCard() | 0x0063 | |
| Storage.DeleteCard() | 0x0064 | |
| Storage.PatchCard() | 0x0065 | |
| Storage.ReadCard() | 0x0066 | |
| Storage.QueryCard() | 0x0067 | |
| Storage.UsePack() | 0x0068 | |
| Storage.EditCard() | 0x0069 | |
| Storage.GetField() | 0x006A | |
| Storage.SetField() | 0x006B | |
| Storage.SetFieldStr() | 0x006C | |
| Storage.GetFieldStr() | 0x006D | |
| Storage.QueryResult() | 0x006E | |
| Storage.Ready() | 0x006F | |
| Storage.SetSlice() | 0x01A0 | |
| Storage.CardLen() | 0x01A1 | |
| Storage.ReadSlice() | 0x01A2 | |
| Storage.WriteSlice() | 0x01A3 | |
| Storage.IsUserPack() | 0x01A4 | |

### Query.* (2 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Query.BuildLookupFilter() | 0x01C0 | |
| Query.BuildManyToManyMap() | 0x01C1 | |

### Search.* (28 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Search.Clear() | 0x01D0 | |
| Search.UpsertText() | 0x01D1 | |
| Search.Delete() | 0x01D2 | |
| Search.IndexPack() | 0x01D3 | |
| Search.QueryText() | 0x01D4 | |
| Search.SetVector() | 0x01D5 | |
| Search.QueryHybrid() | 0x01D6 | |
| Search.Result() | 0x01D7 | |
| Search.Score() | 0x01D8 | |
| Search.Plan() | 0x01D9 | |
| Search.SetSemanticWeight() | 0x01DA | |
| Search.Configure() | 0x01DB | |
| Search.Compatible() | 0x01DC | |
| Search.Rebuild() | 0x01DD | |
| Search.SetFacet() | 0x01DE | |
| Search.SetNumber() | 0x01DF | |
| Search.ClearFields() | 0x0200 | |
| Search.Facets() | 0x0201 | |
| Search.FacetValue() | 0x0202 | |
| Search.FacetCount() | 0x0203 | |
| Search.Range() | 0x0204 | |
| Search.Save() | 0x0205 | |
| Search.Load() | 0x0206 | |
| Search.JournalUpsert() | 0x0207 | |
| Search.JournalDelete() | 0x0208 | |
| Search.JournalFacet() | 0x0209 | |
| Search.JournalNumber() | 0x020A | |
| Search.JournalReplay() | 0x020B | |

---

## HTTP & Request

### Req.* (13 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Req.Seq() | 0x0007 | |
| Req.Principal() | 0x0008 | |
| Req.Method() | 0x0009 | |
| Req.Path() | 0x000A | |
| Req.Header() | 0x000B | |
| Req.BodyMode() | 0x000C | |
| Req.BodyCount() | 0x000D | |
| Req.BodySpan() | 0x000E | |
| Req.SetSlice() | 0x01B0 | |
| Req.BodySlice() | 0x01B1 | |
| Req.BodyLen() | 0x01B2 | |
| Req.Param() | 0x01B6 | |
| Req.ParamCount() | 0x01B7 | |

### Resp.* (13 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Resp.Status() | 0x0015 | |
| Resp.Header() | 0x0016 | |
| Resp.Write() | 0x0017 | |
| Resp.Trailer() | 0x0018 | |
| Resp.Seal() | 0x0019 | |
| Resp.End() | 0x001A | |
| Resp.Respond() | 0x001B | |
| Resp.Flush() | 0x001C | |
| Resp.Continue() | 0x001D | |
| Resp.EndStream() | 0x001E | |
| Resp.Upgrade() | 0x001F | |
| Resp.Abort() | 0x0038 | |
| Resp.EarlyHints() | 0x0039 | |

### Http.* (8 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Http.ReadHeader() | 0x0130 | |
| Http.ReadBody() | 0x0131 | |
| Http.GenerateHeaders() | 0x0132 | |
| Http.GenerateResponse() | 0x0133 | |
| Http.ParseQuery() | 0x0134 | |
| Http.ParseForm() | 0x0135 | |
| Http.ParseJson() | 0x0136 | |
| Http.EncodeJson() | 0x0137 | |

### Html.* (10 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Html.CreateNode() | 0x0140 | |
| Html.AddChildNode() | 0x0141 | |
| Html.RemoveChildNode() | 0x0142 | |
| Html.SetAttribute() | 0x0143 | |
| Html.GetAttribute() | 0x0144 | |
| Html.ParseTree() | 0x0145 | |
| Html.Encode() | 0x0146 | |
| Html.Decode() | 0x0147 | |
| Html.Serialize() | 0x0148 | |
| Html.QuerySelector() | 0x0149 | |

### Context.* (15 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Context.GetVerb() | 0x00E0 | |
| Context.GetPath() | 0x00E1 | |
| Context.GetHost() | 0x00E2 | |
| Context.GetPort() | 0x00E3 | |
| Context.GetRemoteAddr() | 0x00E4 | |
| Context.GetUser() | 0x00E5 | |
| Context.GetPermissions() | 0x00E6 | |
| Context.GetHeaders() | 0x00E7 | |
| Context.GetQueryString() | 0x00E8 | |
| Context.GetBody() | 0x00E9 | |
| Context.SetScratchValue() | 0x00EA | |
| Context.GetScratchValue() | 0x00EB | |
| Context.GetRequestId() | 0x00EC | |
| Context.GetClientCert() | 0x00ED | |
| Context.GetTraceId() | 0x00EE | |

---

## Strings & Numbers

### String.* (13 hooks)

| Method | Code | Description |
|--------|------|-------------|
| String.Concat() | 0x0080 | |
| String.Length() | 0x0081 | |
| String.Substring() | 0x0082 | |
| String.IndexOf() | 0x0083 | |
| String.Replace() | 0x0084 | |
| String.ToUpper() | 0x0085 | |
| String.ToLower() | 0x0086 | |
| String.Trim() | 0x0087 | |
| String.Split() | 0x0088 | |
| String.Join() | 0x0089 | |
| String.StartsWith() | 0x008A | |
| String.EndsWith() | 0x008B | |
| String.SetReplace() | 0x008C | |

### Number.* (11 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Number.Parse() | 0x0090 | |
| Number.ToString() | 0x0091 | |
| Number.ToHex() | 0x0092 | |
| Number.ToOctal() | 0x0093 | |
| Number.ToBinary() | 0x0094 | |
| Number.Abs() | 0x0095 | |
| Number.Floor() | 0x0096 | |
| Number.Ceiling() | 0x0097 | |
| Number.Round() | 0x0098 | |
| Number.Min() | 0x0099 | |
| Number.Max() | 0x009A | |

### Maths.* (12 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Maths.Sin() | 0x00A0 | |
| Maths.Cos() | 0x00A1 | |
| Maths.Tan() | 0x00A2 | |
| Maths.Sqrt() | 0x00A3 | |
| Maths.Power() | 0x00A4 | |
| Maths.Log() | 0x00A5 | |
| Maths.Log10() | 0x00A6 | |
| Maths.Exp() | 0x00A7 | |
| Maths.Random() | 0x00A8 | |
| Maths.RandomRange() | 0x00A9 | |
| Maths.Clamp() | 0x00AA | |
| Maths.Lerp() | 0x00AB | |

### DateTime.* (15 hooks)

| Method | Code | Description |
|--------|------|-------------|
| DateTime.Now() | 0x00B0 | |
| DateTime.UtcNow() | 0x00B1 | |
| DateTime.Parse() | 0x00B2 | |
| DateTime.Format() | 0x00B3 | |
| DateTime.AddSeconds() | 0x00B4 | |
| DateTime.AddMinutes() | 0x00B5 | |
| DateTime.AddHours() | 0x00B6 | |
| DateTime.AddDays() | 0x00B7 | |
| DateTime.GetDayOfWeek() | 0x00B8 | |
| DateTime.GetDayOfYear() | 0x00B9 | |
| DateTime.UnixTimestamp() | 0x00BA | |
| DateTime.DiffDays() | 0x00BB | |
| DateTime.Year() | 0x00BC | |
| DateTime.Month() | 0x00BD | |
| DateTime.Day() | 0x00BE | |

### Locale.* (7 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Locale.GetCurrentLocale() | 0x00C0 | |
| Locale.SetLocale() | 0x00C1 | |
| Locale.FormatCurrency() | 0x00C2 | |
| Locale.FormatNumber() | 0x00C3 | |
| Locale.FormatDate() | 0x00C4 | |
| Locale.FormatTime() | 0x00C5 | |
| Locale.Translate() | 0x00C6 | |

### Base64.* (3 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Base64.Encode() | 0x02D0 | |
| Base64.Decode() | 0x02D1 | |
| Base64.UrlDecode() | 0x02D2 | |

---

## Security

### Crypto.* (15 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Crypto.Sha256() | 0x00F0 | |
| Crypto.Sha512() | 0x00F1 | |
| Crypto.Blake2b() | 0x00F2 | |
| Crypto.Blake3() | 0x00F3 | |
| Crypto.HmacSha256() | 0x00F4 | |
| Crypto.HmacSha512() | 0x00F5 | |
| Crypto.Sign() | 0x00F6 | |
| Crypto.Verify() | 0x00F7 | |
| Crypto.Encrypt() | 0x00F8 | |
| Crypto.Decrypt() | 0x00F9 | |
| Crypto.GenerateKeyPair() | 0x00FA | |
| Crypto.DeriveKey() | 0x00FB | |
| Crypto.RandomBytes() | 0x00FC | |
| Crypto.Md5() | 0x00FD | |
| Crypto.Sha1() | 0x00FE | |

### X509.* (8 hooks)

| Method | Code | Description |
|--------|------|-------------|
| X509.FetchCertificate() | 0x0110 | |
| X509.StoreCertificate() | 0x0111 | |
| X509.GenerateCSR() | 0x0112 | |
| X509.GenerateKeyPair() | 0x0113 | |
| X509.VerifyCertChain() | 0x0114 | |
| X509.GetCertInfo() | 0x0115 | |
| X509.IsCertValid() | 0x0116 | |
| X509.GetKeyHandle() | 0x0117 | |

### Auth.* (10 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Auth.GetUserCredentials() | 0x0120 | |
| Auth.ValidateCredentials() | 0x0121 | |
| Auth.SwitchUserContext() | 0x0122 | |
| Auth.GetUserPermissions() | 0x0123 | |
| Auth.RequestToken() | 0x0124 | |
| Auth.GetToken() | 0x0125 | |
| Auth.ValidateToken() | 0x0126 | |
| Auth.SwitchTokenContext() | 0x0127 | |
| Auth.RefreshToken() | 0x0128 | |
| Auth.RevokeToken() | 0x0129 | |

### Principal.* (3 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Principal.Current() | 0x02A0 | |
| Principal.HasRole() | 0x02A1 | |
| Principal.Claims() | 0x02A2 | |

### Capability.* (3 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Capability.Has() | 0x02A3 | |
| Capability.Request() | 0x02A4 | |
| Capability.Drop() | 0x02A5 | |

### Sandbox.* (1 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Sandbox.Deny() | 0x02A6 | |

---

## Compression

### Compress.* (8 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Compress.BrotliCompress() | 0x0100 | |
| Compress.BrotliDecompress() | 0x0101 | |
| Compress.PicoCompress() | 0x0102 | |
| Compress.PicoDecompress() | 0x0103 | |
| Compress.GzipCompress() | 0x0104 | |
| Compress.GzipDecompress() | 0x0105 | |
| Compress.DeflateCompress() | 0x0106 | |
| Compress.DeflateDecompress() | 0x0107 | |

---

## Hardware & Devices

### Kernel.* (6 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Kernel.WaitIRQ() | 0x0001 | |
| Kernel.WaitSWIRQ() | 0x0002 | |
| Kernel.FireSWIRQ() | 0x0003 | |
| Kernel.ProfileStart() | 0x0004 | |
| Kernel.ProfileEnd() | 0x0005 | |
| Kernel.TracePoint() | 0x0006 | |

### Queue.* (5 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Queue.Dequeue() | 0x0010 | |
| Queue.Enqueue() | 0x0011 | |
| Queue.Depth() | 0x0012 | |
| Queue.DequeueBatch() | 0x0013 | |
| Queue.EnqueueBatch() | 0x0014 | |

### Random.* (1 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Random.U32() | 0x0020 | |

### Gpio.* (7 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Gpio.Count() | 0x0150 | |
| Gpio.SetDir() | 0x0151 | |
| Gpio.GetDir() | 0x0152 | |
| Gpio.SetPull() | 0x0153 | |
| Gpio.GetPull() | 0x0154 | |
| Gpio.Write() | 0x0155 | |
| Gpio.Read() | 0x0156 | |

### Device.* (4 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Device.Open() | 0x0168 | |
| Device.Caps() | 0x0169 | |
| Device.Close() | 0x016A | |
| Device.Status() | 0x016B | |

### Stream.* (8 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Stream.Open() | 0x0170 | |
| Stream.Next() | 0x0171 | |
| Stream.Span() | 0x0172 | |
| Stream.Submit() | 0x0173 | |
| Stream.Release() | 0x0174 | |
| Stream.Close() | 0x0175 | |
| Stream.SetSlice() | 0x0176 | |
| Stream.Slice() | 0x0177 | |

---

## Capsules & IPC

### Pack.* (1 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Pack.Use() | 0x0160 | |

### Card.* (3 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Card.Read() | 0x0161 | |
| Card.Write() | 0x0162 | |
| Card.Address() | 0x0163 | |

### Fifo.* (4 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Fifo.Open() | 0x0164 | |
| Fifo.Send() | 0x0165 | |
| Fifo.Recv() | 0x0166 | |
| Fifo.Poll() | 0x0167 | |

### Capsule.* (5 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Capsule.Call() | 0x02C0 | |
| Capsule.Schedule() | 0x02C1 | |
| Capsule.Jump() | 0x02C2 | |
| Capsule.LoadModule() | 0x02C3 | |
| Capsule.RunModule() | 0x02C4 | |

---

## Events & UI

### Event.* (10 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Event.Post() | 0x0180 | |
| Event.Next() | 0x0181 | |
| Event.Type() | 0x0182 | |
| Event.Target() | 0x0183 | |
| Event.Data() | 0x0184 | |
| Event.SetData() | 0x0185 | |
| Event.Count() | 0x0186 | |
| Event.SetSlice() | 0x01B3 | |
| Event.DataSlice() | 0x01B4 | |
| Event.DataLen() | 0x01B5 | |

### Ui.* (12 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Ui.Window() | 0x0188 | |
| Ui.Panel() | 0x0189 | |
| Ui.Label() | 0x018A | |
| Ui.Button() | 0x018B | |
| Ui.TextBox() | 0x018C | |
| Ui.Checkbox() | 0x018D | |
| Ui.Pos() | 0x018E | |
| Ui.Size() | 0x018F | |
| Ui.SetText() | 0x0190 | |
| Ui.SetId() | 0x0191 | |
| Ui.SetValue() | 0x0192 | |
| Ui.Serialize() | 0x0193 | |

### Assert.* (5 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Assert.Eq() | 0x0178 | |
| Assert.True() | 0x0179 | |
| Assert.Count() | 0x017A | |
| Assert.Failed() | 0x017B | |
| Assert.Reset() | 0x017C | |

---

## AI & Inference

### Tensor.* (12 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Tensor.SetShape() | 0x01E0 | |
| Tensor.DotI8() | 0x01E1 | |
| Tensor.MatVecI8() | 0x01E2 | |
| Tensor.AddI32() | 0x01E3 | |
| Tensor.MulI32() | 0x01E4 | |
| Tensor.ScaleI32() | 0x01E5 | |
| Tensor.ReluI32() | 0x01E6 | |
| Tensor.RmsNormI32() | 0x01E7 | |
| Tensor.RoPEI32() | 0x01E8 | |
| Tensor.SoftmaxI32() | 0x01E9 | |
| Tensor.ArgMaxI32() | 0x01EA | |
| Tensor.HasAccel() | 0x01EB | |

### BitLinear.* (5 hooks)

| Method | Code | Description |
|--------|------|-------------|
| BitLinear.SetShape() | 0x01F0 | |
| BitLinear.MatVecTernary() | 0x01F1 | |
| BitLinear.MatVecBitmap() | 0x01F2 | |
| BitLinear.MatVecBase3() | 0x01F3 | |
| BitLinear.HasFormat() | 0x01F4 | |

### Quant.* (5 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Quant.AbsMax() | 0x0228 | |
| Quant.QuantI8() | 0x0229 | |
| Quant.DequantI8() | 0x022A | |
| Quant.ApplyScale() | 0x022B | |
| Quant.GroupScale() | 0x022C | |

### Attention.* (4 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Attention.SetShape() | 0x0250 | |
| Attention.Scores() | 0x0251 | |
| Attention.Mix() | 0x0252 | |
| Attention.Attend() | 0x0253 | |

### Tokenizer.* (7 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Tokenizer.SetVocab() | 0x0210 | |
| Tokenizer.EncodeBytes() | 0x0211 | |
| Tokenizer.EncodeTrie() | 0x0212 | |
| Tokenizer.DecodeBytes() | 0x0213 | |
| Tokenizer.DecodeTrie() | 0x0214 | |
| Tokenizer.Count() | 0x0215 | |
| Tokenizer.Token() | 0x0216 | |

### Model.* (9 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Model.SetConfig() | 0x0220 | |
| Model.GetConfig() | 0x0221 | |
| Model.TensorView() | 0x0222 | |
| Model.TensorOffset() | 0x0223 | |
| Model.TensorRows() | 0x0224 | |
| Model.TensorCols() | 0x0225 | |
| Model.TensorFormat() | 0x0226 | |
| Model.ReadTensor() | 0x0227 | |
| Model.ReadTensorRow() | 0x0270 | |

### Kv.* (12 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Kv.SetShape() | 0x0230 | |
| Kv.WriteK() | 0x0231 | |
| Kv.WriteV() | 0x0232 | |
| Kv.ReadK() | 0x0233 | |
| Kv.ReadV() | 0x0234 | |
| Kv.Len() | 0x0235 | |
| Kv.Clear() | 0x0236 | |
| Kv.SetHead() | 0x0237 | |
| Kv.WriteKH() | 0x0238 | |
| Kv.WriteVH() | 0x0239 | |
| Kv.ReadKH() | 0x023A | |
| Kv.ReadVH() | 0x023B | |

### Sampling.* (4 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Sampling.ArgMax() | 0x0240 | |
| Sampling.TopK() | 0x0241 | |
| Sampling.Temperature() | 0x0242 | |
| Sampling.ArgMaxRows() | 0x0243 | |

---

## OS Worker

### Process.* (8 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Process.Self() | 0x0280 | |
| Process.Parent() | 0x0281 | |
| Process.Spawn() | 0x0282 | |
| Process.Exit() | 0x0283 | |
| Process.Kill() | 0x0284 | |
| Process.Status() | 0x0285 | |
| Process.Wait() | 0x0286 | |
| Process.Args() | 0x0287 | |

### Env.* (4 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Env.Get() | 0x0288 | |
| Env.Set() | 0x0289 | |
| Env.Count() | 0x028A | |
| Env.Key() | 0x028B | |

### Timer.* (4 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Timer.After() | 0x0290 | |
| Timer.Every() | 0x0291 | |
| Timer.Cancel() | 0x0292 | |
| Timer.Elapsed() | 0x0293 | |

### Scheduler.* (1 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Scheduler.Tick() | 0x0294 | |

### Error.* (6 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Error.SetHandler() | 0x02B0 | |
| Error.HasHandler() | 0x02B1 | |
| Error.Code() | 0x02B2 | |
| Error.Detail() | 0x02B3 | |
| Error.Resume() | 0x02B4 | |
| Error.Clear() | 0x02B5 | |

---

## System Info

### Environment.* (9 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Environment.GetOsVersion() | 0x00D0 | |
| Environment.GetCpuCount() | 0x00D1 | |
| Environment.GetMemoryTotal() | 0x00D2 | |
| Environment.GetMemoryFree() | 0x00D3 | |
| Environment.GetHostname() | 0x00D4 | |
| Environment.GetTimeZone() | 0x00D5 | |
| Environment.GetProcessId() | 0x00D6 | |
| Environment.GetThreadId() | 0x00D7 | |
| Environment.GetElapsedTime() | 0x00D8 | |

### Status.* (1 hooks)

| Method | Code | Description |
|--------|------|-------------|
| Status.Last() | 0x005E | |

---

## Structured Data — Map, Parsing & Binary Serialization

First-class dictionary (`Map`) plus string/bytes → structured `Map` parsers.
Full design + semantics in [docs/MAP.md](MAP.md). Implemented identically on the
Python, JS and C VMs (bit-identical output).

### Map.* (27 hooks) — active-handle dictionary

Keys: int / string / hash (FNV-1a). Values: int / string / null. Insertion-order
enumeration. `New`/`Use` select the active map; every other op acts on it (so all
ops fit the 2-arg host-call ABI — no compiler changes in any dialect).

| Method | Code | Description |
|--------|------|-------------|
| Map.New() | 0x0320 | create empty map, set active -> handle |
| Map.Use(h) | 0x033A | select the active map |
| Map.Free(h) | 0x0321 | release a map |
| Map.Clear() | 0x0322 | empty the active map |
| Map.Count() | 0x0323 | entry count |
| Map.Hash(span) | 0x0324 | FNV-1a 32-bit |
| Map.PutII(k,v) / GetII(k) | 0x0325 / 0x0326 | int->int |
| Map.HasI(k) / DelI(k) | 0x0327 / 0x0328 | int-key has / delete |
| Map.PutIS(k,vSpan) / GetIS(k) | 0x0329 / 0x032A | int->string |
| Map.PutNullI(k) / IsNullI(k) | 0x032B / 0x032C | int->null |
| Map.PutSI(kSpan,v) / GetSI(kSpan) | 0x032D / 0x032E | string->int |
| Map.HasS(kSpan) / DelS(kSpan) | 0x032F / 0x0330 | string-key has / delete |
| Map.PutSS(kSpan,vSpan) / GetSS(kSpan) | 0x0331 / 0x0332 | string->string |
| Map.PutNullS(kSpan) / IsNullS(kSpan) | 0x0333 / 0x0334 | string->null |
| Map.KeyAt(i) / KeySpanAt(i) | 0x0335 / 0x0336 | enumerate keys |
| Map.ValAt(i) / ValSpanAt(i) | 0x0337 / 0x0338 | enumerate values |
| Map.ValIsSpan(i) | 0x0339 | value at index is a string |

### Json.* / Binary.* (parsing & serialization)

| Method | Code | Description |
|--------|------|-------------|
| Json.Parse(span) | 0x0340 | flat JSON object -> Map |
| Binary.ParseCard(span) | 0x0341 | PicoBinarySerializer PSC1 card -> Map |
| Binary.SerializeCard() | 0x0342 | active Map -> PSC1 card |
| Binary.ParseEntity(blob,schema) | 0x0343 | BSO1 (BareMetal.Binary) entity -> Map |
| Binary.SerializeEntity(data,schema) | 0x0344 | Map -> BSO1 entity (signed if key set) |
| Binary.SetKey(span) | 0x0345 | BSO1 HMAC-SHA256 signing key |
| Binary.Verify(blob) | 0x0346 | verify BSO1 HMAC signature -> 0\|1 |

### Http transport (4 hooks) — used by the workflow WEB action

| Method | Code | Description |
|--------|------|-------------|
| Http.Request(reqMap,body) | 0x0138 | send request (headers as a Map) -> response handle |
| Http.RespStatus(resp) | 0x0139 | response status code |
| Http.RespHeaders(resp) | 0x013A | response headers -> enumerable Map |
| Http.RespBody(resp,outDesc) | 0x013B | response body span |
