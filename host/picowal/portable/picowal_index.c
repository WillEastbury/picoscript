#include "picowal_index.h"

#include <string.h>

#define PW_CARD_MAGIC_LO 0x7du
#define PW_CARD_MAGIC_HI 0xcau
#define PW_POST_ANY UINT32_MAX

typedef struct {
    const uint8_t *data;
    uint8_t len;
} field_view_t;

uint32_t pw_index_hash(const void *data, uint32_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < len; i++) {
        h ^= p[i];
        h *= 16777619u;
    }
    return h;
}

static uint32_t key_hash(uint8_t kind, uint16_t pack, uint8_t field,
                         const uint8_t *value, uint8_t value_len)
{
    uint8_t prefix[4] = { kind, (uint8_t)pack, (uint8_t)(pack >> 8), field };
    uint32_t h = pw_index_hash(prefix, sizeof(prefix));
    for (uint8_t i = 0; i < value_len; i++) {
        h ^= value[i];
        h *= 16777619u;
    }
    return h ? h : 1u;
}

static bool key_equal(const pw_index_key_t *key, uint8_t kind, uint16_t pack,
                      uint8_t field, const uint8_t *value, uint8_t value_len,
                      uint32_t hash)
{
    uint8_t stored = value_len > PW_INDEX_VALUE_MAX ? PW_INDEX_VALUE_MAX : value_len;
    return key->used == 1 && key->hash == hash && key->kind == kind &&
           key->pack == pack && key->field == field && key->value_len == value_len &&
           (stored == 0 || memcmp(key->value, value, stored) == 0);
}

static int32_t key_slot(const pw_index_t *index, uint8_t kind, uint16_t pack,
                        uint8_t field, const uint8_t *value, uint8_t value_len,
                        bool create)
{
    if (!index || !index->keys || index->key_capacity == 0) return -1;
    uint32_t hash = key_hash(kind, pack, field, value, value_len);
    uint32_t at = hash % index->key_capacity;
    int32_t tombstone = -1;
    for (uint32_t probe = 0; probe < index->key_capacity; probe++) {
        pw_index_key_t *key = &index->keys[at];
        if (key->used == 0) {
            if (!create) return -1;
            return tombstone >= 0 ? tombstone : (int32_t)at;
        }
        if (key->used == 2) {
            if (tombstone < 0) tombstone = (int32_t)at;
        } else if (key_equal(key, kind, pack, field, value, value_len, hash)) {
            return (int32_t)at;
        }
        at = (at + 1u) % index->key_capacity;
    }
    return create ? tombstone : -1;
}

static void init_key(pw_index_key_t *key, uint8_t kind, uint16_t pack,
                     uint8_t field, const uint8_t *value, uint8_t value_len)
{
    memset(key, 0, sizeof(*key));
    key->hash = key_hash(kind, pack, field, value, value_len);
    key->head = PW_INDEX_NONE;
    key->pack = pack;
    key->field = field;
    key->kind = kind;
    key->value_len = value_len;
    key->used = 1;
    uint8_t n = value_len > PW_INDEX_VALUE_MAX ? PW_INDEX_VALUE_MAX : value_len;
    if (n) memcpy(key->value, value, n);
}

static uint32_t posting_alloc(pw_index_t *index)
{
    if (!index || index->free_posting == PW_INDEX_NONE) return PW_INDEX_NONE;
    uint32_t id = index->free_posting;
    index->free_posting = index->postings[id].next;
    memset(&index->postings[id], 0, sizeof(index->postings[id]));
    index->postings[id].next = PW_INDEX_NONE;
    index->postings[id].used = 1;
    return id;
}

static void posting_free(pw_index_t *index, uint32_t id)
{
    if (!index || id >= index->posting_capacity) return;
    memset(&index->postings[id], 0, sizeof(index->postings[id]));
    index->postings[id].next = index->free_posting;
    index->free_posting = id;
}

