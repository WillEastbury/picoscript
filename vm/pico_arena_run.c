/* pico_arena_run.c -- generic harness for PicoScript-compiled C with an arena.
 *
 * Compiles together with picovm.c and a toC-lowered program whose entry is
 * `pico_entry` (lower_to_c(func_name="pico_entry", emit_main=False)). It gives
 * the program a real byte-addressable data arena (ctx->mem) so Memory.* and
 * Io.WriteByte run natively, then streams the result bytes out.
 *
 *   argv[1] = arena size in bytes
 *   argv[2] = seed length (bytes read from stdin into the arena at offset 0)
 *   stdin   = initial arena image
 *   stdout  = ctx->out bytes after pico_entry returns
 *
 * This is how a host (or the PIOS kernel) loads a model into arena memory and
 * runs a PicoScript-compiled kernel over it at native speed.
 */
#include "picovm.h"
#include <stdio.h>
#include <stdlib.h>

int64_t pico_entry(pv_ctx *ctx);

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s <arena_bytes> <seed_len>\n", argv[0]);
        return 2;
    }
    long arena = strtol(argv[1], NULL, 10);
    long dlen = strtol(argv[2], NULL, 10);
    if (arena <= 0 || dlen < 0 || dlen > arena) {
        fprintf(stderr, "bad sizes\n");
        return 2;
    }

    pv_ctx *ctx = (pv_ctx *)calloc(1, sizeof(pv_ctx));
    if (!ctx) return 1;
    pv_init(ctx);

    ctx->mem = (uint8_t *)calloc((size_t)arena, 1);
    if (!ctx->mem) { fprintf(stderr, "arena alloc failed\n"); return 1; }
    ctx->mem_size = arena;
    ctx->max_steps = 0;   /* pico_entry is straight-line C; no step budget */

#if defined(_WIN32)
    /* avoid CRLF translation mangling the binary arena image / output */
    {
        extern int _setmode(int, int);
        _setmode(_fileno(stdin), 0x8000 /*_O_BINARY*/);
        _setmode(_fileno(stdout), 0x8000 /*_O_BINARY*/);
    }
#endif

    if (dlen > 0) {
        size_t got = fread(ctx->mem, 1, (size_t)dlen, stdin);
        (void)got;
    }

    pico_entry(ctx);

    fwrite(ctx->out, 1, (size_t)ctx->out_len, stdout);
    fflush(stdout);
    free(ctx->mem);
    free(ctx);
    return 0;
}
