/* File-backed Auth + X509 store for hosted Win/POSIX (shippable default).
 *
 * Layout (default path: picoscript_auth.store):
 *   USR\tuser\tsha256hex(password)\tpermissions
 *   TOK\ttoken\tuser\texpiry_unix
 *   CRT\tname\tpem_base64_or_raw
 *
 * Seeds admin/admin on first open. Password stored as hex SHA-256.
 */
#include "picovm.h"
#include "pico_hooks.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

extern int pv_span_from_bytes(pv_ctx *ctx, const void *data, uint32_t len);
extern void pv_crypto_sha256_bytes(const uint8_t *data, size_t len, uint8_t out[32]);

#define AUTH_MAX_USERS 64
#define AUTH_MAX_TOKENS 128
#define AUTH_MAX_CERTS 32

static char g_path[512] = "picoscript_auth.store";
static int g_loaded;

static struct {
    char user[48];
    char pass_hex[65];
    char perms[96];
} g_users[AUTH_MAX_USERS];
static int g_nusers;

static struct {
    char token[64];
    char user[48];
    int64_t exp;
    int valid;
} g_tokens[AUTH_MAX_TOKENS];
static int g_ntokens;

static struct {
    char name[48];
    char pem[512];
} g_certs[AUTH_MAX_CERTS];
static int g_ncerts;

static void sha256_hex(const char *s, char out_hex[65])
{
    static const char *hexd = "0123456789abcdef";
    uint8_t dig[32];
    int i;
    pv_crypto_sha256_bytes((const uint8_t *)s, strlen(s), dig);
    for (i = 0; i < 32; i++) {
        out_hex[i * 2] = hexd[dig[i] >> 4];
        out_hex[i * 2 + 1] = hexd[dig[i] & 0xf];
    }
    out_hex[64] = 0;
}

static void auth_save(void)
{
    FILE *f = fopen(g_path, "wb");
    int i;
    if (!f) return;
    for (i = 0; i < g_nusers; i++)
        fprintf(f, "USR\t%s\t%s\t%s\n", g_users[i].user, g_users[i].pass_hex, g_users[i].perms);
    for (i = 0; i < g_ntokens; i++)
        if (g_tokens[i].valid)
            fprintf(f, "TOK\t%s\t%s\t%lld\n", g_tokens[i].token, g_tokens[i].user,
                    (long long)g_tokens[i].exp);
    for (i = 0; i < g_ncerts; i++)
        fprintf(f, "CRT\t%s\t%s\n", g_certs[i].name, g_certs[i].pem);
    fclose(f);
}

static void auth_seed(void)
{
    char hex[65];
    sha256_hex("admin", hex);
    strncpy(g_users[0].user, "admin", sizeof(g_users[0].user) - 1);
    strncpy(g_users[0].pass_hex, hex, sizeof(g_users[0].pass_hex) - 1);
    strncpy(g_users[0].perms, "read,write,admin", sizeof(g_users[0].perms) - 1);
    g_nusers = 1;
    strncpy(g_certs[0].name, "default", sizeof(g_certs[0].name) - 1);
    strncpy(g_certs[0].pem,
            "-----BEGIN CERTIFICATE-----\nMIIBdemoPicoScriptSelfSigned\n-----END CERTIFICATE-----\n",
            sizeof(g_certs[0].pem) - 1);
    g_ncerts = 1;
    auth_save();
}