static bool posting_add(pw_index_t *index, uint8_t kind, uint16_t pack,
                        uint8_t field, const uint8_t *value, uint8_t value_len,
                        uint32_t record, uint32_t related, int32_t weight)
{
    int32_t slot = key_slot(index, kind, pack, field, value, value_len, true);
    if (slot < 0) { index->complete = false; return false; }
    pw_index_key_t *key = &index->keys[(uint32_t)slot];
    if (key->used != 1) init_key(key, kind, pack, field, value, value_len);
    for (uint32_t p = key->head; p != PW_INDEX_NONE; p = index->postings[p].next) {
        pw_index_posting_t *post = &index->postings[p];
        if (post->record == record && post->related == related) {
            post->weight = weight;
            return true;
        }
    }
    uint32_t id = posting_alloc(index);
    if (id == PW_INDEX_NONE) { index->complete = false; return false; }
    index->postings[id].record = record;
    index->postings[id].related = related;
    index->postings[id].weight = weight;
    index->postings[id].next = key->head;
    key->head = id;
    key->count++;
    return true;
}

static void posting_remove(pw_index_t *index, uint8_t kind, uint16_t pack,
                           uint8_t field, const uint8_t *value, uint8_t value_len,
                           uint32_t record, uint32_t related)
{
    int32_t slot = key_slot(index, kind, pack, field, value, value_len, false);
    if (slot < 0) return;
    pw_index_key_t *key = &index->keys[(uint32_t)slot];
    uint32_t *link = &key->head;
    while (*link != PW_INDEX_NONE) {
        uint32_t id = *link;
        pw_index_posting_t *post = &index->postings[id];
        if (post->record == record && (related == PW_POST_ANY || post->related == related)) {
            *link = post->next;
            posting_free(index, id);
            if (key->count) key->count--;
            if (related != PW_POST_ANY) break;
        } else {
            link = &post->next;
        }
    }
    if (key->count == 0) {
        memset(key, 0, sizeof(*key));
        key->used = 2;
        key->head = PW_INDEX_NONE;
    }
}

void pw_index_init(pw_index_t *index,
                   pw_index_key_t *keys, uint32_t key_capacity,
                   pw_index_posting_t *postings, uint32_t posting_capacity)
{
    if (!index) return;
    memset(index, 0, sizeof(*index));
    index->keys = keys;
    index->key_capacity = key_capacity;
    index->postings = postings;
    index->posting_capacity = posting_capacity;
    pw_index_reset(index);
}

void pw_index_reset(pw_index_t *index)
{
    if (!index) return;
    if (index->keys && index->key_capacity)
        memset(index->keys, 0, index->key_capacity * sizeof(index->keys[0]));
    if (index->postings && index->posting_capacity) {
        memset(index->postings, 0, index->posting_capacity * sizeof(index->postings[0]));
        for (uint32_t i = 0; i < index->posting_capacity; i++)
            index->postings[i].next = i + 1u < index->posting_capacity ? i + 1u : PW_INDEX_NONE;
        index->free_posting = index->posting_capacity ? 0u : PW_INDEX_NONE;
    } else {
        index->free_posting = PW_INDEX_NONE;
    }
    index->complete = index->keys && index->key_capacity &&
                      index->postings && index->posting_capacity;
}

bool pw_index_define_graph(pw_index_t *index, const pw_graph_def_t *definition)
{
    if (!index || !definition || index->graph_count >= PW_INDEX_MAX_GRAPHS) return false;
    for (uint8_t i = 0; i < index->graph_count; i++) {
        if (index->graphs[i].edge_pack == definition->edge_pack) {
            index->graphs[i] = *definition;
            return true;
        }
    }
    index->graphs[index->graph_count++] = *definition;
    return true;
}

static bool card_field(const uint8_t *card, uint16_t len, uint8_t ordinal,
                       field_view_t *out)
{
    if (out) { out->data = NULL; out->len = 0; }
    if (!card || len < 4 || card[0] != PW_CARD_MAGIC_LO || card[1] != PW_CARD_MAGIC_HI)
        return false;
    uint16_t off = 4;
    while (off + 2u <= len) {
        uint8_t ord = card[off] & 0x1fu;
        uint8_t flen = card[off + 1u];
        off += 2u;
        if ((uint32_t)off + flen > len) return false;
        if (ord == ordinal) {
            if (out) { out->data = card + off; out->len = flen; }
            return true;
        }
        off += flen;
    }
    return false;
}

