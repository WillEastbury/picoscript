#ifndef PICOWAL_PORTABLE_INDEX_H
#define PICOWAL_PORTABLE_INDEX_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PW_INDEX_NONE UINT32_MAX
#define PW_INDEX_VALUE_MAX 31u
#define PW_INDEX_MAX_SCHEMA_FIELDS 32u
#define PW_INDEX_MAX_GRAPHS 8u

typedef enum {
    PW_INDEX_EXACT = 1,
    PW_INDEX_REVERSE_LOOKUP = 2,
    PW_INDEX_FULLTEXT = 3,
    PW_INDEX_GRAPH_FORWARD = 4,
    PW_INDEX_GRAPH_REVERSE = 5
} pw_index_kind_t;

typedef struct {
    uint8_t ordinal;
    uint8_t type;
    uint8_t target_pack;
} pw_schema_field_t;

typedef struct {
    uint16_t pack;
    uint8_t field_count;
    pw_schema_field_t fields[PW_INDEX_MAX_SCHEMA_FIELDS];
} pw_schema_t;

typedef struct {
    uint16_t edge_pack;
    uint8_t source_ordinal;
    uint8_t target_ordinal;
    uint8_t weight_ordinal;
    bool directed;
} pw_graph_def_t;

typedef struct {
    uint32_t hash;
    uint32_t head;
    uint32_t count;
    uint16_t pack;
    uint8_t field;
    uint8_t kind;
    uint8_t value_len;
    uint8_t used;
    uint8_t value[PW_INDEX_VALUE_MAX];
} pw_index_key_t;

typedef struct {
    uint32_t record;
    uint32_t related;
    int32_t weight;
    uint32_t next;
    uint8_t used;
} pw_index_posting_t;

typedef struct {
    pw_index_key_t *keys;
    uint32_t key_capacity;
    pw_index_posting_t *postings;
    uint32_t posting_capacity;
    uint32_t free_posting;
    pw_graph_def_t graphs[PW_INDEX_MAX_GRAPHS];
    uint8_t graph_count;
    bool complete;
} pw_index_t;

typedef struct {
    uint32_t visited;
    uint32_t expanded;
    int64_t total_weight;
    bool complete;
} pw_graph_result_t;

void pw_index_init(pw_index_t *index,
                   pw_index_key_t *keys, uint32_t key_capacity,
                   pw_index_posting_t *postings, uint32_t posting_capacity);
void pw_index_reset(pw_index_t *index);

bool pw_index_define_graph(pw_index_t *index, const pw_graph_def_t *definition);

bool pw_index_card_update(pw_index_t *index, uint16_t pack, uint32_t record,
                          const pw_schema_t *schema,
                          const uint8_t *old_card, uint16_t old_len,
                          const uint8_t *new_card, uint16_t new_len);

uint32_t pw_index_exact(const pw_index_t *index, uint16_t pack, uint8_t field,
                        const uint8_t *value, uint8_t value_len,
                        uint32_t *out_records, uint32_t max_records);

uint32_t pw_index_reverse_lookup(const pw_index_t *index, uint16_t source_pack,
                                 uint8_t field, uint32_t target_record,
                                 uint32_t *out_records, uint32_t max_records);

uint32_t pw_index_text_all(const pw_index_t *index, uint16_t pack, uint8_t field,
                           const char *query, uint32_t query_len,
                           uint32_t *out_records, uint32_t max_records);

uint32_t pw_index_graph_neighbors(const pw_index_t *index, uint16_t edge_pack,
                                  uint32_t node, bool incoming,
                                  uint32_t *out_neighbors, int32_t *out_weights,
                                  uint32_t max_neighbors);

bool pw_index_graph_shortest_path(const pw_index_t *index, uint16_t edge_pack,
                                  uint32_t source, uint32_t target,
                                  uint32_t *path, uint32_t *inout_path_len,
                                  uint32_t *work_nodes, int64_t *work_distance,
                                  uint32_t *work_parent, uint8_t *work_closed,
                                  uint32_t work_capacity,
                                  pw_graph_result_t *result);

uint32_t pw_index_hash(const void *data, uint32_t len);

#ifdef __cplusplus
}
#endif

#endif
