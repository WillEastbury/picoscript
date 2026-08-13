#include "picovm.h"
#include "pico_hooks.h"
#include "picotls/tls/keysched.h"
#include "picotls/tls/record.h"

#include <stdio.h>
#include <string.h>

extern int pv_span_from_bytes(pv_ctx *ctx, const void *data, uint32_t len);
extern int pv_crypto_ext_dispatch(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #x); return 1; } } while (0)

static unsigned char mem[256 * 1024];

static int span_copy_local(pv_ctx *ctx, int h, unsigned char *out, int cap)
{
    int n = (h > 0 && h < ctx->span_count) ? ctx->span_len[h] : 0;
    unsigned p = (h > 0 && h < ctx->span_count) ? ctx->span_ptr[h] : 0;
    int i;
    if (n < 0) n = 0;
    if (n > cap) n = cap;
    for (i = 0; i < n; i++) out[i] = ctx->mem[(p + (unsigned)i) % (unsigned)ctx->mem_size];
    return n;
}

int main(void)
{
    pv_ctx ctx;
    int key_span, msg_span, sig_span;
    unsigned char pair[64], sigbuf[256];
    uint8_t traffic_secret[32], key[32], iv[12];
    tls_record_dir_t tx, rx;
    uint8_t record[256], *plain;
    size_t plain_len;
    tls_content_type_t inner;
    size_t wire_len;
    int i;

    pv_init(&ctx);
    ctx.mem = mem;
    ctx.mem_size = (long)sizeof(mem);
    ctx.caps = PV_CAP_ALL;

#if !defined(_MSC_VER)
    pv_host2(&ctx, PV_HOOK_CRYPTO_GENERATEKEYPAIR, 0, 0);
    key_span = ctx.regs[0];
    CHECK(span_copy_local(&ctx, key_span, pair, sizeof(pair)) == 64);

    msg_span = pv_span_from_bytes(&ctx, "hello tls", 9);
    ctx.regs[1] = key_span;
    ctx.regs[2] = msg_span;
    CHECK(pv_crypto_ext_dispatch(&ctx, PV_HOOK_CRYPTO_SIGN, 0, 1, 2) == 1);
    sig_span = ctx.regs[0];
    CHECK(span_copy_local(&ctx, sig_span, sigbuf, sizeof(sigbuf)) == 73);

    ctx.regs[1] = key_span;
    ctx.regs[2] = sig_span;
    CHECK(pv_crypto_ext_dispatch(&ctx, PV_HOOK_CRYPTO_VERIFY, 0, 1, 2) == 1);
    CHECK(ctx.regs[0] == 1);
#endif

    for (i = 0; i < 32; i++) traffic_secret[i] = (uint8_t)(i + 1);
    tls13_derive_traffic_keys(traffic_secret, key, iv);
    memset(&tx, 0, sizeof(tx));
    memset(&rx, 0, sizeof(rx));
    memcpy(tx.key, key, 32); memcpy(rx.key, key, 32);
    memcpy(tx.static_iv, iv, 12); memcpy(rx.static_iv, iv, 12);
    wire_len = tls13_seal_record(&tx, TLS_CT_APPLICATION_DATA, TLS_CT_APPLICATION_DATA,
                                 (const uint8_t *)"ping", 4, record, sizeof(record));
    CHECK(wire_len > 0);
    CHECK(tls13_open_record(&rx, record, wire_len, &inner, &plain, &plain_len) == 0);
    CHECK(inner == TLS_CT_APPLICATION_DATA);
    CHECK(plain_len == 4);
    CHECK(memcmp(plain, "ping", 4) == 0);

    printf("PASS tls core smoke\n");
    return 0;
}
