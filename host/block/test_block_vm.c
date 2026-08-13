#include "pv_block_vm.h"

#include <stdio.h>
#include <string.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL %s:%d: %s\n",__FILE__,__LINE__,#x); return 1; } } while (0)

static uint8_t image[8 * 512];
static uint8_t scratch[512];
static uint8_t arena[64 * 1024];
static int rd(void *c,uint64_t l,void *p,uint32_t n){(void)c;if(l+n>8)return-1;memcpy(p,image+(size_t)l*512,(size_t)n*512);return 0;}
static int wr(void *c,uint64_t l,const void *p,uint32_t n){(void)c;if(l+n>8)return-1;memcpy(image+(size_t)l*512,p,(size_t)n*512);return 0;}

int main(void)
{
    pv_rp2350_backend be = {0};
    pv_block_dev dev;
    pv_ctx ctx;
    int span;
    be.block_size=512; be.block_count=8; be.read_blocks=rd; be.write_blocks=wr;
    be.scratch=scratch; be.scratch_size=sizeof(scratch);
    pv_rp2350_install(&be);
    CHECK(pv_block_open(&dev,pv_block_ops_rp2350(),"flash0",PV_BLOCK_READ|PV_BLOCK_WRITE,0)==0);
    pv_init(&ctx); ctx.mem=arena; ctx.mem_size=sizeof(arena); ctx.caps=PV_CAP_ALL;
    pv_block_vm_install(); pv_block_vm_bind(&ctx,&dev);
    memcpy(image+20,"PICO",4);
    ctx.regs[0]=20; ctx.regs[1]=0;
    pv_default_host(&ctx,PV_HOOK_BLOCK_SETOFFSET,0,0,1,0);
    ctx.regs[2]=4;
    pv_default_host(&ctx,PV_HOOK_BLOCK_READ,3,2,0,0);
    span=ctx.regs[3];
    CHECK(span>0 && ctx.span_len[span]==4);
    CHECK(memcmp(ctx.mem+ctx.span_ptr[span],"PICO",4)==0);
    CHECK(ctx.block_status==PV_BLOCK_OK);
    pv_block_close(&dev); pv_rp2350_install(NULL);
    puts("PASS native VM Block hooks");
    return 0;
}