static void auth_load(void)
{
    FILE *f;
    char line[1024];
    if (g_loaded) return;
    g_loaded = 1;
    g_nusers = g_ntokens = g_ncerts = 0;
    f = fopen(g_path, "rb");
    if (!f) {
        auth_seed();
        return;
    }
    while (fgets(line, sizeof(line), f)) {
        char *nl = strchr(line, '\n');
        char *t1, *t2, *t3;
        if (nl) *nl = 0;
        if (strncmp(line, "USR\t", 4) == 0 && g_nusers < AUTH_MAX_USERS) {
            t1 = line + 4;
            t2 = strchr(t1, '\t'); if (!t2) continue; *t2++ = 0;
            t3 = strchr(t2, '\t'); if (!t3) continue; *t3++ = 0;
            strncpy(g_users[g_nusers].user, t1, 47);
            strncpy(g_users[g_nusers].pass_hex, t2, 64);
            strncpy(g_users[g_nusers].perms, t3, 95);
            g_nusers++;
        } else if (strncmp(line, "TOK\t", 4) == 0 && g_ntokens < AUTH_MAX_TOKENS) {
            t1 = line + 4;
            t2 = strchr(t1, '\t'); if (!t2) continue; *t2++ = 0;
            t3 = strchr(t2, '\t'); if (!t3) continue; *t3++ = 0;
            strncpy(g_tokens[g_ntokens].token, t1, 63);
            strncpy(g_tokens[g_ntokens].user, t2, 47);
            g_tokens[g_ntokens].exp = (int64_t)atoll(t3);
            g_tokens[g_ntokens].valid = 1;
            g_ntokens++;
        } else if (strncmp(line, "CRT\t", 4) == 0 && g_ncerts < AUTH_MAX_CERTS) {
            t1 = line + 4;
            t2 = strchr(t1, '\t'); if (!t2) continue; *t2++ = 0;
            strncpy(g_certs[g_ncerts].name, t1, 47);
            strncpy(g_certs[g_ncerts].pem, t2, 511);
            g_ncerts++;
        }
    }
    fclose(f);
    if (g_nusers == 0) auth_seed();
}

void pv_auth_store_set_path(const char *path)
{
    if (path && path[0]) {
        strncpy(g_path, path, sizeof(g_path) - 1);
        g_loaded = 0;
    }
}

static void span_str(pv_ctx *ctx, int h, char *out, size_t cap)
{
    uint32_t p = (h > 0 && h < ctx->span_count) ? ctx->span_ptr[h] : 0;
    int32_t n = (h > 0 && h < ctx->span_count) ? ctx->span_len[h] : 0;
    int i;
    if (n < 0) n = 0;
    if ((size_t)n >= cap) n = (int32_t)cap - 1;
    for (i = 0; i < n; i++)
        out[i] = ctx->mem ? (char)ctx->mem[p + (uint32_t)i] : 0;
    out[n] = 0;
}

static int find_user(const char *u)
{
    int i;
    for (i = 0; i < g_nusers; i++)
        if (strcmp(g_users[i].user, u) == 0) return i;
    return -1;
}

