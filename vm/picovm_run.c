/* picovm_run.c -- host test harness for picovm.c.
 *
 * Reads a program from stdin:  first line = word count N, then N lines each a
 * hex 32-bit instruction word.  Runs it and prints final state as lines:
 *
 *   STEPS <n>
 *   STATUS <http_status>
 *   REGS <r0> <r1> ... <r15>
 *   OUT  <hex bytes>
 *
 * Used by tools to compare against picoscript_vm.PicoVM.
 */
#include "picovm.h"
#include <stdio.h>

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

    pv_ctx ctx;
    pv_init(&ctx);
    long steps = pv_vm_run(&ctx, prog, n);

    printf("STEPS %ld\n", steps);
    printf("STATUS %d\n", ctx.http_status);
    printf("REGS");
    for (int i = 0; i < PV_NUM_REGS; i++) printf(" %d", ctx.regs[i]);
    printf("\n");
    printf("OUT");
    for (int i = 0; i < ctx.out_len; i++) printf(" %02x", ctx.out[i]);
    printf("\n");
    return 0;
}
