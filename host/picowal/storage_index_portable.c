#include "storage_index_portable.h"
#include "storage_file.h"
#include "portable/picowal_index.h"
#include "pico_hooks.h"
#include "picocompress.h"

#include <string.h>

#define PW_SCRIPT_MAX_KEYS 16384u
#define PW_SCRIPT_MAX_POSTINGS 65536u
#define PW_SCRIPT_MAX_RESULTS 4096u
#define PW_SCRIPT_MAX_TEXT 1024u
#define PW_SCRIPT_MAX_GRAPHS 8u
#define PW_SCRIPT_MAX_EDGES 512u
#define PW_SCRIPT_INDEX_PACK 4094
#define PW_SCRIPT_EVENT_MAX 4096u

static pw_index_t g_index;
static pw_index_key_t g_keys[PW_SCRIPT_MAX_KEYS];
static pw_index_posting_t g_postings[PW_SCRIPT_MAX_POSTINGS];
static uint32_t g_results[PW_SCRIPT_MAX_RESULTS];
static int32_t g_weights[PW_SCRIPT_MAX_RESULTS];
static uint32_t g_result_count;
static int32_t g_last_access = 5; /* 1 HASH, 2 ORDERED, 3 FULLTEXT, 4 GRAPH, 5 SCAN */
static uint32_t g_next_event;
static uint8_t g_loaded;
static uint8_t g_field;
static uint32_t g_mode;
static uint32_t g_selected_pack;
static int32_t g_graph_weight;
static uint8_t g_fts_cards[PW_SCRIPT_MAX_GRAPHS][PW_SCRIPT_MAX_EDGES][PW_SCRIPT_MAX_TEXT];
static uint16_t g_fts_lens[PW_SCRIPT_MAX_GRAPHS][PW_SCRIPT_MAX_EDGES];
static uint16_t g_fts_pack[PW_SCRIPT_MAX_GRAPHS];
static uint8_t g_fts_field[PW_SCRIPT_MAX_GRAPHS];

typedef struct {
    uint8_t used;
    int32_t pack, relation;
    uint16_t synthetic_pack;
    uint32_t edge_ids[PW_SCRIPT_MAX_EDGES];
    uint8_t edge_used[PW_SCRIPT_MAX_EDGES];
    uint8_t edge_cards[PW_SCRIPT_MAX_EDGES][20];
    uint16_t edge_lens[PW_SCRIPT_MAX_EDGES];
} pw_graph_state;
static pw_graph_state g_graphs[PW_SCRIPT_MAX_GRAPHS];
static uint16_t card_text(uint8_t *out, uint8_t field, const uint8_t *text, uint16_t len);
static void text_schema(pw_schema_t *schema, uint16_t pack, uint8_t field);
static pw_graph_state *graph_state(int32_t pack, int32_t relation, int create);

static void put16(uint8_t *p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void put32(uint8_t *p,uint32_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);p[2]=(uint8_t)(v>>16);p[3]=(uint8_t)(v>>24);}
static uint16_t get16(const uint8_t *p){return (uint16_t)p[0]|((uint16_t)p[1]<<8);}
static uint32_t get32(const uint8_t *p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}

static int make_span(pv_ctx *ctx,const uint8_t *data,uint32_t len)
{
    if (!ctx->mem || ctx->span_count>=PV_MAX_SPANS || (uint64_t)ctx->arena_top+len>ctx->mem_size) return 0;
    uint32_t p=ctx->arena_top; memcpy(ctx->mem+p,data,len); int h=ctx->span_count++;
    ctx->span_ptr[h]=p;ctx->span_len[h]=(int32_t)len;ctx->arena_top+=len;return h;
}

static void persist_event(pv_ctx *ctx,uint8_t kind,uint16_t pack,uint8_t field,uint32_t id,const uint8_t *card,uint16_t card_len)
{
    uint8_t raw[PW_SCRIPT_EVENT_MAX],comp[PW_SCRIPT_EVENT_MAX];size_t comp_len=0;
    if ((uint32_t)card_len+9u>sizeof(raw)) return;
    raw[0]=kind;put16(raw+1,pack);raw[3]=field;put32(raw+4,id);put16(raw+8,card_len);if(card_len)memcpy(raw+10,card,card_len);
    if (pc_compress_buffer(raw,(size_t)card_len+10u,comp,sizeof(comp),&comp_len)!=PC_OK) return;
    int saved1=ctx->regs[1],saved2=ctx->regs[2],saved0=ctx->regs[0];
    uint8_t packbuf[4];put32(packbuf,PW_SCRIPT_INDEX_PACK);int ph=make_span(ctx,packbuf,4);ctx->regs[1]=PW_SCRIPT_INDEX_PACK;ctx->regs[2]=0;pv_storage_file_hook(ctx,0x68,0,1,2);
    int h=make_span(ctx,comp,(uint32_t)comp_len);if(h){ctx->regs[1]=(int32_t)g_next_event++;ctx->regs[2]=h;pv_storage_file_hook(ctx,0x1A5,0,1,2);} (void)ph;
    ctx->regs[1]=saved1;ctx->regs[2]=saved2;ctx->regs[0]=saved0;
}