static field_view_t logical_text(field_view_t raw)
{
    if (raw.len > 0 && raw.data[0] == (uint8_t)(raw.len - 1u)) {
        raw.data++;
        raw.len--;
    }
    return raw;
}

static bool text_type(uint8_t type)
{
    return type == 0x08u || type == 0x09u;
}

static bool numeric_type(uint8_t type)
{
    return (type >= 0x01u && type <= 0x06u) || type == 0x07u || type == 0x12u;
}

static uint32_t field_u32(field_view_t value)
{
    uint32_t out = 0;
    uint8_t n = value.len > 4u ? 4u : value.len;
    for (uint8_t i = 0; i < n; i++) out |= (uint32_t)value.data[i] << (i * 8u);
    return out;
}

static int32_t field_i32(field_view_t value, uint8_t type)
{
    uint32_t raw = field_u32(value);
    if (type == 0x04u) return (int32_t)(int8_t)raw;
    if (type == 0x05u) return (int32_t)(int16_t)raw;
    return (int32_t)raw;
}

typedef void (*token_cb_t)(void *ctx, const uint8_t *token, uint8_t len);

static void tokenize(field_view_t text, token_cb_t cb, void *ctx)
{
    uint8_t token[PW_INDEX_VALUE_MAX];
    uint8_t n = 0;
    for (uint16_t i = 0; i <= text.len; i++) {
        uint8_t c = i < text.len ? text.data[i] : 0;
        bool word = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                    (c >= '0' && c <= '9') || c >= 0x80u;
        if (word) {
            if (c >= 'A' && c <= 'Z') c = (uint8_t)(c + ('a' - 'A'));
            if (n < sizeof(token)) token[n++] = c;
        } else if (n) {
            if (n >= 2u) cb(ctx, token, n);
            n = 0;
        }
    }
}

typedef struct {
    pw_index_t *index;
    uint16_t pack;
    uint8_t field;
    uint32_t record;
    bool add;
} token_update_t;

static void update_token(void *opaque, const uint8_t *token, uint8_t len)
{
    token_update_t *u = (token_update_t *)opaque;
    if (u->add)
        posting_add(u->index, PW_INDEX_FULLTEXT, u->pack, u->field,
                    token, len, u->record, 0, 0);
    else
        posting_remove(u->index, PW_INDEX_FULLTEXT, u->pack, u->field,
                       token, len, u->record, PW_POST_ANY);
}

static void update_field(pw_index_t *index, uint16_t pack, uint32_t record,
                         const pw_schema_field_t *field,
                         field_view_t value, bool add)
{
    if (text_type(field->type)) value = logical_text(value);
    if (add)
        posting_add(index, PW_INDEX_EXACT, pack, field->ordinal,
                    value.data, value.len, record, 0, 0);
    else
        posting_remove(index, PW_INDEX_EXACT, pack, field->ordinal,
                       value.data, value.len, record, PW_POST_ANY);

    if (field->type == 0x12u) {
        uint32_t target = field_u32(value);
        uint8_t encoded[4] = { (uint8_t)target, (uint8_t)(target >> 8),
                               (uint8_t)(target >> 16), (uint8_t)(target >> 24) };
        if (add)
            posting_add(index, PW_INDEX_REVERSE_LOOKUP, pack, field->ordinal,
                        encoded, sizeof(encoded), record, target, 0);
        else
            posting_remove(index, PW_INDEX_REVERSE_LOOKUP, pack, field->ordinal,
                           encoded, sizeof(encoded), record, PW_POST_ANY);
    }
    if (text_type(field->type)) {
        token_update_t update = { index, pack, field->ordinal, record, add };
        tokenize(value, update_token, &update);
    }
}

static const pw_schema_field_t *schema_field(const pw_schema_t *schema, uint8_t ordinal)
{
    if (!schema) return NULL;
    for (uint8_t i = 0; i < schema->field_count; i++)
        if (schema->fields[i].ordinal == ordinal) return &schema->fields[i];
    return NULL;
}

