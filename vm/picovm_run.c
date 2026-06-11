/* picovm_run.c -- host test harness for picovm.c.
 *
 * Reads a program from stdin:  first line = word count N, then N lines each a
 * hex 32-bit instruction word.  Runs it and prints final state as lines:
 *
 *   STEPS <n>
 *   FAULT <code> <pc> <detail>
 *   STATUS <http_status>
 *   REGS <r0> <r1> ... <r15>
 *   OUT  <hex bytes>
 *
 * Used by tools to compare against picoscript_vm.PicoVM.
 */
#include "picovm.h"
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    int n = 0;
    if (scanf("%d", &n) != 1) return 1;

    static uint32_t prog[65536];
    if (n < 0) n = 0;
    if (n > 65536) n = 65536;
    for (int i = 0; i < n; i++) {
        unsigned w = 0;
        if (scanf("%x", &w) != 1) return 1;
        prog[i] = (uint32_t)w;
    }

    static uint8_t arena[520 * 1024];   /* data arena for Memory.* / Io (Pico 2 SRAM size) */
    pv_ctx ctx;
    pv_init(&ctx);
    ctx.mem = arena;
    ctx.mem_size = (long)sizeof(arena);
    const char *ms = getenv("PICOVM_MAX_STEPS");   /* let tests drive the step budget */
    if (ms && *ms) ctx.max_steps = atol(ms);
    const char *cp = getenv("PICOVM_CAPS");         /* let tests restrict binding capabilities */
    if (cp && *cp) ctx.caps = (uint32_t)strtoul(cp, 0, 0);
    const char *na = getenv("PICOVM_NOALLOC");       /* let tests enable hot-path no-alloc mode */
    if (na && *na && na[0] != '0') ctx.no_alloc = 1;
    const char *sd = getenv("PICOVM_SEED");         /* let tests pin Random.U32 nondeterminism */
    if (sd && *sd) ctx.rng_state = (uint64_t)strtoull(sd, 0, 0);
    long steps = pv_vm_run(&ctx, prog, n);

    printf("STEPS %ld\n", steps);
    printf("FAULT %d %d %d\n", ctx.fault, ctx.fault_pc, ctx.fault_detail);
    printf("STATUS %d\n", ctx.http_status);
    printf("REGS");
    for (int i = 0; i < PV_NUM_REGS; i++) printf(" %d", ctx.regs[i]);
    printf("\n");
    printf("OUT");
    for (int i = 0; i < ctx.out_len; i++) printf(" %02x", ctx.out[i]);
    printf("\n");
    return 0;
}
