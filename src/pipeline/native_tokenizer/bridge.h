#ifndef LOGCHAT_VOCAB_BRIDGE_H
#define LOGCHAT_VOCAB_BRIDGE_H
#include <stdint.h>

#define LC_VOCAB_ABI 1
#define LC_VOCAB_MAX_BYTES 1048576u
#define LC_VOCAB_MAX_TOKENS 1048576u

enum lc_vocab_status {
    LC_OK = 0,
    LC_ARGUMENT = 1,
    LC_LIBRARY = 2,
    LC_SYMBOL = 3,
    LC_MEMORY = 4,
    LC_MODEL = 5,
    LC_VOCAB = 6,
    LC_CAPACITY = 7,
    LC_TOKENIZE = 8
};

struct lc_vocab;
/* Caller must verify library, dependencies and model identity before open.
 * One owner serializes count/close; close consumes the handle, including NULL.
 * The bridge does no discovery, backend initialization, context or inference.
 */
uint32_t lc_vocab_abi(void);
int lc_vocab_open(const char * library, const char * model, struct lc_vocab ** out);
int lc_vocab_count(struct lc_vocab * handle, const char * text, uint32_t length,
                   int add_special, int parse_special, uint32_t capacity,
                   uint32_t * out);
void lc_vocab_close(struct lc_vocab * handle);
#endif
