/* catq_plan.c -- dependency-free CAT-Q C-PicoScript plan generator.
 *
 * Input:
 *   model.safetensors.index.json
 *   activation manifest, one tab-separated record per line:
 *     weight_name  calibration_shard  calibration_tensor  output_shard
 *
 * Output:
 *   executable C-syntax PicoScript that maps each weight and its matching
 *   captured activation tensor, invokes CAT-Q, packs, and saves the result.
 */

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    char *tensor;
    char *shard;
} weight_entry;

typedef struct {
    weight_entry *items;
    size_t count;
    size_t capacity;
} weight_map;

static char *read_file(const char *path)
{
    FILE *file = fopen(path, "rb");
    long size;
    char *data;
    if (!file) return 0;
    if (fseek(file, 0, SEEK_END) != 0 || (size = ftell(file)) < 0 ||
        fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return 0;
    }
    data = (char *)malloc((size_t)size + 1);
    if (!data || fread(data, 1, (size_t)size, file) != (size_t)size) {
        free(data);
        fclose(file);
        return 0;
    }
    data[size] = '\0';
    fclose(file);
    return data;
}

static const char *skip_ws(const char *p)
{
    while (*p && isspace((unsigned char)*p)) p++;
    return p;
}

static const char *json_string(const char *p, char **out)
{
    const char *start;
    char *value, *dst;
    if (*p != '"') return 0;
    start = ++p;
    while (*p && *p != '"') {
        if (*p == '\\' && p[1]) p++;
        p++;
    }
    if (*p != '"') return 0;
    value = (char *)malloc((size_t)(p - start) + 1);
    if (!value) return 0;
    dst = value;
    while (start < p) {
        if (*start == '\\' && start + 1 < p) start++;
        *dst++ = *start++;
    }
    *dst = '\0';
    *out = value;
    return p + 1;
}

static int map_push(weight_map *map, char *tensor, char *shard)
{
    if (map->count == map->capacity) {
        size_t next = map->capacity ? map->capacity * 2 : 256;
        weight_entry *items = (weight_entry *)realloc(map->items, next * sizeof(*items));
        if (!items) return 0;
        map->items = items;
        map->capacity = next;
    }
    map->items[map->count].tensor = tensor;
    map->items[map->count].shard = shard;
    map->count++;
    return 1;
}

static int parse_weight_map(const char *json, weight_map *map)
{
    const char *p = strstr(json, "\"weight_map\"");
    int depth = 0;
    if (!p || !(p = strchr(p, '{'))) return 0;
    depth = 1;
    p++;
    while (*p && depth > 0) {
        char *tensor = 0, *shard = 0;
        p = skip_ws(p);
        if (*p == '}') break;
        p = json_string(p, &tensor);
        if (!p) return 0;
        p = skip_ws(p);
        if (*p++ != ':') { free(tensor); return 0; }
        p = skip_ws(p);
        p = json_string(p, &shard);
        if (!p || !map_push(map, tensor, shard)) {
            free(tensor); free(shard); return 0;
        }
        p = skip_ws(p);
        if (*p == ',') p++;
        else if (*p == '}') depth--;
    }
    return map->count > 0;
}

static void free_weight_map(weight_map *map)
{
    size_t i;
    for (i = 0; i < map->count; i++) {
        free(map->items[i].tensor);
        free(map->items[i].shard);
    }
    free(map->items);
}

static const char *lookup_shard(const weight_map *map, const char *tensor)
{
    size_t i;
    for (i = 0; i < map->count; i++)
        if (strcmp(map->items[i].tensor, tensor) == 0) return map->items[i].shard;
    return 0;
}

static int contains(const char *text, const char *part)
{
    return strstr(text, part) != 0;
}

static int ends_with(const char *text, const char *suffix)
{
    size_t a = strlen(text), b = strlen(suffix);
    return a >= b && strcmp(text + a - b, suffix) == 0;
}

static int qwen35_quantizable(const char *name)
{
    if (strncmp(name, "model.language_model.layers.", 28) != 0) return 0;
    if (contains(name, "layernorm") || contains(name, ".norm.") ||
        contains(name, ".mlp.gate.weight") || contains(name, "shared_expert_gate") ||
        contains(name, ".bias") || ends_with(name, ".A_log") ||
        ends_with(name, ".dt_bias") || contains(name, ".conv1d."))
        return 0;
    return ends_with(name, ".weight") ||
        ends_with(name, ".experts.down_proj") ||
        ends_with(name, ".experts.gate_up_proj");
}