static void apply_event(const uint8_t *raw,uint32_t n)
{
    if(n<10u)return;uint8_t kind=raw[0];uint16_t pack=get16(raw+1);uint8_t field=raw[3];uint32_t id=get32(raw+4);uint16_t len=get16(raw+8);if((uint32_t)len+10u>n)return;
    if(kind<=2){int slot=-1,free_slot=-1;for(int i=0;i<(int)PW_SCRIPT_MAX_GRAPHS;i++){if(g_fts_pack[i]==pack&&g_fts_field[i]==field){slot=i;break;}if(!g_fts_pack[i]&&free_slot<0)free_slot=i;}if(slot<0)slot=free_slot;if(slot<0||id>=PW_SCRIPT_MAX_EDGES)return;g_fts_pack[slot]=pack;g_fts_field[slot]=field;pw_schema_t s;text_schema(&s,pack,field);uint8_t old[PW_SCRIPT_MAX_TEXT+6];uint16_t oldlen=0;if(g_fts_lens[slot][id])oldlen=card_text(old,field,g_fts_cards[slot][id],g_fts_lens[slot][id]);if(kind==1){(void)pw_index_card_update(&g_index,pack,id,&s,oldlen?old:NULL,oldlen,raw+10,len);memcpy(g_fts_cards[slot][id],raw+10,len>PW_SCRIPT_MAX_TEXT?PW_SCRIPT_MAX_TEXT:len);g_fts_lens[slot][id]=len>PW_SCRIPT_MAX_TEXT?PW_SCRIPT_MAX_TEXT:len;}else{(void)pw_index_card_update(&g_index,pack,id,&s,oldlen?old:raw+10,oldlen?oldlen:len,NULL,0);g_fts_lens[slot][id]=0;}return;}
    if(kind>=3){pw_graph_state *g=graph_state(pack,field,1);if(!g)return;int slot=(int)(id%PW_SCRIPT_MAX_EDGES);pw_schema_t s;memset(&s,0,sizeof(s));s.pack=g->synthetic_pack;s.field_count=3;for(int i=0;i<3;i++){s.fields[i].ordinal=(uint8_t)i;s.fields[i].type=0x06;}if(kind==3){(void)pw_index_card_update(&g_index,g->synthetic_pack,id,&s,g->edge_used[slot]?g->edge_cards[slot]:NULL,g->edge_used[slot]?g->edge_lens[slot]:0,raw+10,len);memcpy(g->edge_cards[slot],raw+10,len);g->edge_lens[slot]=len;g->edge_ids[slot]=id;g->edge_used[slot]=1;}else{(void)pw_index_card_update(&g_index,g->synthetic_pack,id,&s,g->edge_cards[slot],g->edge_lens[slot],NULL,0);g->edge_used[slot]=0;} }
}

static void ensure_loaded(pv_ctx *ctx)
{
    if(g_loaded)return;g_loaded=1;int saved1=ctx->regs[1],saved2=ctx->regs[2],saved0=ctx->regs[0];ctx->regs[1]=PW_SCRIPT_INDEX_PACK;ctx->regs[2]=0;pv_storage_file_hook(ctx,0x68,0,1,2);int32_t after=-1;
    for(;;){ctx->regs[1]=after;pv_storage_file_hook(ctx,0x1A9,0,1,2);int32_t id=ctx->regs[0];if(id<0)break;after=id;ctx->regs[1]=id;pv_storage_file_hook(ctx,0x1A6,0,1,2);int h=ctx->regs[0];if(h<=0||h>=ctx->span_count)continue;uint8_t raw[PW_SCRIPT_EVENT_MAX];size_t raw_len=0;uint32_t p=ctx->span_ptr[h];int32_t l=ctx->span_len[h];if(l>0&&pc_decompress_buffer(ctx->mem+p,(size_t)l,raw,sizeof(raw),&raw_len)==PC_OK)apply_event(raw,(uint32_t)raw_len);if((uint32_t)id>=g_next_event)g_next_event=(uint32_t)id+1;}
    ctx->regs[1]=saved1;ctx->regs[2]=saved2;ctx->regs[0]=saved0;
}