static void graph_update(pw_index_t *index, const pw_graph_def_t *graph,
                         uint32_t edge_record, const pw_schema_t *schema,
                         const uint8_t *card, uint16_t len, bool add)
{
    field_view_t srcv, dstv, weightv;
    if (!card_field(card, len, graph->source_ordinal, &srcv) ||
        !card_field(card, len, graph->target_ordinal, &dstv)) return;
    uint32_t source = field_u32(srcv);
    uint32_t target = field_u32(dstv);
    int32_t weight = 1;
    const pw_schema_field_t *wf = schema_field(schema, graph->weight_ordinal);
    if (wf && numeric_type(wf->type) && card_field(card, len, graph->weight_ordinal, &weightv))
        weight = field_i32(weightv, wf->type);
    uint8_t skey[4] = { (uint8_t)source, (uint8_t)(source >> 8),
                        (uint8_t)(source >> 16), (uint8_t)(source >> 24) };
    uint8_t tkey[4] = { (uint8_t)target, (uint8_t)(target >> 8),
                        (uint8_t)(target >> 16), (uint8_t)(target >> 24) };
    if (add) {
        posting_add(index, PW_INDEX_GRAPH_FORWARD, graph->edge_pack,
                    graph->source_ordinal, skey, sizeof(skey), edge_record, target, weight);
        posting_add(index, PW_INDEX_GRAPH_REVERSE, graph->edge_pack,
                    graph->target_ordinal, tkey, sizeof(tkey), edge_record, source, weight);
        if (!graph->directed) {
            posting_add(index, PW_INDEX_GRAPH_FORWARD, graph->edge_pack,
                        graph->source_ordinal, tkey, sizeof(tkey), edge_record, source, weight);
            posting_add(index, PW_INDEX_GRAPH_REVERSE, graph->edge_pack,
                        graph->target_ordinal, skey, sizeof(skey), edge_record, target, weight);
        }
    } else {
        posting_remove(index, PW_INDEX_GRAPH_FORWARD, graph->edge_pack,
                       graph->source_ordinal, skey, sizeof(skey), edge_record, target);
        posting_remove(index, PW_INDEX_GRAPH_REVERSE, graph->edge_pack,
                       graph->target_ordinal, tkey, sizeof(tkey), edge_record, source);
        if (!graph->directed) {
            posting_remove(index, PW_INDEX_GRAPH_FORWARD, graph->edge_pack,
                           graph->source_ordinal, tkey, sizeof(tkey), edge_record, source);
            posting_remove(index, PW_INDEX_GRAPH_REVERSE, graph->edge_pack,
                           graph->target_ordinal, skey, sizeof(skey), edge_record, target);
        }
    }
}

bool pw_index_card_update(pw_index_t *index, uint16_t pack, uint32_t record,
                          const pw_schema_t *schema,
                          const uint8_t *old_card, uint16_t old_len,
                          const uint8_t *new_card, uint16_t new_len)
{
    if (!index || !schema || schema->pack != pack) return false;
    bool ok = true;
    for (uint8_t i = 0; i < schema->field_count; i++) {
        const pw_schema_field_t *field = &schema->fields[i];
        field_view_t oldv = {0}, newv = {0};
        bool have_old = card_field(old_card, old_len, field->ordinal, &oldv);
        bool have_new = card_field(new_card, new_len, field->ordinal, &newv);
        if (have_old && have_new && oldv.len == newv.len &&
            (oldv.len == 0 || memcmp(oldv.data, newv.data, oldv.len) == 0)) continue;
        if (have_old) update_field(index, pack, record, field, oldv, false);
        if (have_new) update_field(index, pack, record, field, newv, true);
        if (!index->complete) ok = false;
    }
    for (uint8_t i = 0; i < index->graph_count; i++) {
        if (index->graphs[i].edge_pack != pack) continue;
        if (old_card) graph_update(index, &index->graphs[i], record, schema, old_card, old_len, false);
        if (new_card) graph_update(index, &index->graphs[i], record, schema, new_card, new_len, true);
    }
    return ok;
}

static uint32_t collect(const pw_index_t *index, uint8_t kind, uint16_t pack,
                        uint8_t field, const uint8_t *value, uint8_t value_len,
                        uint32_t *out, uint32_t max)
{
    if (!index || !out || max == 0) return 0;
    int32_t slot = key_slot(index, kind, pack, field, value, value_len, false);
    if (slot < 0) return 0;
    uint32_t n = 0;
    for (uint32_t p = index->keys[(uint32_t)slot].head;
         p != PW_INDEX_NONE && n < max; p = index->postings[p].next)
        out[n++] = index->postings[p].record;
    return n;
}