static int qwen3_quantizable(const char *name)
{
    if (strncmp(name, "model.layers.", 13) != 0) return 0;
    if (contains(name, "layernorm") || contains(name, ".q_norm.") ||
        contains(name, ".k_norm.") || contains(name, ".bias"))
        return 0;
    return ends_with(name, ".weight");
}

static int gpt_oss_quantizable(const char *name)
{
    if (contains(name, "_blocks") || ends_with(name, ".blocks")) return 2;
    if (contains(name, "_scales") || ends_with(name, ".scales")) return 0;
    if (strncmp(name, "model.layers.", 13) != 0) return 0;
    if (contains(name, "layernorm") || contains(name, ".router.") ||
        contains(name, ".bias") || ends_with(name, ".sinks"))
        return 0;
    return ends_with(name, ".weight");
}

static int architecture_quantizable(const char *architecture, const char *name)
{
    if (strcmp(architecture, "qwen3.5") == 0 ||
        strcmp(architecture, "qwen3_5") == 0)
        return qwen35_quantizable(name);
    if (strcmp(architecture, "qwen3") == 0)
        return qwen3_quantizable(name);
    if (strcmp(architecture, "gpt-oss") == 0 ||
        strcmp(architecture, "gpt_oss") == 0)
        return gpt_oss_quantizable(name);
    return 0;
}

static void pico_string(FILE *out, const char *text)
{
    const unsigned char *p = (const unsigned char *)text;
    fputc('"', out);
    while (*p) {
        if (*p == '"' || *p == '\\') fputc('\\', out);
        if (*p == '\n') fputs("\\n", out);
        else if (*p == '\r') fputs("\\r", out);
        else if (*p == '\t') fputs("\\t", out);
        else fputc(*p, out);
        p++;
    }
    fputc('"', out);
}

static void join_path(char *out, size_t cap, const char *dir, const char *name)
{
    size_t n = strlen(dir);
    snprintf(out, cap, "%s%s%s", dir,
             n && dir[n - 1] != '/' && dir[n - 1] != '\\' ? "/" : "", name);
}

static int split_manifest(char *line, char **fields, int count)
{
    int i = 0;
    char *p = line;
    while (i < count) {
        char *tab;
        fields[i++] = p;
        tab = strchr(p, '\t');
        if (!tab) break;
        *tab = '\0';
        p = tab + 1;
    }
    while (i < count) fields[i++] = 0;
    for (i = 0; i < count; i++) if (!fields[i]) return i;
    return count;
}