static uint16_t card_text(uint8_t *out, uint8_t field, const uint8_t *text, uint16_t len)
{
    if (len > 255u) len = 255u;
    out[0]=0x7d; out[1]=0xca; out[2]=1; out[3]=0;
    out[4]=field; out[5]=(uint8_t)len;
    if (len) memcpy(out+6, text, len);
    return (uint16_t)(6u + len);
}

static uint16_t card_edge(uint8_t *out, uint32_t source, uint32_t target, int32_t weight)
{
    out[0]=0x7d; out[1]=0xca; out[2]=1; out[3]=0;
    out[4]=0; out[5]=4; memcpy(out+6,&source,4);
    out[10]=1; out[11]=4; memcpy(out+12,&target,4);
    out[16]=2; out[17]=4; memcpy(out+18,&weight,4);
    return 22;
}

static void text_schema(pw_schema_t *schema, uint16_t pack, uint8_t field)
{
    memset(schema,0,sizeof(*schema)); schema->pack=pack; schema->field_count=1;
    schema->fields[0].ordinal=field; schema->fields[0].type=0x08;
}

static pw_graph_state *graph_state(int32_t pack, int32_t relation, int create)
{
    int free_slot=-1;
    for (int i=0;i<(int)PW_SCRIPT_MAX_GRAPHS;i++) {
        if (g_graphs[i].used && g_graphs[i].pack==pack && g_graphs[i].relation==relation) return &g_graphs[i];
        if (!g_graphs[i].used && free_slot<0) free_slot=i;
    }
    if (!create || free_slot<0) return NULL;
    pw_graph_state *g=&g_graphs[free_slot]; memset(g,0,sizeof(*g));
    g->used=1; g->pack=pack; g->relation=relation; g->synthetic_pack=(uint16_t)(3000+free_slot);
    pw_schema_t schema; memset(&schema,0,sizeof(schema)); schema.pack=g->synthetic_pack; schema.field_count=3;
    schema.fields[0].ordinal=0; schema.fields[0].type=0x06;
    schema.fields[1].ordinal=1; schema.fields[1].type=0x06;
    schema.fields[2].ordinal=2; schema.fields[2].type=0x06;
    pw_graph_def_t def={g->synthetic_pack,0,1,2,true};
    (void)pw_index_define_graph(&g_index,&def);
    return g;
}

void pwf_portable_indexes_init(void)
{
    pw_index_init(&g_index,g_keys,PW_SCRIPT_MAX_KEYS,g_postings,PW_SCRIPT_MAX_POSTINGS);
    memset(g_results,0,sizeof(g_results)); memset(g_weights,0,sizeof(g_weights));
    memset(g_fts_lens,0,sizeof(g_fts_lens)); memset(g_graphs,0,sizeof(g_graphs));
    memset(g_fts_pack,0,sizeof(g_fts_pack)); memset(g_fts_field,0,sizeof(g_fts_field));
    g_result_count=0; g_last_access=5; g_field=0; g_mode=0; g_next_event=0; g_loaded=0;
}