uint32_t pw_index_exact(const pw_index_t *index, uint16_t pack, uint8_t field,
                        const uint8_t *value, uint8_t value_len,
                        uint32_t *out_records, uint32_t max_records)
{
    return collect(index, PW_INDEX_EXACT, pack, field, value, value_len,
                   out_records, max_records);
}

uint32_t pw_index_reverse_lookup(const pw_index_t *index, uint16_t source_pack,
                                 uint8_t field, uint32_t target_record,
                                 uint32_t *out_records, uint32_t max_records)
{
    uint8_t value[4] = { (uint8_t)target_record, (uint8_t)(target_record >> 8),
                         (uint8_t)(target_record >> 16), (uint8_t)(target_record >> 24) };
    return collect(index, PW_INDEX_REVERSE_LOOKUP, source_pack, field, value,
                   sizeof(value), out_records, max_records);
}

static bool term_has_record(const pw_index_t *index, uint16_t pack, uint8_t field,
                            const uint8_t *term, uint8_t len, uint32_t record)
{
    int32_t slot = key_slot(index, PW_INDEX_FULLTEXT, pack, field, term, len, false);
    if (slot < 0) return false;
    for (uint32_t p = index->keys[(uint32_t)slot].head;
         p != PW_INDEX_NONE; p = index->postings[p].next)
        if (index->postings[p].record == record) return true;
    return false;
}

typedef struct {
    const pw_index_t *index;
    uint16_t pack;
    uint8_t field;
    uint32_t *out;
    uint32_t max;
    uint32_t count;
    uint8_t terms;
} text_query_t;

static void query_token(void *opaque, const uint8_t *token, uint8_t len)
{
    text_query_t *q = (text_query_t *)opaque;
    if (q->terms == 0) {
        q->count = collect(q->index, PW_INDEX_FULLTEXT, q->pack, q->field,
                           token, len, q->out, q->max);
    } else {
        uint32_t write = 0;
        for (uint32_t i = 0; i < q->count; i++) {
            if (term_has_record(q->index, q->pack, q->field, token, len, q->out[i]))
                q->out[write++] = q->out[i];
        }
        q->count = write;
    }
    q->terms++;
}

uint32_t pw_index_text_all(const pw_index_t *index, uint16_t pack, uint8_t field,
                           const char *query, uint32_t query_len,
                           uint32_t *out_records, uint32_t max_records)
{
    if (!query || !out_records || !max_records) return 0;
    field_view_t view = { (const uint8_t *)query,
                          (uint8_t)(query_len > 255u ? 255u : query_len) };
    text_query_t state = { index, pack, field, out_records, max_records, 0, 0 };
    tokenize(view, query_token, &state);
    return state.terms ? state.count : 0;
}

static const pw_graph_def_t *find_graph(const pw_index_t *index, uint16_t edge_pack)
{
    if (!index) return NULL;
    for (uint8_t i = 0; i < index->graph_count; i++)
        if (index->graphs[i].edge_pack == edge_pack) return &index->graphs[i];
    return NULL;
}

static int32_t graph_slot(const pw_index_t *index, const pw_graph_def_t *graph,
                          uint32_t node, bool incoming)
{
    uint8_t value[4] = { (uint8_t)node, (uint8_t)(node >> 8),
                         (uint8_t)(node >> 16), (uint8_t)(node >> 24) };
    return key_slot(index, incoming ? PW_INDEX_GRAPH_REVERSE : PW_INDEX_GRAPH_FORWARD,
                    graph->edge_pack,
                    incoming ? graph->target_ordinal : graph->source_ordinal,
                    value, sizeof(value), false);
}

