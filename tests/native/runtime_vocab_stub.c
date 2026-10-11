/* Offline process fixture: synthetic counts only, no model/vocabulary parsing. */
#include "llama.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
struct llama_model { int present; };
struct llama_vocab { int present; };
static struct llama_model model;
static struct llama_vocab vocab;
static char audit[8192];
static void mark(const char * value) {
    FILE * file = fopen(audit, "a");
    assert(file);
    fputs(value, file);
    fclose(file);
}
struct llama_model_params llama_model_default_params(void) {
    struct llama_model_params params = {0};
    params.main_gpu = 17;
    return params;
}
struct llama_model * llama_model_load_from_file(const char * path, struct llama_model_params params) {
    assert(params.vocab_only && params.n_gpu_layers == 0 && params.main_gpu == 17);
    snprintf(audit, sizeof(audit), "%s.audit", path);
    mark("open\n");
    return &model;
}
const struct llama_vocab * llama_model_get_vocab(const struct llama_model * value) {
    assert(value == &model);
    return &vocab;
}
void llama_model_free(struct llama_model * value) {
    assert(value == &model);
    mark("free\n");
}
int32_t llama_tokenize(const struct llama_vocab * value, const char * text, int32_t length,
                       llama_token * tokens, int32_t capacity, bool add_special, bool parse_special) {
    assert(value == &vocab);
    if (length == 4 && memcmp(text, "hang", 4) == 0) {
        mark("hang\n");
        sleep(30);
    }
    /* Synthetic reversible encoding of flags + complete byte length. */
    int32_t count = length + (add_special ? 2 : 0) + (parse_special ? 1 : 0);
    if (!tokens) return -count;
    assert(capacity == count);
    for (int32_t i = 0; i < count; i++) tokens[i] = i;
    return count;
}