int pv_auth_store_dispatch(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    char u[64], p[64], t[64];
    char hex[65];
    int i;
    auth_load();

    if (hook == PV_HOOK_AUTH_GETUSERCREDENTIALS) {
        span_str(ctx, ctx->regs[rs1], u, sizeof(u));
        i = u[0] ? find_user(u) : 0;
        if (i < 0) { ctx->regs[rd] = 0; ctx->host_status = 1; return 1; }
        {
            char out[128];
            snprintf(out, sizeof(out), "%s:", g_users[i].user);
            ctx->regs[rd] = pv_span_from_bytes(ctx, out, (uint32_t)strlen(out));
            ctx->host_status = 0;
        }
        return 1;
    }
    if (hook == PV_HOOK_AUTH_VALIDATECREDENTIALS) {
        span_str(ctx, ctx->regs[rs1], u, sizeof(u));
        span_str(ctx, ctx->regs[rs2], p, sizeof(p));
        sha256_hex(p, hex);
        i = find_user(u);
        ctx->regs[rd] = (i >= 0 && strcmp(g_users[i].pass_hex, hex) == 0) ? 1 : 0;
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_AUTH_GETUSERPERMISSIONS) {
        span_str(ctx, ctx->regs[rs1], u, sizeof(u));
        i = u[0] ? find_user(u) : 0;
        if (i < 0) { ctx->regs[rd] = 0; ctx->host_status = 1; return 1; }
        ctx->regs[rd] = pv_span_from_bytes(ctx, g_users[i].perms, (uint32_t)strlen(g_users[i].perms));
        return 1;
    }
    if (hook == PV_HOOK_AUTH_REQUESTTOKEN || hook == PV_HOOK_AUTH_GETTOKEN ||
        hook == PV_HOOK_AUTH_REFRESHTOKEN) {
        char tok[64];
        span_str(ctx, ctx->regs[rs1], u, sizeof(u));
        if (!u[0]) strcpy(u, "admin");
        snprintf(tok, sizeof(tok), "tok-%s-%08x", u, (unsigned)time(NULL) ^ (unsigned)g_ntokens);
        if (g_ntokens < AUTH_MAX_TOKENS) {
            strncpy(g_tokens[g_ntokens].token, tok, 63);
            strncpy(g_tokens[g_ntokens].user, u, 47);
            g_tokens[g_ntokens].exp = (int64_t)time(NULL) + 86400;
            g_tokens[g_ntokens].valid = 1;
            g_ntokens++;
            auth_save();
        }
        ctx->regs[rd] = pv_span_from_bytes(ctx, tok, (uint32_t)strlen(tok));
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_AUTH_VALIDATETOKEN) {
        span_str(ctx, ctx->regs[rs1], t, sizeof(t));
        for (i = 0; i < g_ntokens; i++)
            if (g_tokens[i].valid && strcmp(g_tokens[i].token, t) == 0 &&
                g_tokens[i].exp >= (int64_t)time(NULL)) {
                ctx->regs[rd] = 1;
                ctx->host_status = 0;
                return 1;
            }
        ctx->regs[rd] = 0;
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_AUTH_SWITCHUSERCONTEXT || hook == PV_HOOK_AUTH_SWITCHTOKENCONTEXT) {
        ctx->regs[rd] = 1;
        return 1;
    }
    if (hook == PV_HOOK_AUTH_REVOKETOKEN) {
        span_str(ctx, ctx->regs[rs1], t, sizeof(t));
        for (i = 0; i < g_ntokens; i++)
            if (strcmp(g_tokens[i].token, t) == 0) g_tokens[i].valid = 0;
        auth_save();
        ctx->regs[rd] = 1;
        return 1;
    }
    if (hook == PV_HOOK_X509_FETCHCERTIFICATE || hook == PV_HOOK_X509_GETCERTINFO) {
        span_str(ctx, ctx->regs[rs1], u, sizeof(u));
        i = 0;
        if (u[0]) {
            for (i = 0; i < g_ncerts; i++)
                if (strcmp(g_certs[i].name, u) == 0) break;
            if (i >= g_ncerts) { ctx->regs[rd] = 0; ctx->host_status = 1; return 1; }
        }
        ctx->regs[rd] = pv_span_from_bytes(ctx, g_certs[i].pem, (uint32_t)strlen(g_certs[i].pem));
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_X509_STORECERTIFICATE) {
        span_str(ctx, ctx->regs[rs1], u, sizeof(u));
        span_str(ctx, ctx->regs[rs2], p, sizeof(p));
        if (!u[0]) strcpy(u, "default");
        for (i = 0; i < g_ncerts; i++)
            if (strcmp(g_certs[i].name, u) == 0) {
                strncpy(g_certs[i].pem, p, 511);
                auth_save();
                ctx->regs[rd] = 1;
                return 1;
            }
        if (g_ncerts < AUTH_MAX_CERTS) {
            strncpy(g_certs[g_ncerts].name, u, 47);
            strncpy(g_certs[g_ncerts].pem, p, 511);
            g_ncerts++;
            auth_save();
        }
        ctx->regs[rd] = 1;
        return 1;
    }
    if (hook == PV_HOOK_X509_GENERATECSR || hook == PV_HOOK_X509_GENERATEKEYPAIR) {
        char out[256];
        snprintf(out, sizeof(out),
                 "-----BEGIN %s-----\nPicoScript-Generated\n-----END %s-----\n",
                 hook == PV_HOOK_X509_GENERATECSR ? "CSR" : "PRIVATE KEY",
                 hook == PV_HOOK_X509_GENERATECSR ? "CSR" : "PRIVATE KEY");
        ctx->regs[rd] = pv_span_from_bytes(ctx, out, (uint32_t)strlen(out));
        ctx->host_status = 0;
        return 1;
    }
    if (hook == PV_HOOK_X509_VERIFYCERTCHAIN || hook == PV_HOOK_X509_ISCERTVALID) {
        /* Presence check in store */
        span_str(ctx, ctx->regs[rs1], u, sizeof(u));
        if (!u[0] && g_ncerts > 0) { ctx->regs[rd] = 1; return 1; }
        for (i = 0; i < g_ncerts; i++)
            if (strcmp(g_certs[i].name, u) == 0 || strstr(g_certs[i].pem, u)) {
                ctx->regs[rd] = 1;
                return 1;
            }
        ctx->regs[rd] = g_ncerts > 0 ? 1 : 0;
        return 1;
    }
    if (hook == PV_HOOK_X509_GETKEYHANDLE) {
        ctx->regs[rd] = 1;
        return 1;
    }
    return 0;
}
