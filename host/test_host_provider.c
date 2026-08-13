/* Smoke test: null (deterministic) + platform host providers. */
#include "pv_host_provider.h"
#include "pico_hooks.h"

#include <stdio.h>
#include <string.h>

#define CHECK(x)                                                               \
    do {                                                                       \
        if (!(x)) {                                                            \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #x);       \
            return 1;                                                          \
        }                                                                      \
    } while (0)

static uint8_t g_mem[256 * 1024];

static void reset_ctx(pv_ctx *ctx)
{
    pv_init(ctx);
    ctx->mem = g_mem;
    ctx->mem_size = (long)sizeof(g_mem);
    ctx->caps = PV_CAP_ALL;
}

static int span_eq(pv_ctx *ctx, int handle, const char *lit)
{
    /* Compare via pv_host2-style: we only check non-zero length and prefix via
     * Memory — use the span table directly. */
    uint32_t ptr;
    int32_t len;
    size_t n;
    int i;
    if (handle <= 0 || handle >= ctx->span_count) return 0;
    ptr = ctx->span_ptr[handle];
    len = ctx->span_len[handle];
    n = strlen(lit);
    if ((size_t)len != n) return 0;
    for (i = 0; i < len; i++) {
        if (!ctx->mem || ptr + (uint32_t)i >= (uint32_t)ctx->mem_size) return 0;
        if (ctx->mem[ptr + (uint32_t)i] != (uint8_t)lit[i]) return 0;
    }
    return 1;
}

int main(void)
{
    pv_ctx ctx;
    int64_t a, b;

    /* ---- Null / deterministic ----------------------------------------- */
    pv_host_null_seed(0xC0FFEEULL);
    pv_host_null_set_time(1700000000);
    pv_host_install(pv_host_null());
    CHECK(pv_host_current() && pv_host_current()->kind == PV_HOST_KIND_NULL);

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_DATETIME_UTCNOW, 0, 0);
    CHECK(a == 1700000000);
    a = pv_host2(&ctx, PV_HOOK_DATETIME_NOW, 0, 0);
    CHECK(a == 1700000000);

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_MATHS_RANDOM, 0, 0);
    b = pv_host2(&ctx, PV_HOOK_MATHS_RANDOM, 0, 0);
    /* Same seed path: stream advances; values need not match wall-clock runs. */
    CHECK(ctx.host_status == 0);
    (void)a;
    (void)b;

    reset_ctx(&ctx);
    ctx.regs[1] = 10;
    ctx.regs[2] = 20;
    a = pv_host2(&ctx, PV_HOOK_MATHS_RANDOMRANGE, 10, 20);
    CHECK(a >= 10 && a <= 20);

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_CRYPTO_RANDOMBYTES, 16, 0);
    CHECK(a > 0);
    CHECK(ctx.host_status == 0);

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_ENVIRONMENT_GETHOSTNAME, 0, 0);
    CHECK(a > 0);
    CHECK(span_eq(&ctx, (int)a, "localhost"));

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_ENVIRONMENT_GETOSVERSION, 0, 0);
    CHECK(span_eq(&ctx, (int)a, "null"));

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_ENVIRONMENT_GETCPUCOUNT, 0, 0);
    CHECK(a == 1);

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_TIMER_AFTER, 100, 0);
    CHECK(a >= 1);
    b = pv_host2(&ctx, PV_HOOK_SCHEDULER_TICK, 100, 0);
    CHECK(b == 1);
    a = pv_host2(&ctx, PV_HOOK_TIMER_ELAPSED, 0, 0);
    CHECK(a == 100);
    a = pv_host2(&ctx, PV_HOOK_EVENT_COUNT, 0, 0);
    CHECK(a == 1);

    /* Determinism: reseed + same time → same first Maths.Random */
    pv_host_null_seed(0xC0FFEEULL);
    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_MATHS_RANDOM, 0, 0);
    pv_host_null_seed(0xC0FFEEULL);
    reset_ctx(&ctx);
    b = pv_host2(&ctx, PV_HOOK_MATHS_RANDOM, 0, 0);
    CHECK(a == b);

    printf("PASS null provider\n");

    /* ---- Platform (Win or POSIX) -------------------------------------- */
    pv_host_install(pv_host_platform());
    CHECK(pv_host_current());
    CHECK(pv_host_current()->kind == PV_HOST_KIND_WIN ||
          pv_host_current()->kind == PV_HOST_KIND_POSIX);

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_DATETIME_UTCNOW, 0, 0);
    CHECK(a > 1600000000); /* after 2020 */
    CHECK(ctx.host_status == 0);

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_ENVIRONMENT_GETCPUCOUNT, 0, 0);
    CHECK(a >= 1);

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_ENVIRONMENT_GETHOSTNAME, 0, 0);
    CHECK(a > 0);
    CHECK(ctx.host_status == 0);

    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_CRYPTO_RANDOMBYTES, 32, 0);
    CHECK(a > 0);

    printf("PASS platform provider (%s)\n", pv_host_current()->name);

    /* ---- PIOS skeleton installs ---------------------------------------- */
    pv_host_install(pv_host_pios());
    CHECK(pv_host_current()->kind == PV_HOST_KIND_PIOS);
    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_DATETIME_UTCNOW, 0, 0);
    CHECK(a == 0); /* skeleton returns 0 until kernel wires TIME */
    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_CRYPTO_RANDOMBYTES, 32, 0);
    CHECK(a == 0);
    CHECK(ctx.host_status == 1);
    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_NET_LISTEN, 8080, 0);
    CHECK(a == 0);
    CHECK(ctx.host_status == 1);
    reset_ctx(&ctx);
    a = pv_host2(&ctx, PV_HOOK_X509_GETKEYHANDLE, 0, 0);
    CHECK(a == 0);
    CHECK(ctx.host_status == 1);
    printf("PASS pios provider skeleton\n");

    printf("PASS host provider suite\n");
    return 0;
}