static int paired_scale_name(const char *blocks, char *out, size_t cap)
{
    const char *suffix = 0;
    size_t prefix;
    if ((suffix = strstr(blocks, "_blocks")) != 0 && suffix[7] == '\0') {
        prefix = (size_t)(suffix - blocks);
        if (prefix + 8 >= cap) return 0;
        memcpy(out, blocks, prefix);
        memcpy(out + prefix, "_scales", 8);
        return 1;
    }
    if (ends_with(blocks, ".blocks")) {
        prefix = strlen(blocks) - 7;
        if (prefix + 8 >= cap) return 0;
        memcpy(out, blocks, prefix);
        memcpy(out + prefix, ".scales", 8);
        return 1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    char index_path[2048], single_path[2048], line[8192];
    char *json;
    weight_map map = {0};
    FILE *manifest, *out;
    int emitted = 0;
    const char *options;
    if (argc < 5) {
        fprintf(stderr,
            "usage: catq_plan <qwen3|qwen3.5|gpt-oss> <model-dir> "
            "<activation-manifest.tsv> <output.pc> [catq-options]\n");
        return 2;
    }
    options = argc > 5 ? argv[5] :
        "group=128;epochs=60;batch=3;gamma=0.8;s0=30;lr=0.001";
    join_path(index_path, sizeof(index_path), argv[2], "model.safetensors.index.json");
    json = read_file(index_path);
    join_path(single_path, sizeof(single_path), argv[2], "model.safetensors");
    if (json && !parse_weight_map(json, &map)) {
        fprintf(stderr, "failed to parse weight map: %s\n", index_path);
        free(json);
        return 3;
    }
    if (!json) {
        FILE *single = fopen(single_path, "rb");
        if (!single) {
            fprintf(stderr, "no index or single safetensors shard in %s\n", argv[2]);
            return 3;
        }
        fclose(single);
    }
    manifest = fopen(argv[3], "r");
    out = fopen(argv[4], "w");
    if (!manifest || !out) {
        fprintf(stderr, "failed to open manifest or output\n");
        if (manifest) fclose(manifest);
        if (out) fclose(out);
        free_weight_map(&map);
        free(json);
        return 4;
    }

    fputs("// Generated by tools/catq_plan.c -- executable C-PicoScript.\n\n", out);
    fputs("int quantizeTensor(int modelPath, int weightOptions, int viewSpec, "
          "int calibrationPath, int calibrationName, int outputPath) {\n", out);
    fputs("    int model = Shard.Load(modelPath, \"mmap\");\n"
          "    if (model == 0) { raise 3001; }\n"
          "    int calShard = Shard.Load(calibrationPath, \"mmap\");\n"
          "    if (calShard == 0) { raise 3002; }\n"
          "    int calibrationOptions = String.Concat(\"tensor=\", calibrationName);\n"
          "    int weights = Tensor.Map(model, weightOptions);\n"
          "    if (weights == 0) { raise 3003; }\n"
          "    if (Span.Len(viewSpec) > 0) {\n"
          "        weights = Tensor.View(weights, viewSpec);\n"
          "        if (weights == 0) { raise 3006; }\n"
          "    }\n"
          "    int calibration = Tensor.Map(calShard, calibrationOptions);\n"
          "    if (calibration == 0) { raise 3004; }\n"
          "    int context = CatQ.Calibrate(calibration, ", out);
    pico_string(out, options);
    fputs(");\n"
          "    int optimized = CatQ.Optimize(context, weights);\n"
          "    int ternary = CatQ.Ternarize(context, optimized);\n"
          "    int packed = CatQ.Pack(context, ternary);\n"
          "    int saved = Shard.Save(packed, outputPath);\n"
          "    if (saved == 0) { raise 3005; }\n"
          "    Tensor.Release(packed);\n"
          "    Tensor.Release(ternary);\n"
          "    Tensor.Release(optimized);\n"
          "    Tensor.Release(context);\n"
          "    Tensor.Release(calibration);\n"
          "    Tensor.Release(weights);\n"
          "    Tensor.Release(calShard);\n"
          "    Tensor.Release(model);\n"
          "    return saved;\n"
          "}\n\n"
          "int completed = 0;\n", out);

    while (fgets(line, sizeof(line), manifest)) {
        char *fields[5], model_path[4096], weight_options[1024], scale_name[512];
        const char *shard, *scale_shard;
        int allowed;
        int field_count;
        size_t n = strlen(line);
        while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = '\0';
        if (!n || line[0] == '#') continue;
        field_count = split_manifest(line, fields, 5);
        if (field_count < 4) {
            fprintf(stderr, "invalid manifest row: %s\n", line);
            continue;
        }
        shard = lookup_shard(&map, fields[0]);
        if (!shard && !json) shard = "model.safetensors";
        if (!shard) {
            fprintf(stderr, "weight not found: %s\n", fields[0]);
            continue;
        }
        allowed = architecture_quantizable(argv[1], fields[0]);
        if (!allowed) {
            fprintf(stderr, "excluded non-quantizable tensor: %s\n", fields[0]);
            continue;
        }
        if (allowed == 2) {
            if (!paired_scale_name(fields[0], scale_name, sizeof(scale_name)) ||
                !(scale_shard = lookup_shard(&map, scale_name)) ||
                strcmp(scale_shard, shard) != 0) {
                fprintf(stderr, "MXFP4 scale tensor missing or in another shard: %s\n", fields[0]);
                continue;
            }
            snprintf(weight_options, sizeof(weight_options),
                     "mxfp4_blocks=%s;mxfp4_scales=%s", fields[0], scale_name);
        } else {
            snprintf(weight_options, sizeof(weight_options), "tensor=%s", fields[0]);
        }
        join_path(model_path, sizeof(model_path), argv[2], shard);
        fprintf(out, "int jobMark%d = Arena.Mark();\n", emitted);
        fputs("completed += quantizeTensor(", out);
        pico_string(out, model_path); fputs(", ", out);
        pico_string(out, weight_options); fputs(", ", out);
        pico_string(out, field_count >= 5 ? fields[4] : ""); fputs(", ", out);
        pico_string(out, fields[1]); fputs(", ", out);
        pico_string(out, fields[2]); fputs(", ", out);
        pico_string(out, fields[3]); fputs(");\n", out);
        fprintf(out, "Arena.Rewind(jobMark%d);\n", emitted);
        emitted++;
    }
    fputs("\nreturn completed;\n", out);

    fclose(manifest);
    fclose(out);
    free_weight_map(&map);
    free(json);
    if (!emitted) {
        fprintf(stderr, "no quantizable tensor/activation pairs emitted\n");
        return 5;
    }
    fprintf(stdout, "emitted %d CAT-Q tensor jobs to %s\n", emitted, argv[4]);
    return 0;
}
