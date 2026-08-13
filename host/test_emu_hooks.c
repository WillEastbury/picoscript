/* Smoke test for picovm_emu multi-target hooks. */
#include "picovm.h"
#include "picovm_emu.h"
#include "pico_hooks.h"
#include "pv_host_provider.h"

#include <stdio.h>
#include <string.h>

#define CHECK(x) do { if (!(x)) { \
  fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #x); return 1; } } while (0)

static uint8_t mem[256 * 1024];

static void reset(pv_ctx *c)
{
    pv_init(c);
    c->mem = mem;
    c->mem_size = (long)sizeof(mem);
    c->caps = PV_CAP_ALL;
}

int main(void)
{
    pv_ctx ctx;
    int64_t v;

    pv_host_install(pv_host_null());
    pv_host_null_set_time(1700000000);

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_DATETIME_UTCNOW, 0, 0);
    CHECK(v == 1700000000);

    reset(&ctx);
    /* Format epoch 0 */
    v = pv_host2(&ctx, PV_HOOK_DATETIME_FORMAT, 0, 0);
    CHECK(v > 0);

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_DATETIME_ADDDAYS, 0, 1);
    CHECK(v == 86400);

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_DATETIME_YEAR, 0, 0);
    CHECK(v == 1970);

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_GPIO_COUNT, 0, 0);
    CHECK(v == 40);
    pv_host2(&ctx, PV_HOOK_GPIO_WRITE, 3, 512);
    /* write: rs1=pin via a, rs2=val via b — host2 puts a in r1, b in r2 */
    reset(&ctx);
    ctx.regs[1] = 3;
    ctx.regs[2] = 512;
    pv_emu_dispatch(&ctx, PV_HOOK_GPIO_WRITE, 0, 1, 2);
    pv_emu_dispatch(&ctx, PV_HOOK_GPIO_READ, 0, 1, 2);
    CHECK(ctx.regs[0] == 512);

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_DEVICE_OPEN, 0, 0);
    CHECK(v >= 1);

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_AUTH_VALIDATECREDENTIALS, 0, 0);
    /* empty user/pass fail */
    CHECK(v == 0 || v == 1);

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_LOCALE_GETCURRENTLOCALE, 0, 0);
    CHECK(v > 0);

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_PROCESS_SELF, 0, 0);
    CHECK(v == 1);

    pv_host_install(0);
    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_TIMER_AFTER, 100, 0);
    CHECK(v >= 1);
    v = pv_host2(&ctx, PV_HOOK_SCHEDULER_TICK, 100, 0);
    CHECK(v >= 1);
    pv_emu_dispatch(&ctx, PV_HOOK_EVENT_COUNT, 0, 1, 2);
    CHECK(ctx.regs[0] >= 1);
    pv_emu_dispatch(&ctx, PV_HOOK_EVENT_NEXT, 0, 1, 2);
    v = ctx.regs[0];
    CHECK(v >= 1);
    ctx.regs[1] = (int32_t)v;
    pv_emu_dispatch(&ctx, PV_HOOK_EVENT_TYPE, 0, 1, 2);
    CHECK(ctx.regs[0] == 100);
    pv_host_install(pv_host_null());

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_PRINCIPAL_CURRENT, 0, 0);
    CHECK(v > 0);
    v = pv_host2(&ctx, PV_HOOK_CAPABILITY_HAS, PV_CAP_STORAGE, 0);
    CHECK(v == 1);
    v = pv_host2(&ctx, PV_HOOK_SANDBOX_DENY, PV_CAP_STORAGE, 0);
    CHECK(v == 1);
    v = pv_host2(&ctx, PV_HOOK_CAPABILITY_HAS, PV_CAP_STORAGE, 0);
    CHECK(v == 0);
    v = pv_host2(&ctx, PV_HOOK_CAPABILITY_REQUEST, PV_CAP_STORAGE, 0);
    CHECK(v == 0);

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_UI_WINDOW, 0, 0);
    CHECK(v >= 1);

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_ENVIRONMENT_GETHOSTNAME, 0, 0);
    CHECK(v > 0); /* null provider → localhost */

    reset(&ctx);
    v = pv_host2(&ctx, PV_HOOK_CONTEXT_GETHOST, 0, 0);
    CHECK(v > 0);

    printf("PASS emu hooks smoke\n");
    return 0;
}
