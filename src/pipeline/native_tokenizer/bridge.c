/* Logchat's scalar ABI over the unmodified llama.cpp b11081 public header.
 * See sources.json and vendor/LICENSE for upstream attribution and hashes.
 * POSIX loader only; no platform compatibility is inferred from compilation.
 */
#include "bridge.h"
#include "llama.h"
#include <dlfcn.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

/* Derive every function pointer type from the pinned declaration, including
 * the large return-by-value and argument-by-value model parameter struct. */
struct lc_vocab {
    void * library;
    struct llama_model * model;
    const struct llama_vocab * vocab;
    __typeof__(&llama_model_default_params) defaults;
    __typeof__(&llama_model_load_from_file) load;
    __typeof__(&llama_model_get_vocab) get_vocab;
    __typeof__(&llama_tokenize) tokenize;
    __typeof__(&llama_model_free) free_model;
};

uint32_t lc_vocab_abi(void) { return LC_VOCAB_ABI; }

void lc_vocab_close(struct lc_vocab * handle) {
    if (!handle) return;
    if (handle->model) handle->free_model(handle->model);
    if (handle->library) dlclose(handle->library);
    free(handle);
}

/* POSIX dlsym returns an address usable as a function pointer. memcpy avoids
 * aliasing violations; the compile-time check rejects incompatible targets. */
#define LC_BIND(field, symbol) do { \
    void * address = dlsym(handle->library, #symbol); \
    _Static_assert(sizeof(handle->field) == sizeof(address), "function pointer ABI"); \
    if (!address) { lc_vocab_close(handle); return LC_SYMBOL; } \
    memcpy(&handle->field, &address, sizeof(address)); \
} while (0)

int lc_vocab_open(const char * library, const char * model, struct lc_vocab ** out) {
    if (!out) return LC_ARGUMENT;
    *out = NULL;
    if (!library || library[0] != '/' || !model || model[0] != '/') return LC_ARGUMENT;
    struct lc_vocab * handle = calloc(1, sizeof(*handle));
    if (!handle) return LC_MEMORY;
    handle->library = dlopen(library, RTLD_NOW | RTLD_LOCAL);
    if (!handle->library) { lc_vocab_close(handle); return LC_LIBRARY; }
    LC_BIND(defaults, llama_model_default_params);
    LC_BIND(load, llama_model_load_from_file);
    LC_BIND(get_vocab, llama_model_get_vocab);
    LC_BIND(tokenize, llama_tokenize);
    LC_BIND(free_model, llama_model_free);
    struct llama_model_params params = handle->defaults();
    params.vocab_only = true;
    params.n_gpu_layers = 0;
    handle->model = handle->load(model, params);
    if (!handle->model) { lc_vocab_close(handle); return LC_MODEL; }
    handle->vocab = handle->get_vocab(handle->model);
    if (!handle->vocab) { lc_vocab_close(handle); return LC_VOCAB; }
    *out = handle;
    return LC_OK;
}

int lc_vocab_count(struct lc_vocab * handle, const char * text, uint32_t length,
                   int add_special, int parse_special, uint32_t capacity,
                   uint32_t * out) {
    if (!out) return LC_ARGUMENT;
    *out = 0;
    if (!handle || !text || (add_special != 0 && add_special != 1) ||
        (parse_special != 0 && parse_special != 1)) return LC_ARGUMENT;
    if (length > LC_VOCAB_MAX_BYTES || capacity > LC_VOCAB_MAX_TOKENS) return LC_CAPACITY;
    int32_t needed = handle->tokenize(handle->vocab, text, (int32_t) length,
                                     NULL, 0, add_special, parse_special);
    if (needed == INT32_MIN) return LC_CAPACITY;
    if (needed > 0) return LC_TOKENIZE; /* violates zero-capacity API contract */
    if (needed == 0) return LC_OK;
    uint32_t count = (uint32_t) -needed;
    _Static_assert(LC_VOCAB_MAX_TOKENS <= SIZE_MAX / sizeof(llama_token), "token buffer size");
    if (count > capacity) return LC_CAPACITY;
    llama_token * tokens = malloc(count * sizeof(*tokens));
    if (!tokens) return LC_MEMORY;
    int32_t actual = handle->tokenize(handle->vocab, text, (int32_t) length,
                                     tokens, (int32_t) count, add_special, parse_special);
    /* No token IDs leave this function. Erase the transient token buffer. */
    volatile llama_token * wipe = tokens;
    for (uint32_t i = 0; i < count; i++) wipe[i] = 0;
    free(tokens);
    if (actual < 0 || (uint32_t) actual != count) return LC_TOKENIZE;
    *out = count;
    return LC_OK;
}