static int hook_fts(pv_ctx *ctx,int hook,int rd,int rs1,int rs2)
{
    int32_t doc=ctx->regs[rs1];
    int slot=-1, free_slot=-1;
    for (int i=0;i<(int)PW_SCRIPT_MAX_GRAPHS;i++) { if (g_fts_pack[i]==g_selected_pack && g_fts_field[i]==g_field) {slot=i;break;} if (!g_fts_pack[i] && free_slot<0) free_slot=i; }
    if (slot<0 && free_slot>=0) {slot=free_slot;g_fts_pack[slot]=(uint16_t)g_selected_pack;g_fts_field[slot]=g_field;}
    if (slot<0 || slot>=(int)PW_SCRIPT_MAX_GRAPHS || doc<0 || doc>=(int)PW_SCRIPT_MAX_EDGES) return 0;
    pw_schema_t schema; text_schema(&schema,(uint16_t)(2000+slot),g_field);
    uint8_t card[PW_SCRIPT_MAX_TEXT+6], old[PW_SCRIPT_MAX_TEXT+6]; uint16_t len=0,oldlen=0;
    if (hook==PV_HOOK_STORAGE_FULLTEXTUPSERT) {
        int h=ctx->regs[rs2]; uint32_t p=(h>0&&h<ctx->span_count)?ctx->span_ptr[h]:0; int32_t n=(h>0&&h<ctx->span_count)?ctx->span_len[h]:0;
        if (n<0) n=0; if (n>(int32_t)PW_SCRIPT_MAX_TEXT) n=PW_SCRIPT_MAX_TEXT;
        len=card_text(card,g_field,ctx->mem+p,(uint16_t)n);
        if (g_fts_lens[slot][doc]) { oldlen=card_text(old,g_field,g_fts_cards[slot][doc],g_fts_lens[slot][doc]); }
        (void)pw_index_card_update(&g_index,(uint16_t)(2000+slot),(uint32_t)doc,&schema,
                                   oldlen?old:NULL,oldlen,card,len);
        memcpy(g_fts_cards[slot][doc],ctx->mem+p,(size_t)n); g_fts_lens[slot][doc]=(uint16_t)n; persist_event(ctx,1,(uint16_t)g_selected_pack,g_field,(uint32_t)doc,card,len); ctx->regs[rd]=1; return 1;
    }
    if (hook==PV_HOOK_STORAGE_FULLTEXTDELETE) {
        if (!g_fts_lens[slot][doc]) {ctx->regs[rd]=0;return 1;}
        oldlen=card_text(old,g_field,g_fts_cards[slot][doc],g_fts_lens[slot][doc]);
        (void)pw_index_card_update(&g_index,(uint16_t)(2000+slot),(uint32_t)doc,&schema,old,oldlen,NULL,0);
        persist_event(ctx,2,(uint16_t)g_selected_pack,g_field,(uint32_t)doc,old,oldlen); g_fts_lens[slot][doc]=0; ctx->regs[rd]=1; return 1;
    }
    if (hook==PV_HOOK_STORAGE_FULLTEXTFIND) {
        int h=ctx->regs[rs1]; uint32_t p=(h>0&&h<ctx->span_count)?ctx->span_ptr[h]:0; uint32_t n=(h>0&&h<ctx->span_count)?(uint32_t)ctx->span_len[h]:0;
        g_result_count=pw_index_text_all(&g_index,(uint16_t)(2000+slot),g_field,(const char *)(ctx->mem+p),n,g_results,PW_SCRIPT_MAX_RESULTS); ctx->regs[rd]=(int32_t)g_result_count; return 1;
    }
    if (hook==PV_HOOK_STORAGE_FULLTEXTRESULT) {int i=ctx->regs[rs1];ctx->regs[rd]=(i>=0&&(uint32_t)i<g_result_count)?(int32_t)g_results[i]:-1;return 1;}
    return 0;
}

