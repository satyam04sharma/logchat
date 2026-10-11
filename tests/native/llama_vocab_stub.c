/* Offline ABI fixture. Never reads a GGUF or calls an actual tokenizer. */
#include "llama.h"
#include <assert.h>
#include <limits.h>
#include <string.h>

struct llama_model { int present; };
struct llama_vocab { int present; };
static struct llama_model model;
static struct llama_vocab vocab;
static int mode, opens, frees, calls, flags, bytes;

void stub_reset(int value) { mode = value; opens = frees = calls = flags = bytes = 0; }
int stub_opens(void) { return opens; }
int stub_frees(void) { return frees; }
int stub_calls(void) { return calls; }
int stub_flags(void) { return flags; }
int stub_bytes(void) { return bytes; }

struct llama_model_params llama_model_default_params(void) {
    struct llama_model_params params = {0};
    params.n_gpu_layers = 99;
    params.main_gpu = 17;
    params.split_mode = LLAMA_SPLIT_MODE_ROW;
    params.load_mode = LLAMA_LOAD_MODE_MMAP;
    params.lazy_mode = LLAMA_LAZY_MODE_AUTO;
    params.check_tensors = true;
    params.use_extra_bufts = true;
    params.load_mtp = true;
    return params;
}
struct llama_model * llama_model_load_from_file(const char * path, struct llama_model_params params) {
    assert(strcmp(path, "/offline-fixture.gguf") == 0);
    assert(params.vocab_only && params.n_gpu_layers == 0);
    assert(params.main_gpu == 17 && params.split_mode == LLAMA_SPLIT_MODE_ROW);
    assert(params.load_mode == LLAMA_LOAD_MODE_MMAP && params.lazy_mode == LLAMA_LAZY_MODE_AUTO);
    assert(params.check_tensors && params.use_extra_bufts && params.load_mtp);
    assert(!params.no_host && !params.no_alloc);
    assert(!params.devices && !params.tensor_buft_overrides && !params.tensor_split);
    assert(!params.progress_callback && !params.progress_callback_user_data && !params.kv_overrides);
    opens++;
    return mode == 1 ? NULL : &model;
}
const struct llama_vocab * llama_model_get_vocab(const struct llama_model * value) {
    assert(value == &model);
    return mode == 2 ? NULL : &vocab;
}
void llama_model_free(struct llama_model * value) { assert(value == &model); frees++; }
#ifndef STUB_MISSING_TOKENIZE
int32_t llama_tokenize(const struct llama_vocab * value, const char * text, int32_t length,
                       llama_token * tokens, int32_t capacity, bool add_special, bool parse_special) {
    assert(value == &vocab && text);
    calls++;
    flags = (add_special ? 2 : 0) + (parse_special ? 1 : 0);
    bytes = length;
    if (mode == 3) return INT32_MIN;
    if (mode == 4) return INT32_MAX;
    if (mode == 5) return -1048577;
    if (mode == 6) return 0;
    if (!tokens) { assert(capacity == 0); return -7; }
    assert(capacity == 7);
    for (int i = 0; i < capacity; i++) tokens[i] = i;
    if (mode == 7) return -8;
    if (mode == 8) return 6;
    if (mode == 9) return 8;
    return 7;
}
#endif
