#include "storage_index_portable.h"
#include "pico_hooks.h"
#include <stdio.h>
#include <string.h>

int pv_storage_file_hook(pv_ctx *ctx,int hook,int rd,int rs1,int rs2)
{ (void)rs2; ctx->regs[rd]=(hook==0x1A9)?-1:(hook==0x68?ctx->regs[rs1]:0); return 1; }

static int call(pv_ctx *c,int hook,int a,int b)
{ c->regs[1]=a; c->regs[2]=b; c->regs[0]=0; pwf_portable_storage_hook(c,hook,0,1,2); return c->regs[0]; }

int main(void)
{
    pv_ctx c; unsigned char mem[4096]; const char *text="red battery pack"; const char *query="red battery";
    memset(&c,0,sizeof(c)); memset(mem,0,sizeof(mem)); c.mem=mem; c.mem_size=sizeof(mem); c.span_count=3;
    memcpy(mem+100,text,strlen(text)); c.span_ptr[1]=100; c.span_len[1]=(int)strlen(text);
    memcpy(mem+200,query,strlen(query)); c.span_ptr[2]=200; c.span_len[2]=(int)strlen(query);
    pwf_portable_indexes_init();
    if (call(&c,PV_HOOK_STORAGE_USEPACK,5,0)!=5) return 1;
    if (call(&c,PV_HOOK_STORAGE_FULLTEXTFIELD,0,0)!=0) return 2;
    if (c.regs[1]=42, c.regs[2]=1, !pwf_portable_storage_hook(&c,PV_HOOK_STORAGE_FULLTEXTUPSERT,0,1,2)) return 3;
    c.regs[1]=2; if (!pwf_portable_storage_hook(&c,PV_HOOK_STORAGE_FULLTEXTFIND,0,1,2) || c.regs[0]!=1) return 4;
    c.regs[1]=0; if (!pwf_portable_storage_hook(&c,PV_HOOK_STORAGE_FULLTEXTRESULT,0,1,2) || c.regs[0]!=42) return 5;
    if (call(&c,PV_HOOK_STORAGE_GRAPHRELATION,3,0)!=3) return 6;
    c.regs[1]=5; if (!pwf_portable_storage_hook(&c,PV_HOOK_STORAGE_GRAPHWEIGHTSET,0,1,2)) return 7;
    c.regs[1]=1; c.regs[2]=2; if (!pwf_portable_storage_hook(&c,PV_HOOK_STORAGE_GRAPHADD,0,1,2)) return 8;
    c.regs[1]=2; c.regs[2]=3; if (!pwf_portable_storage_hook(&c,PV_HOOK_STORAGE_GRAPHWEIGHTSET,0,1,2)) return 9;
    c.regs[1]=2; c.regs[2]=3; if (!pwf_portable_storage_hook(&c,PV_HOOK_STORAGE_GRAPHADD,0,1,2)) return 10;
    c.regs[1]=1; c.regs[2]=3; if (!pwf_portable_storage_hook(&c,PV_HOOK_STORAGE_GRAPHPATH,0,1,2) || c.regs[0]!=3) return 11;
    c.regs[1]=2; if (!pwf_portable_storage_hook(&c,PV_HOOK_STORAGE_GRAPHRESULTNODE,0,1,2) || c.regs[0]!=3) return 12;
    puts("portable index adapter: ok"); return 0;
}