static int hook_graph(pv_ctx *ctx,int hook,int rd,int rs1,int rs2)
{
    pw_graph_state *g=graph_state((int32_t)g_selected_pack,(int32_t)g_field,hook!=PV_HOOK_STORAGE_GRAPHRESULTNODE&&hook!=PV_HOOK_STORAGE_GRAPHRESULTWEIGHT);
    if (!g) return 0;
    uint32_t source=(uint32_t)ctx->regs[rs1], target=(uint32_t)ctx->regs[rs2];
    /* GraphRelation is selected by the superinstruction before this hook; the
       provider receives relation in g_field and edge endpoints in rs1/rs2. */
    if (hook==PV_HOOK_STORAGE_GRAPHADD || hook==PV_HOOK_STORAGE_GRAPHWEIGHTSET) {
        uint32_t s=(uint32_t)ctx->regs[rs1], d=(uint32_t)ctx->regs[rs2]; int32_t w=g_graph_weight;
        int slot=(int)((s*2654435761u+d)%PW_SCRIPT_MAX_EDGES); uint8_t card[22]; uint16_t len=card_edge(card,s,d,w);
        pw_schema_t schema; memset(&schema,0,sizeof(schema)); schema.pack=g->synthetic_pack; schema.field_count=3;
        schema.fields[0].ordinal=0; schema.fields[0].type=0x06; schema.fields[1].ordinal=1; schema.fields[1].type=0x06; schema.fields[2].ordinal=2; schema.fields[2].type=0x06;
        (void)pw_index_card_update(&g_index,g->synthetic_pack,(uint32_t)(slot+1),&schema,NULL,0,card,len);
        memcpy(g->edge_cards[slot],card,len);g->edge_lens[slot]=len;g->edge_ids[slot]=(uint32_t)(slot+1);g->edge_used[slot]=1;persist_event(ctx,3,(uint16_t)g->pack,(uint8_t)g->relation,g->edge_ids[slot],card,len);ctx->regs[rd]=1;return 1;
    }
    if (hook==PV_HOOK_STORAGE_GRAPHDELETE) {
        uint32_t s=(uint32_t)ctx->regs[rs1], d=(uint32_t)ctx->regs[rs2];
        int slot=(int)((s*2654435761u+d)%PW_SCRIPT_MAX_EDGES);
        if (!g->edge_used[slot]) { ctx->regs[rd]=0; return 1; }
        pw_schema_t schema; memset(&schema,0,sizeof(schema)); schema.pack=g->synthetic_pack; schema.field_count=3;
        schema.fields[0].ordinal=0; schema.fields[0].type=0x06; schema.fields[1].ordinal=1; schema.fields[1].type=0x06; schema.fields[2].ordinal=2; schema.fields[2].type=0x06;
        (void)pw_index_card_update(&g_index,g->synthetic_pack,g->edge_ids[slot],&schema,g->edge_cards[slot],g->edge_lens[slot],NULL,0);
        persist_event(ctx,4,(uint16_t)g->pack,(uint8_t)g->relation,g->edge_ids[slot],g->edge_cards[slot],g->edge_lens[slot]); g->edge_used[slot]=0; g->edge_lens[slot]=0; ctx->regs[rd]=1; return 1;
    }
    if (hook==PV_HOOK_STORAGE_GRAPHWEIGHT) {
        uint32_t nn[PW_SCRIPT_MAX_RESULTS]; int32_t ww[PW_SCRIPT_MAX_RESULTS];
        uint32_t n=pw_index_graph_neighbors(&g_index,g->synthetic_pack,source,false,nn,ww,PW_SCRIPT_MAX_RESULTS);
        for (uint32_t i=0;i<n;i++) if (nn[i]==target) { ctx->regs[rd]=ww[i]; return 1; }
        ctx->regs[rd]=0; return 1;
    }
    if (hook==PV_HOOK_STORAGE_GRAPHOUT) {
        uint32_t n=pw_index_graph_neighbors(&g_index,g->synthetic_pack,source,ctx->regs[rs2]!=0,g_results,g_weights,PW_SCRIPT_MAX_RESULTS);g_result_count=n;ctx->regs[rd]=(int32_t)n;return 1;
    }
    if (hook==PV_HOOK_STORAGE_GRAPHRESULTNODE||hook==PV_HOOK_STORAGE_GRAPHRESULTWEIGHT) {int i=ctx->regs[rs1];ctx->regs[rd]=(i>=0&&(uint32_t)i<g_result_count)?(hook==PV_HOOK_STORAGE_GRAPHRESULTNODE?(int32_t)g_results[i]:g_weights[i]):-1;return 1;}
    if (hook==PV_HOOK_STORAGE_GRAPHPATH) {
        uint32_t path[PW_SCRIPT_MAX_RESULTS],nodes[PW_SCRIPT_MAX_RESULTS],parents[PW_SCRIPT_MAX_RESULTS];int64_t dist[PW_SCRIPT_MAX_RESULTS];uint8_t closed[PW_SCRIPT_MAX_RESULTS];uint32_t cap=PW_SCRIPT_MAX_RESULTS;pw_graph_result_t result;
        int ok=pw_index_graph_shortest_path(&g_index,g->synthetic_pack,source,target,path,&cap,nodes,dist,parents,closed,PW_SCRIPT_MAX_RESULTS,&result);g_result_count=ok?cap:0;
        ctx->host_status = result.complete ? 0 : 3;
        if(ok) { int64_t total=0; for(uint32_t i=0;i<cap;i++){g_results[i]=path[i];g_weights[i]=(int32_t)total;if(i+1<cap){uint32_t nn[PW_SCRIPT_MAX_RESULTS];int32_t ww[PW_SCRIPT_MAX_RESULTS];uint32_t n=pw_index_graph_neighbors(&g_index,g->synthetic_pack,path[i],false,nn,ww,PW_SCRIPT_MAX_RESULTS);for(uint32_t j=0;j<n;j++)if(nn[j]==path[i+1]){total+=ww[j];break;}}} }
        ctx->regs[rd]=(int32_t)g_result_count;return 1;
    }
    return 0;
}

