/* Owned scalar-only worker. No untrusted strings ever reach the protocol fd. */
#include "bridge.h"
#include "llama.h"
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int transfer(int fd, void * buffer, size_t length, int writing) {
    unsigned char * bytes = buffer;
    while (length) {
        ssize_t n = writing ? write(fd, bytes, length) : read(fd, bytes, length);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return 0;
        bytes += n;
        length -= (size_t)n;
    }
    return 1;
}
static uint32_t decode(const unsigned char * p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}
static void encode(unsigned char * p, uint32_t n) {
    p[0] = (unsigned char)(n >> 24); p[1] = (unsigned char)(n >> 16);
    p[2] = (unsigned char)(n >> 8); p[3] = (unsigned char)n;
}
static int reply(int fd, uint32_t status, uint32_t count) {
    unsigned char frame[12] = {'L', 'C', 'R', '1'};
    encode(frame + 4, status); encode(frame + 8, count);
    return transfer(fd, frame, sizeof(frame), 1);
}
static void quiet(enum ggml_log_level level, const char * text, void * data) {
    (void)level; (void)text; (void)data;
}
int main(int argc, char ** argv) {
    int output = dup(STDOUT_FILENO), sink = open("/dev/null", O_WRONLY);
    if (output < 0 || sink < 0) return 1;
    if (dup2(sink, STDOUT_FILENO) < 0 || dup2(sink, STDERR_FILENO) < 0) return 1;
    close(sink);
    struct lc_vocab * handle = NULL;
    void * library = NULL;
    int result = 1;
    if (argc != 3 || argv[1][0] != '/' || argv[2][0] != '/') goto done;
    library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!library) { reply(output, 2, 0); goto done; }
    __typeof__(&llama_log_set) logs;
    void * symbol = dlsym(library, "llama_log_set");
    _Static_assert(sizeof(logs) == sizeof(symbol), "function pointer ABI");
    if (!symbol) { reply(output, 3, 0); goto done; }
    memcpy(&logs, &symbol, sizeof(logs));
    logs(quiet, NULL); /* Global callback exists only in this exclusive child. */
    int status = lc_vocab_open(argv[1], argv[2], &handle);
    if (status) { reply(output, (uint32_t)status, 0); goto done; }
#if defined(__x86_64__)
    const uint32_t cpu = 1;
#elif defined(__aarch64__) || defined(__arm64__)
    const uint32_t cpu = 2;
#else
#error Unsupported worker CPU
#endif
    if (!reply(output, 0, cpu)) goto done;
    for (;;) {
        unsigned char header[20];
        if (!transfer(STDIN_FILENO, header, sizeof(header), 0)) goto done;
        uint32_t op = decode(header + 4), length = decode(header + 8);
        uint32_t capacity = decode(header + 12), flags = decode(header + 16);
        if (memcmp(header, "LCQ1", 4) || op > 1 || length > LC_VOCAB_MAX_BYTES ||
                capacity > LC_VOCAB_MAX_TOKENS || flags > 3 ||
                (!op && (length || capacity || flags))) {
            reply(output, 1, 0); goto done;
        }
        if (!op) { result = 0; break; }
        char * text = malloc(length ? length : 1);
        if (!text) { reply(output, 4, 0); goto done; }
        if (!transfer(STDIN_FILENO, text, length, 0)) { free(text); goto done; }
        uint32_t count = 0;
        status = lc_vocab_count(handle, text, length, flags & 1, (flags >> 1) & 1,
                                capacity, &count);
        volatile char * wipe = text;
        for (uint32_t i = 0; i < length; i++) wipe[i] = 0;
        free(text);
        if (!reply(output, (uint32_t)status, status ? 0 : count) || status) goto done;
    }
done:
    lc_vocab_close(handle);
    if (library) dlclose(library);
    close(output);
    return result;
}
