/* Host provider install + hook dispatch for Time / Random / Environment. */
#include "pv_host_provider.h"
#include "pico_hooks.h"

#include <string.h>

static const pv_host_provider *g_provider;
static pv_host_provider g_provider_copy;

#define PV_HOST_TIMER_MAX 64
static int g_timer_seq;
static struct { int ms, rem, rep, active; } g_timer[PV_HOST_TIMER_MAX];
static int g_timer_elapsed;

extern pv_host_provider_dispatch_fn pv_host_provider_dispatch_hook;

void pv_host_install(const pv_host_provider *provider)
{
    if (provider) {
        g_provider_copy = *provider;
        g_provider = &g_provider_copy;
        pv_host_provider_dispatch_hook = pv_host_provider_dispatch;
    } else {
        g_provider = 0;
        pv_host_provider_dispatch_hook = 0;
    }
}

const pv_host_provider *pv_host_current(void)
{
    return g_provider;
}

static int put_span_cstr(pv_ctx *ctx, int rd, int (*fn)(void *, char *, uint32_t),
                         void *user)
{
    char buf[256];
    int n;
    if (!fn) {
        ctx->regs[rd] = 0;
        ctx->host_status = 1;
        return 1;
    }
    n = fn(user, buf, (uint32_t)sizeof(buf));
    if (n < 0) {
        ctx->regs[rd] = 0;
        ctx->host_status = 1;
        return 1;
    }
    if (n > (int)sizeof(buf)) n = (int)sizeof(buf);
    ctx->regs[rd] = pv_span_from_bytes(ctx, buf, (uint32_t)n);
    ctx->host_status = 0;
    return 1;
}

int pv_host_provider_dispatch(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    const pv_host_provider *p = g_provider;
    void *user;
    if (!p || !ctx) return 0;
    user = p->user;

    /* ---- Time (wall) -------------------------------------------------- */
    if (hook == PV_HOOK_DATETIME_NOW || hook == PV_HOOK_DATETIME_UTCNOW ||
        hook == PV_HOOK_DATETIME_UNIXTIMESTAMP) {
        int64_t t;
        if (!p->now_unix) {
            ctx->regs[rd] = 0;
            ctx->host_status = 1;
            return 1;
        }
        t = p->now_unix(user);
        ctx->regs[rd] = (int32_t)(uint32_t)(t & 0xffffffffu);
        ctx->host_status = 0;
        return 1;
    }

    /* ---- Random ------------------------------------------------------- */
    if (hook == PV_HOOK_MATHS_RANDOM) {
        uint32_t u;
        if (!p->random_u32) {
            ctx->regs[rd] = 0;
            ctx->host_status = 1;
            return 1;
        }
        /* Q16.16 fraction in [0, 1): top 16 bits of a u32. */
        u = p->random_u32(user);
        ctx->regs[rd] = (int32_t)(u >> 16);
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_MATHS_RANDOMRANGE) {
        int32_t lo = ctx->regs[rs1];
        int32_t hi = ctx->regs[rs2];
        uint32_t span;
        uint32_t u;
        if (!p->random_u32) {
            ctx->regs[rd] = 0;
            ctx->host_status = 1;
            return 1;
        }
        if (lo > hi) {
            int32_t tmp = lo;
            lo = hi;
            hi = tmp;
        }
        span = (uint32_t)((int64_t)hi - (int64_t)lo + 1);
        if (span == 0) {
            ctx->regs[rd] = lo;
            ctx->host_status = 0;
            return 1;
        }
        u = p->random_u32(user);
        ctx->regs[rd] = lo + (int32_t)(u % span);
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_CRYPTO_RANDOMBYTES) {
        int32_t n = ctx->regs[rs1];
        uint8_t tmp[512];
        uint32_t count;
        if (!p->random_bytes || n <= 0) {
            ctx->regs[rd] = 0;
            ctx->host_status = (n <= 0) ? 2 : 1;
            return 1;
        }
        count = (uint32_t)n;
        if (count > sizeof(tmp)) count = (uint32_t)sizeof(tmp);
        if (p->random_bytes(user, tmp, count) != 0) {
            ctx->regs[rd] = 0;
            ctx->host_status = 1;
            return 1;
        }
        ctx->regs[rd] = pv_span_from_bytes(ctx, tmp, count);
        ctx->host_status = 0;
        (void)rs2;
        return 1;
    }

    /* ---- Environment -------------------------------------------------- */
    if (hook == PV_HOOK_ENVIRONMENT_GETOSVERSION)
        return put_span_cstr(ctx, rd, p->os_version, user);
    if (hook == PV_HOOK_ENVIRONMENT_GETHOSTNAME)
        return put_span_cstr(ctx, rd, p->hostname, user);
    if (hook == PV_HOOK_ENVIRONMENT_GETTIMEZONE)
        return put_span_cstr(ctx, rd, p->timezone, user);
    if (hook == PV_HOOK_ENVIRONMENT_GETCPUCOUNT) {
        ctx->regs[rd] = p->cpu_count ? p->cpu_count(user) : 0;
        ctx->host_status = p->cpu_count ? 0 : 1;
        return 1;
    }
    if (hook == PV_HOOK_ENVIRONMENT_GETMEMORYTOTAL) {
        int64_t v = p->memory_total ? p->memory_total(user) : 0;
        ctx->regs[rd] = (int32_t)(uint32_t)(v & 0xffffffffu);
        ctx->host_status = p->memory_total ? 0 : 1;
        return 1;
    }
    if (hook == PV_HOOK_ENVIRONMENT_GETMEMORYFREE) {
        int64_t v = p->memory_free ? p->memory_free(user) : 0;
        ctx->regs[rd] = (int32_t)(uint32_t)(v & 0xffffffffu);
        ctx->host_status = p->memory_free ? 0 : 1;
        return 1;
    }
    if (hook == PV_HOOK_ENVIRONMENT_GETPROCESSID) {
        ctx->regs[rd] = p->process_id ? p->process_id(user) : 0;
        ctx->host_status = p->process_id ? 0 : 1;
        return 1;
    }
    if (hook == PV_HOOK_ENVIRONMENT_GETTHREADID) {
        ctx->regs[rd] = p->thread_id ? p->thread_id(user) : 0;
        ctx->host_status = p->thread_id ? 0 : 1;
        return 1;
    }
    if (hook == PV_HOOK_ENVIRONMENT_GETELAPSEDTIME) {
        ctx->regs[rd] = p->elapsed_ms ? p->elapsed_ms(user) : 0;
        ctx->host_status = p->elapsed_ms ? 0 : 1;
        return 1;
    }

    if (p->hook)
        return p->hook(ctx, hook, rd, rs1, rs2, user);

    return 0;
}