uint32_t pw_index_graph_neighbors(const pw_index_t *index, uint16_t edge_pack,
                                  uint32_t node, bool incoming,
                                  uint32_t *out_neighbors, int32_t *out_weights,
                                  uint32_t max_neighbors)
{
    const pw_graph_def_t *graph = find_graph(index, edge_pack);
    if (!graph || !out_neighbors || max_neighbors == 0) return 0;
    int32_t slot = graph_slot(index, graph, node, incoming);
    if (slot < 0) return 0;
    uint32_t n = 0;
    for (uint32_t p = index->keys[(uint32_t)slot].head;
         p != PW_INDEX_NONE && n < max_neighbors; p = index->postings[p].next) {
        out_neighbors[n] = index->postings[p].related;
        if (out_weights) out_weights[n] = index->postings[p].weight;
        n++;
    }
    return n;
}

static int32_t work_find(const uint32_t *nodes, uint32_t count, uint32_t node)
{
    for (uint32_t i = 0; i < count; i++) if (nodes[i] == node) return (int32_t)i;
    return -1;
}

bool pw_index_graph_shortest_path(const pw_index_t *index, uint16_t edge_pack,
                                  uint32_t source, uint32_t target,
                                  uint32_t *path, uint32_t *inout_path_len,
                                  uint32_t *work_nodes, int64_t *work_distance,
                                  uint32_t *work_parent, uint8_t *work_closed,
                                  uint32_t work_capacity,
                                  pw_graph_result_t *result)
{
    const pw_graph_def_t *graph = find_graph(index, edge_pack);
    if (result) memset(result, 0, sizeof(*result));
    if (!graph || !path || !inout_path_len || !work_nodes || !work_distance ||
        !work_parent || !work_closed || work_capacity == 0 || *inout_path_len == 0)
        return false;
    uint32_t count = 1;
    work_nodes[0] = source;
    work_distance[0] = 0;
    work_parent[0] = source;
    work_closed[0] = 0;
    bool bounded_out = false;
    while (true) {
        int32_t best = -1;
        for (uint32_t i = 0; i < count; i++)
            if (!work_closed[i] && (best < 0 || work_distance[i] < work_distance[(uint32_t)best]))
                best = (int32_t)i;
        if (best < 0) break;
        uint32_t bi = (uint32_t)best;
        uint32_t node = work_nodes[bi];
        if (node == target) break;
        work_closed[bi] = 1;
        if (result) result->expanded++;
        int32_t slot = graph_slot(index, graph, node, false);
        if (slot < 0) continue;
        for (uint32_t p = index->keys[(uint32_t)slot].head;
             p != PW_INDEX_NONE; p = index->postings[p].next) {
            const pw_index_posting_t *edge = &index->postings[p];
            if (edge->weight < 0) continue;
            int64_t candidate = work_distance[bi] + edge->weight;
            int32_t ni = work_find(work_nodes, count, edge->related);
            if (ni < 0) {
                if (count >= work_capacity) { bounded_out = true; continue; }
                ni = (int32_t)count++;
                work_nodes[(uint32_t)ni] = edge->related;
                work_distance[(uint32_t)ni] = candidate;
                work_parent[(uint32_t)ni] = node;
                work_closed[(uint32_t)ni] = 0;
            } else if (candidate < work_distance[(uint32_t)ni]) {
                work_distance[(uint32_t)ni] = candidate;
                work_parent[(uint32_t)ni] = node;
                work_closed[(uint32_t)ni] = 0;
            }
        }
    }
    if (result) { result->visited = count; result->complete = !bounded_out; }
    int32_t target_i = work_find(work_nodes, count, target);
    if (target_i < 0) return false;
    uint32_t cap = *inout_path_len;
    uint32_t n = 0;
    uint32_t cursor = target;
    while (n < cap) {
        path[n++] = cursor;
        if (cursor == source) break;
        int32_t ci = work_find(work_nodes, count, cursor);
        if (ci < 0 || work_parent[(uint32_t)ci] == cursor) return false;
        cursor = work_parent[(uint32_t)ci];
    }
    if (n == cap && path[n - 1u] != source) return false;
    for (uint32_t i = 0; i < n / 2u; i++) {
        uint32_t tmp = path[i]; path[i] = path[n - 1u - i]; path[n - 1u - i] = tmp;
    }
    *inout_path_len = n;
    if (result) result->total_weight = work_distance[(uint32_t)target_i];
    return true;
}