/* Db query façade: feed the existing PicoWAL index/query substrate.  The
 * structured compiler ABI supplies handles in the same two-input/one-output
 * registers; the first implementation keeps query state in this adapter's
 * deterministic result buffer and preserves Storage.QueryCard compatibility. */
static int hook_db_query(pv_ctx *ctx, int hook, int rd, int rs1, int rs2)
{
    if (hook == PV_HOOK_DB_QUERY) {
        /* Query handle currently carries a legacy query-span handle.  This
         * deliberately reuses the Storage parser until the structured plan
         * serializer is enabled by the host. */
        g_last_access = 5;
        return pv_storage_file_hook(ctx, 0x67, rd, rs1, rs2);
    }
    if (hook == PV_HOOK_DB_NEXT) {
        return pv_storage_file_hook(ctx, 0x6E, rd, rs1, rs2);
    }
    if (hook == PV_HOOK_DB_BATCH || hook == PV_HOOK_DB_MATERIALIZE) {
        /* Existing result buffer is already bounded and deterministic.  Batch
         * and materialize expose the same result count for this ABI revision;
         * continuation remains the result index in rs1. */
        int32_t start = ctx->regs[rs1], count = ctx->regs[rs2];
        if (start < 0) start = 0;
        if (count < 0) count = 0;
        if ((uint32_t)start > g_result_count) start = (int32_t)g_result_count;
        if ((uint32_t)count > g_result_count - (uint32_t)start)
            count = (int32_t)(g_result_count - (uint32_t)start);
        ctx->regs[rd] = count;
        return 1;
    }
    if (hook == PV_HOOK_DB_PLAN) {
        ctx->regs[rd] = g_last_access;
        return 1;
    }
    if (hook == PV_HOOK_DB_SEEK) {
        uint8_t value[4];
        put32(value, (uint32_t)ctx->regs[rs2]);
        g_result_count = pw_index_exact(&g_index, (uint16_t)g_selected_pack,
                                        (uint8_t)ctx->regs[rs1], value, 4,
                                        g_results, PW_SCRIPT_MAX_RESULTS);
        g_last_access = 1;
        ctx->regs[rd] = (int32_t)g_result_count;
        return 1;
    }
    return 0;
}
int pwf_portable_index_hook(pv_ctx *ctx,int hook,int rd,int rs1,int rs2)
{
    if (hook >= PV_HOOK_DB_SEEK && hook <= PV_HOOK_DB_PLAN) {
        ensure_loaded(ctx);
        if (hook_db_query(ctx,hook,rd,rs1,rs2)) return 1;
    }
    if (hook==PV_HOOK_STORAGE_USEPACK) { g_selected_pack=(uint32_t)ctx->regs[rs1]; return 0; }
    if (hook!=PV_HOOK_STORAGE_USEPACK) ensure_loaded(ctx);
    if (hook==PV_HOOK_STORAGE_FULLTEXTFIELD) {g_field=(uint8_t)ctx->regs[rs1];ctx->regs[rd]=g_field;return 1;}
    if (hook==PV_HOOK_STORAGE_FULLTEXTMODE) {g_mode=(uint32_t)ctx->regs[rs1];ctx->regs[rd]=1;return 1;}
    if (hook>=PV_HOOK_STORAGE_FULLTEXTUPSERT&&hook<=PV_HOOK_STORAGE_FULLTEXTRESULT) return hook_fts(ctx,hook,rd,rs1,rs2);
    if (hook==PV_HOOK_STORAGE_GRAPHRELATION) {g_field=(uint8_t)ctx->regs[rs1];ctx->regs[rd]=g_field;return 1;}
    if (hook==PV_HOOK_STORAGE_GRAPHWEIGHTSET) {g_graph_weight=ctx->regs[rs1];ctx->regs[rd]=1;return 1;}
    if (hook>=PV_HOOK_STORAGE_GRAPHWEIGHTSET&&hook<=PV_HOOK_STORAGE_GRAPHPATH) return hook_graph(ctx,hook,rd,rs1,rs2);
    return 0;
}

int pwf_portable_storage_hook(pv_ctx *ctx,int hook,int rd,int rs1,int rs2)
{
    if (pwf_portable_index_hook(ctx,hook,rd,rs1,rs2)) return 1;
    return pv_storage_file_hook(ctx,hook,rd,rs1,rs2);
}