static void push_timer_event(pv_ctx *ctx, int target)
{
    int id = ++ctx->event_seq;
    if (id >= PV_MAX_EVENTS) id = 1;
    ctx->event_seq = id;
    ctx->event_used[id] = 1;
    ctx->event_type[id] = 100;
    ctx->event_target[id] = target;
    ctx->event_span[id] = 0;
    if (((ctx->event_qtail + 1) % PV_MAX_EVENTS) != ctx->event_qhead) {
        ctx->event_queue[ctx->event_qtail] = id;
        ctx->event_qtail = (ctx->event_qtail + 1) % PV_MAX_EVENTS;
    }
}

int pv_host_provider_shared_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2, void *user)
{
    (void)user;
    if (!ctx) return 0;
    if (hook == PV_HOOK_TIMER_AFTER || hook == PV_HOOK_TIMER_EVERY) {
        int h = ++g_timer_seq;
        if (h >= PV_HOST_TIMER_MAX) h = 1;
        g_timer[h].ms = ctx->regs[rs1];
        g_timer[h].rem = ctx->regs[rs1];
        g_timer[h].rep = (hook == PV_HOOK_TIMER_EVERY);
        g_timer[h].active = 1;
        ctx->regs[rd] = h;
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_TIMER_CANCEL) {
        int h = ctx->regs[rs1];
        if (h > 0 && h < PV_HOST_TIMER_MAX && g_timer[h].active) {
            g_timer[h].active = 0;
            ctx->regs[rd] = 1;
        } else ctx->regs[rd] = 0;
        ctx->host_status = ctx->regs[rd] ? 0 : 1;
        return 1;
    }
    if (hook == PV_HOOK_TIMER_ELAPSED) {
        ctx->regs[rd] = g_timer_elapsed;
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_SCHEDULER_TICK) {
        int delta = ctx->regs[rs1];
        int i, fired = 0;
        g_timer_elapsed += delta;
        for (i = 1; i < PV_HOST_TIMER_MAX; i++) {
            if (!g_timer[i].active) continue;
            g_timer[i].rem -= delta;
            while (g_timer[i].rem <= 0 && g_timer[i].active) {
                fired++;
                push_timer_event(ctx, i);
                if (g_timer[i].rep) g_timer[i].rem += g_timer[i].ms;
                else g_timer[i].active = 0;
            }
        }
        ctx->regs[rd] = fired;
        ctx->host_status = 0;
        return 1;
    }
    return 0;
}
