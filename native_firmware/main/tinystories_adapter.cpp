/*
 * TinyStories ESP-IDF adapter, derived from the MIT-licensed llama2.c
 * inference implementation by Andrej Karpathy (Copyright (c) 2023 Andrej).
 * This adapter retains the llama2.c checkpoint layout and transformer,
 * tokenizer, and sampling algorithms while replacing POSIX mmap/exit and
 * command-line I/O with checked ESP-IDF allocation and callback APIs.
 * The upstream license is in third_party/llama2.c/LICENSE.
 */

#include "tinystories_adapter.hpp"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(TINYSTORIES_HOST_BUILD)
// Host-build seam: the parsing/generation core below is identical to what the
// badge ships, but on the host (unit tests) we have no ESP-IDF. These tiny
// shims stand in for the ESP allocation/RNG APIs so the SAME code compiles and
// runs under a desktop toolchain (and ASan/UBSan). See tests/support/ and
// tests/unit/test_tinystories_adapter.py. Nothing here changes the ESP-IDF
// build, which still pulls in the real headers via the #else branch.
#include <stdint.h>
#ifndef MALLOC_CAP_8BIT
#define MALLOC_CAP_8BIT (1u << 0)
#endif
#ifndef MALLOC_CAP_SPIRAM
#define MALLOC_CAP_SPIRAM (1u << 10)
#endif
static inline void* heap_caps_malloc(size_t bytes, uint32_t /*caps*/) {
    return malloc(bytes);
}
static inline void heap_caps_free(void* ptr) { free(ptr); }
// Deterministic stand-in for esp_random(); the adapter only calls this when a
// caller passes seed == 0, which the host tests avoid for reproducibility.
static inline uint32_t esp_random(void) {
    static uint32_t state = 0x6d7a1f3bu;
    state = state * 1664525u + 1013904223u;
    return state;
}
// The badge mounts its model/tokenizer under the "/data" LittleFS volume, which
// valid_data_path() enforces. On the host there is no such volume, so this shim
// transparently redirects a "/data/..." open to a writable directory named by
// the TINYSTORIES_HOST_DATA_ROOT env var. valid_data_path() itself is left
// unchanged, so the host tests still exercise the real "/data"-prefix rule.
static inline FILE* tinystories_host_fopen(const char* path, const char* mode) {
    const char* root = getenv("TINYSTORIES_HOST_DATA_ROOT");
    if (root != nullptr && strncmp(path, "/data/", 6) == 0) {
        static char redirected[4096];
        snprintf(redirected, sizeof(redirected), "%s/%s", root, path + 6);
        return fopen(redirected, mode);
    }
    return fopen(path, mode);
}
#define fopen(path, mode) tinystories_host_fopen((path), (mode))
#else
#include "esp_heap_caps.h"
#include "esp_random.h"
#endif

namespace tinystories {
namespace {

constexpr int32_t kMaxLayers = 128;
constexpr int32_t kMaxDimension = 4096;
constexpr int32_t kMaxHiddenDimension = 16384;
constexpr int32_t kMaxVocabSize = 100000;
constexpr int32_t kMaxSequenceLength = 4096;
constexpr uint32_t kMaxTokenizerFileSize = 2 * 1024 * 1024;
constexpr uint32_t kMaxTokenLength = 1024;

struct Config {
    int32_t dim;
    int32_t hidden_dim;
    int32_t n_layers;
    int32_t n_heads;
    int32_t n_kv_heads;
    int32_t vocab_size;
    int32_t seq_len;
};

struct Weights {
    float* token_embedding_table;
    float* rms_att_weight;
    float* wq;
    float* wk;
    float* wv;
    float* wo;
    float* rms_ffn_weight;
    float* w1;
    float* w2;
    float* w3;
    float* rms_final_weight;
    float* wcls;
};

struct RunState {
    float* x;
    float* xb;
    float* xb2;
    float* hb;
    float* hb2;
    float* q;
    float* att;
    float* logits;
    float* key_cache;
    float* value_cache;
};

struct TokenIndex {
    const char* str;
    int32_t id;
};

struct ProbIndex {
    float prob;
    int32_t index;
};

struct Tokenizer {
    char** vocab = nullptr;
    float* vocab_scores = nullptr;
    TokenIndex* sorted_vocab = nullptr;
    char* strings = nullptr;
    int32_t vocab_size = 0;
    uint32_t max_token_length = 0;
    unsigned char byte_pieces[512];
};

void* internal_alloc(size_t bytes) {
    return heap_caps_malloc(bytes, MALLOC_CAP_8BIT);
}

void* psram_alloc(size_t bytes) {
    return heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
}

void* psram_calloc(size_t count, size_t size) {
    if (size != 0 && count > SIZE_MAX / size) return nullptr;
    const size_t bytes = count * size;
    void* memory = psram_alloc(bytes);
    if (memory != nullptr) memset(memory, 0, bytes);
    return memory;
}

bool valid_data_path(const char* path) {
    if (path == nullptr || strncmp(path, "/data/", 6) != 0 || path[6] == '\0') {
        return false;
    }
    const char* component = path + 6;
    while (*component != '\0') {
        const char* slash = strchr(component, '/');
        const size_t length = slash == nullptr ? strlen(component)
                                               : static_cast<size_t>(slash - component);
        if (length == 0 || (length == 1 && component[0] == '.') ||
            (length == 2 && component[0] == '.' && component[1] == '.')) {
            return false;
        }
        if (slash == nullptr) break;
        component = slash + 1;
    }
    return true;
}

bool file_size(FILE* file, size_t* size) {
    if (fseek(file, 0, SEEK_END) != 0) return false;
    const long end = ftell(file);
    if (end < 0 || fseek(file, 0, SEEK_SET) != 0) return false;
    *size = static_cast<size_t>(end);
    return true;
}

bool valid_config(const Config& config, int32_t* vocab_size, bool* shared_weights) {
    if (config.dim <= 0 || config.dim > kMaxDimension ||
        config.hidden_dim <= 0 || config.hidden_dim > kMaxHiddenDimension ||
        config.n_layers <= 0 || config.n_layers > kMaxLayers ||
        config.n_heads <= 0 || config.n_kv_heads <= 0 ||
        config.n_heads % config.n_kv_heads != 0 ||
        config.dim % config.n_heads != 0 ||
        config.n_kv_heads > config.n_heads ||
        config.seq_len <= 0 || config.seq_len > kMaxSequenceLength ||
        config.vocab_size == INT32_MIN) {
        return false;
    }
    *shared_weights = config.vocab_size > 0;
    *vocab_size = config.vocab_size > 0 ? config.vocab_size : -config.vocab_size;
    const int32_t head_size = config.dim / config.n_heads;
    return *vocab_size >= 259 && *vocab_size <= kMaxVocabSize &&
           (head_size % 2) == 0;
}

uint64_t expected_weight_floats(const Config& c, int32_t vocab_size,
                                bool shared_weights) {
    const uint64_t layers = static_cast<uint64_t>(c.n_layers);
    const uint64_t dim = static_cast<uint64_t>(c.dim);
    const uint64_t hidden = static_cast<uint64_t>(c.hidden_dim);
    const uint64_t vocab = static_cast<uint64_t>(vocab_size);
    const uint64_t head_size = dim / static_cast<uint64_t>(c.n_heads);
    const uint64_t kv_dim =
        (dim * static_cast<uint64_t>(c.n_kv_heads)) / static_cast<uint64_t>(c.n_heads);
    uint64_t total = vocab * dim;
    total += layers * dim;                   // attention RMSNorm
    total += layers * dim * dim;             // Wq
    total += layers * dim * kv_dim * 2;      // Wk and Wv
    total += layers * dim * dim;             // Wo
    total += layers * dim;                   // FFN RMSNorm
    total += layers * dim * hidden * 2;      // W1 and W3
    total += layers * hidden * dim;          // W2
    total += dim;                            // final RMSNorm
    total += static_cast<uint64_t>(c.seq_len) * head_size;  // legacy RoPE arrays
    if (!shared_weights) total += vocab * dim;
    return total;
}

int compare_tokens(const void* a, const void* b) {
    return strcmp(static_cast<const TokenIndex*>(a)->str,
                  static_cast<const TokenIndex*>(b)->str);
}

void free_tokenizer(Tokenizer* tokenizer) {
    if (tokenizer == nullptr) return;
    heap_caps_free(tokenizer->sorted_vocab);
    heap_caps_free(tokenizer->vocab_scores);
    heap_caps_free(tokenizer->vocab);
    heap_caps_free(tokenizer->strings);
    memset(tokenizer, 0, sizeof(*tokenizer));
}

Status build_tokenizer(Tokenizer* tokenizer, const char* path, int32_t vocab_size) {
    FILE* file = fopen(path, "rb");
    if (file == nullptr) return Status::kIoError;

    size_t bytes = 0;
    if (!file_size(file, &bytes) || bytes < sizeof(int32_t) ||
        bytes > kMaxTokenizerFileSize) {
        fclose(file);
        return Status::kInvalidFormat;
    }
    uint8_t* source = static_cast<uint8_t*>(psram_alloc(bytes));
    if (source == nullptr) {
        fclose(file);
        return Status::kOutOfMemory;
    }
    const bool read_ok = fread(source, 1, bytes, file) == bytes;
    fclose(file);
    if (!read_ok) {
        heap_caps_free(source);
        return Status::kIoError;
    }

    size_t offset = 0;
    int32_t max_token_length = 0;
    memcpy(&max_token_length, source, sizeof(max_token_length));
    offset += sizeof(max_token_length);
    if (max_token_length <= 0 ||
        static_cast<uint32_t>(max_token_length) > kMaxTokenLength) {
        heap_caps_free(source);
        return Status::kInvalidFormat;
    }

    size_t strings_size = 0;
    for (int32_t i = 0; i < vocab_size; ++i) {
        if (offset > bytes || bytes - offset < sizeof(float) + sizeof(int32_t)) {
            heap_caps_free(source);
            return Status::kInvalidFormat;
        }
        offset += sizeof(float);
        int32_t length = 0;
        memcpy(&length, source + offset, sizeof(length));
        offset += sizeof(length);
        if (length < 0 || static_cast<uint32_t>(length) > kMaxTokenLength ||
            static_cast<size_t>(length) > bytes - offset ||
            strings_size > SIZE_MAX - static_cast<size_t>(length) - 1) {
            heap_caps_free(source);
            return Status::kInvalidFormat;
        }
        strings_size += static_cast<size_t>(length) + 1;
        offset += static_cast<size_t>(length);
    }
    if (offset != bytes) {
        heap_caps_free(source);
        return Status::kInvalidFormat;
    }

    tokenizer->vocab = static_cast<char**>(
        psram_alloc(static_cast<size_t>(vocab_size) * sizeof(char*)));
    tokenizer->vocab_scores = static_cast<float*>(
        psram_alloc(static_cast<size_t>(vocab_size) * sizeof(float)));
    tokenizer->sorted_vocab = static_cast<TokenIndex*>(
        psram_alloc(static_cast<size_t>(vocab_size) * sizeof(TokenIndex)));
    tokenizer->strings = static_cast<char*>(psram_alloc(strings_size));
    if (tokenizer->vocab == nullptr || tokenizer->vocab_scores == nullptr ||
        tokenizer->sorted_vocab == nullptr || tokenizer->strings == nullptr) {
        heap_caps_free(source);
        free_tokenizer(tokenizer);
        return Status::kOutOfMemory;
    }

    offset = sizeof(int32_t);
    size_t string_offset = 0;
    for (int32_t i = 0; i < vocab_size; ++i) {
        int32_t length = 0;
        memcpy(&tokenizer->vocab_scores[i], source + offset, sizeof(float));
        offset += sizeof(float);
        memcpy(&length, source + offset, sizeof(length));
        offset += sizeof(length);
        tokenizer->vocab[i] = tokenizer->strings + string_offset;
        memcpy(tokenizer->vocab[i], source + offset, static_cast<size_t>(length));
        tokenizer->vocab[i][length] = '\0';
        string_offset += static_cast<size_t>(length) + 1;
        offset += static_cast<size_t>(length);
        tokenizer->sorted_vocab[i] = {tokenizer->vocab[i], i};
    }
    heap_caps_free(source);

    tokenizer->vocab_size = vocab_size;
    tokenizer->max_token_length = static_cast<uint32_t>(max_token_length);
    for (int i = 0; i < 256; ++i) {
        tokenizer->byte_pieces[i * 2] = static_cast<unsigned char>(i);
        tokenizer->byte_pieces[i * 2 + 1] = '\0';
    }
    qsort(tokenizer->sorted_vocab, vocab_size, sizeof(TokenIndex), compare_tokens);
    return Status::kOk;
}

int32_t token_lookup(const char* text, const Tokenizer& tokenizer) {
    const TokenIndex key = {text, -1};
    const TokenIndex* found = static_cast<const TokenIndex*>(
        bsearch(&key, tokenizer.sorted_vocab, tokenizer.vocab_size,
                sizeof(TokenIndex), compare_tokens));
    return found == nullptr ? -1 : found->id;
}

bool encode(const Tokenizer& tokenizer, const char* text, int32_t* tokens,
            size_t token_capacity, size_t* token_count) {
    const size_t text_size = strlen(text);
    const size_t merge_capacity =
        static_cast<size_t>(tokenizer.max_token_length) * 2 + 2;
    char* merge_buffer = static_cast<char*>(internal_alloc(merge_capacity));
    if (merge_buffer == nullptr) return false;

    size_t count = 0;
    if (token_capacity == 0) {
        heap_caps_free(merge_buffer);
        return false;
    }
    tokens[count++] = 1;
    if (text_size != 0) {
        const int32_t prefix = token_lookup(" ", tokenizer);
        if (prefix < 0 || count == token_capacity) {
            heap_caps_free(merge_buffer);
            return false;
        }
        tokens[count++] = prefix;
    }

    size_t codepoint_size = 0;
    for (size_t i = 0; i < text_size; ++i) {
        const unsigned char byte = static_cast<unsigned char>(text[i]);
        if ((byte & 0xC0) != 0x80) codepoint_size = 0;
        if (codepoint_size + 1 >= merge_capacity) {
            heap_caps_free(merge_buffer);
            return false;
        }
        merge_buffer[codepoint_size++] = static_cast<char>(byte);
        merge_buffer[codepoint_size] = '\0';
        const bool next_is_continuation =
            i + 1 < text_size &&
            (static_cast<unsigned char>(text[i + 1]) & 0xC0) == 0x80;
        if (next_is_continuation && codepoint_size < 4) continue;

        const int32_t id = token_lookup(merge_buffer, tokenizer);
        if (id >= 0) {
            if (count == token_capacity) {
                heap_caps_free(merge_buffer);
                return false;
            }
            tokens[count++] = id;
        } else {
            if (count + codepoint_size > token_capacity) {
                heap_caps_free(merge_buffer);
                return false;
            }
            for (size_t j = 0; j < codepoint_size; ++j) {
                const int32_t byte_id =
                    static_cast<unsigned char>(merge_buffer[j]) + 3;
                if (byte_id >= tokenizer.vocab_size) {
                    heap_caps_free(merge_buffer);
                    return false;
                }
                tokens[count++] = byte_id;
            }
        }
        codepoint_size = 0;
    }

    while (count > 1) {
        float best_score = -1.0e10f;
        int32_t best_id = -1;
        size_t best_index = 0;
        for (size_t i = 0; i + 1 < count; ++i) {
            const char* left = tokenizer.vocab[tokens[i]];
            const char* right = tokenizer.vocab[tokens[i + 1]];
            const size_t left_size = strlen(left);
            const size_t right_size = strlen(right);
            if (left_size + right_size >= merge_capacity) continue;
            memcpy(merge_buffer, left, left_size);
            memcpy(merge_buffer + left_size, right, right_size + 1);
            const int32_t candidate = token_lookup(merge_buffer, tokenizer);
            if (candidate >= 0 && tokenizer.vocab_scores[candidate] > best_score) {
                best_score = tokenizer.vocab_scores[candidate];
                best_id = candidate;
                best_index = i;
            }
        }
        if (best_id < 0) break;
        tokens[best_index] = best_id;
        memmove(tokens + best_index + 1, tokens + best_index + 2,
                (count - best_index - 2) * sizeof(int32_t));
        --count;
    }

    heap_caps_free(merge_buffer);
    *token_count = count;
    return true;
}

void rmsnorm(float* output, const float* input, const float* weight, int32_t size) {
    float sum = 0.0f;
    for (int32_t i = 0; i < size; ++i) sum += input[i] * input[i];
    const float scale = 1.0f / sqrtf(sum / size + 1.0e-5f);
    for (int32_t i = 0; i < size; ++i) output[i] = weight[i] * (scale * input[i]);
}

void softmax(float* values, int32_t size) {
    float maximum = values[0];
    for (int32_t i = 1; i < size; ++i) {
        if (values[i] > maximum) maximum = values[i];
    }
    float sum = 0.0f;
    for (int32_t i = 0; i < size; ++i) {
        values[i] = expf(values[i] - maximum);
        sum += values[i];
    }
    if (sum <= 0.0f || !isfinite(sum)) return;
    for (int32_t i = 0; i < size; ++i) values[i] /= sum;
}

void matmul(float* output, const float* input, const float* weight,
            int32_t input_size, int32_t output_size) {
    for (int32_t i = 0; i < output_size; ++i) {
        float value = 0.0f;
        for (int32_t j = 0; j < input_size; ++j) {
            value += weight[static_cast<size_t>(i) * input_size + j] * input[j];
        }
        output[i] = value;
    }
}

float* forward(const Config& config, const Weights& weights, RunState& state,
               int32_t token, int32_t position) {
    const int32_t dim = config.dim;
    const int32_t head_size = dim / config.n_heads;
    const int32_t kv_dim = (dim * config.n_kv_heads) / config.n_heads;
    const int32_t kv_multiplier = config.n_heads / config.n_kv_heads;
    const int32_t hidden_dim = config.hidden_dim;
    memcpy(state.x, weights.token_embedding_table +
                        static_cast<size_t>(token) * dim,
           static_cast<size_t>(dim) * sizeof(float));

    for (int32_t layer = 0; layer < config.n_layers; ++layer) {
        rmsnorm(state.xb, state.x, weights.rms_att_weight +
                                      static_cast<size_t>(layer) * dim,
                dim);
        const size_t layer_offset =
            static_cast<size_t>(layer) * config.seq_len * kv_dim;
        float* key = state.key_cache + layer_offset +
                     static_cast<size_t>(position) * kv_dim;
        float* value = state.value_cache + layer_offset +
                       static_cast<size_t>(position) * kv_dim;
        matmul(state.q, state.xb, weights.wq +
                                      static_cast<size_t>(layer) * dim * dim,
               dim, dim);
        matmul(key, state.xb, weights.wk +
                                  static_cast<size_t>(layer) * dim * kv_dim,
               dim, kv_dim);
        matmul(value, state.xb, weights.wv +
                                    static_cast<size_t>(layer) * dim * kv_dim,
               dim, kv_dim);

        for (int32_t i = 0; i < dim; i += 2) {
            const int32_t head_dim = i % head_size;
            const float frequency =
                1.0f / powf(10000.0f, head_dim / static_cast<float>(head_size));
            const float angle = position * frequency;
            const float real = cosf(angle);
            const float imaginary = sinf(angle);
            const int32_t vector_count = i < kv_dim ? 2 : 1;
            for (int32_t v = 0; v < vector_count; ++v) {
                float* vector = v == 0 ? state.q : key;
                const float v0 = vector[i];
                const float v1 = vector[i + 1];
                vector[i] = v0 * real - v1 * imaginary;
                vector[i + 1] = v0 * imaginary + v1 * real;
            }
        }

        for (int32_t head = 0; head < config.n_heads; ++head) {
            const float* query = state.q + head * head_size;
            float* attention = state.att +
                               static_cast<size_t>(head) * config.seq_len;
            for (int32_t t = 0; t <= position; ++t) {
                const float* k = state.key_cache + layer_offset +
                    static_cast<size_t>(t) * kv_dim +
                    (head / kv_multiplier) * head_size;
                float score = 0.0f;
                for (int32_t i = 0; i < head_size; ++i) score += query[i] * k[i];
                attention[t] = score / sqrtf(static_cast<float>(head_size));
            }
            softmax(attention, position + 1);
            float* output = state.xb + head * head_size;
            memset(output, 0, static_cast<size_t>(head_size) * sizeof(float));
            for (int32_t t = 0; t <= position; ++t) {
                const float* v = state.value_cache + layer_offset +
                    static_cast<size_t>(t) * kv_dim +
                    (head / kv_multiplier) * head_size;
                for (int32_t i = 0; i < head_size; ++i) {
                    output[i] += attention[t] * v[i];
                }
            }
        }

        matmul(state.xb2, state.xb,
               weights.wo + static_cast<size_t>(layer) * dim * dim, dim, dim);
        for (int32_t i = 0; i < dim; ++i) state.x[i] += state.xb2[i];
        rmsnorm(state.xb, state.x, weights.rms_ffn_weight +
                                      static_cast<size_t>(layer) * dim,
                dim);
        matmul(state.hb, state.xb,
               weights.w1 + static_cast<size_t>(layer) * dim * hidden_dim,
               dim, hidden_dim);
        matmul(state.hb2, state.xb,
               weights.w3 + static_cast<size_t>(layer) * dim * hidden_dim,
               dim, hidden_dim);
        for (int32_t i = 0; i < hidden_dim; ++i) {
            const float value = state.hb[i];
            state.hb[i] = value / (1.0f + expf(-value)) * state.hb2[i];
        }
        matmul(state.xb, state.hb,
               weights.w2 + static_cast<size_t>(layer) * dim * hidden_dim,
               hidden_dim, dim);
        for (int32_t i = 0; i < dim; ++i) state.x[i] += state.xb[i];
    }

    rmsnorm(state.x, state.x, weights.rms_final_weight, dim);
    matmul(state.logits, state.x, weights.wcls, dim, config.vocab_size);
    return state.logits;
}

int compare_probabilities(const void* a, const void* b) {
    const auto* first = static_cast<const ProbIndex*>(a);
    const auto* second = static_cast<const ProbIndex*>(b);
    if (first->prob > second->prob) return -1;
    if (first->prob < second->prob) return 1;
    return 0;
}

uint32_t random_u32(uint64_t* state) {
    *state ^= *state >> 12;
    *state ^= *state << 25;
    *state ^= *state >> 27;
    return static_cast<uint32_t>((*state * 0x2545F4914F6CDD1Dull) >> 32);
}

int32_t sample(float* logits, int32_t vocab_size, float temperature, float top_p,
               ProbIndex* candidates, uint64_t* rng_state) {
    if (temperature == 0.0f) {
        int32_t maximum = 0;
        for (int32_t i = 1; i < vocab_size; ++i) {
            if (logits[i] > logits[maximum]) maximum = i;
        }
        return maximum;
    }
    for (int32_t i = 0; i < vocab_size; ++i) logits[i] /= temperature;
    softmax(logits, vocab_size);
    const float coin = (random_u32(rng_state) >> 8) / 16777216.0f;
    if (top_p <= 0.0f || top_p >= 1.0f) {
        float cumulative = 0.0f;
        for (int32_t i = 0; i < vocab_size; ++i) {
            cumulative += logits[i];
            if (coin < cumulative) return i;
        }
        return vocab_size - 1;
    }

    int32_t candidate_count = 0;
    const float cutoff = (1.0f - top_p) / (vocab_size - 1);
    for (int32_t i = 0; i < vocab_size; ++i) {
        if (logits[i] >= cutoff) {
            candidates[candidate_count++] = {logits[i], i};
        }
    }
    if (candidate_count == 0) return 0;
    qsort(candidates, candidate_count, sizeof(ProbIndex), compare_probabilities);
    float cumulative_probability = 0.0f;
    int32_t last = candidate_count - 1;
    for (int32_t i = 0; i < candidate_count; ++i) {
        cumulative_probability += candidates[i].prob;
        if (cumulative_probability > top_p) {
            last = i;
            break;
        }
    }
    const float target = coin * cumulative_probability;
    float cumulative = 0.0f;
    for (int32_t i = 0; i <= last; ++i) {
        cumulative += candidates[i].prob;
        if (target < cumulative) return candidates[i].index;
    }
    return candidates[last].index;
}

const char* decode(const Tokenizer& tokenizer, int32_t previous, int32_t token,
                   char* byte_piece) {
    const char* piece = tokenizer.vocab[token];
    if (previous == 1 && piece[0] == ' ') ++piece;
    if (strlen(piece) == 6 && piece[0] == '<' && piece[1] == '0' &&
        piece[2] == 'x' && piece[5] == '>') {
        char* end = nullptr;
        const long value = strtol(piece + 3, &end, 16);
        if (end == piece + 5 && value >= 0 && value <= 255) {
            byte_piece[0] = static_cast<char>(value);
            byte_piece[1] = '\0';
            return byte_piece;
        }
    }
    return piece;
}

size_t safe_piece(const char* piece, char* output, size_t capacity) {
    size_t written = 0;
    for (const unsigned char* p = reinterpret_cast<const unsigned char*>(piece);
         *p != '\0' && written + 1 < capacity; ++p) {
        if (*p < 0x20 && *p != '\n' && *p != '\r' && *p != '\t') continue;
        if (*p == 0x7f) continue;
        output[written++] = static_cast<char>(*p);
    }
    output[written] = '\0';
    return written;
}

}  // namespace

struct Adapter::Impl {
    Config config{};
    Weights weights{};
    RunState state{};
    Tokenizer tokenizer{};
    uint8_t* checkpoint = nullptr;
    ProbIndex* candidates = nullptr;
    bool ready = false;
};

Adapter::~Adapter() {
    shutdown();
}

void Adapter::shutdown() {
    if (impl_ == nullptr) return;
    free_tokenizer(&impl_->tokenizer);
    heap_caps_free(impl_->candidates);
    heap_caps_free(impl_->state.x);
    heap_caps_free(impl_->state.xb);
    heap_caps_free(impl_->state.xb2);
    heap_caps_free(impl_->state.hb);
    heap_caps_free(impl_->state.hb2);
    heap_caps_free(impl_->state.q);
    heap_caps_free(impl_->state.att);
    heap_caps_free(impl_->state.logits);
    heap_caps_free(impl_->state.key_cache);
    heap_caps_free(impl_->state.value_cache);
    heap_caps_free(impl_->checkpoint);
    heap_caps_free(impl_);
    impl_ = nullptr;
}

bool Adapter::initialized() const {
    return impl_ != nullptr && impl_->ready;
}

Status Adapter::initialize(const char* model_path, const char* tokenizer_path) {
    shutdown();
    if (!valid_data_path(model_path) || !valid_data_path(tokenizer_path)) {
        return Status::kInvalidArgument;
    }

    Impl* state = static_cast<Impl*>(internal_alloc(sizeof(Impl)));
    if (state == nullptr) return Status::kOutOfMemory;
    memset(state, 0, sizeof(*state));

    FILE* model = fopen(model_path, "rb");
    if (model == nullptr) {
        heap_caps_free(state);
        return Status::kIoError;
    }
    size_t model_size = 0;
    if (!file_size(model, &model_size) || model_size < sizeof(Config)) {
        fclose(model);
        heap_caps_free(state);
        return Status::kInvalidFormat;
    }
    if (fread(&state->config, 1, sizeof(Config), model) != sizeof(Config)) {
        fclose(model);
        heap_caps_free(state);
        return Status::kIoError;
    }

    int32_t vocab_size = 0;
    bool shared_weights = false;
    if (!valid_config(state->config, &vocab_size, &shared_weights)) {
        fclose(model);
        heap_caps_free(state);
        return Status::kInvalidFormat;
    }
    const uint64_t weight_floats =
        expected_weight_floats(state->config, vocab_size, shared_weights);
    const uint64_t expected_size =
        sizeof(Config) + weight_floats * sizeof(float);
    if (expected_size != model_size || expected_size > SIZE_MAX) {
        fclose(model);
        heap_caps_free(state);
        return Status::kInvalidFormat;
    }

    state->checkpoint = static_cast<uint8_t*>(psram_alloc(model_size));
    if (state->checkpoint == nullptr) {
        fclose(model);
        heap_caps_free(state);
        return Status::kOutOfMemory;
    }
    memcpy(state->checkpoint, &state->config, sizeof(Config));
    if (fread(state->checkpoint + sizeof(Config), 1,
              model_size - sizeof(Config), model) != model_size - sizeof(Config)) {
        fclose(model);
        impl_ = state;
        shutdown();
        return Status::kIoError;
    }
    fclose(model);

    float* cursor = reinterpret_cast<float*>(state->checkpoint + sizeof(Config));
    const int32_t dim = state->config.dim;
    const int32_t hidden = state->config.hidden_dim;
    const int32_t layers = state->config.n_layers;
    const int32_t head_size = dim / state->config.n_heads;
    const int32_t kv_dim = (dim * state->config.n_kv_heads) / state->config.n_heads;
    const uint32_t layer_count = static_cast<uint32_t>(layers);
    const size_t d = static_cast<size_t>(dim);
    const size_t h = static_cast<size_t>(hidden);
    state->weights.token_embedding_table = cursor;
    cursor += static_cast<size_t>(vocab_size) * d;
    state->weights.rms_att_weight = cursor;
    cursor += static_cast<size_t>(layer_count) * d;
    state->weights.wq = cursor;
    cursor += static_cast<size_t>(layer_count) * d * d;
    state->weights.wk = cursor;
    cursor += static_cast<size_t>(layer_count) * d * kv_dim;
    state->weights.wv = cursor;
    cursor += static_cast<size_t>(layer_count) * d * kv_dim;
    state->weights.wo = cursor;
    cursor += static_cast<size_t>(layer_count) * d * d;
    state->weights.rms_ffn_weight = cursor;
    cursor += static_cast<size_t>(layer_count) * d;
    state->weights.w1 = cursor;
    cursor += static_cast<size_t>(layer_count) * d * h;
    state->weights.w2 = cursor;
    cursor += static_cast<size_t>(layer_count) * h * d;
    state->weights.w3 = cursor;
    cursor += static_cast<size_t>(layer_count) * d * h;
    state->weights.rms_final_weight = cursor;
    cursor += d;
    cursor += static_cast<size_t>(state->config.seq_len) * head_size;
    state->weights.wcls = shared_weights
                              ? state->weights.token_embedding_table
                              : cursor;
    state->config.vocab_size = vocab_size;

    const size_t kv_cache_size =
        static_cast<size_t>(layers) * state->config.seq_len * kv_dim;
    state->state.x = static_cast<float*>(psram_calloc(d, sizeof(float)));
    state->state.xb = static_cast<float*>(psram_calloc(d, sizeof(float)));
    state->state.xb2 = static_cast<float*>(psram_calloc(d, sizeof(float)));
    state->state.hb = static_cast<float*>(psram_calloc(h, sizeof(float)));
    state->state.hb2 = static_cast<float*>(psram_calloc(h, sizeof(float)));
    state->state.q = static_cast<float*>(psram_calloc(d, sizeof(float)));
    state->state.att = static_cast<float*>(psram_calloc(
        static_cast<size_t>(state->config.n_heads) * state->config.seq_len,
        sizeof(float)));
    state->state.logits =
        static_cast<float*>(psram_calloc(vocab_size, sizeof(float)));
    state->state.key_cache =
        static_cast<float*>(psram_calloc(kv_cache_size, sizeof(float)));
    state->state.value_cache =
        static_cast<float*>(psram_calloc(kv_cache_size, sizeof(float)));
    state->candidates = static_cast<ProbIndex*>(
        psram_alloc(static_cast<size_t>(vocab_size) * sizeof(ProbIndex)));
    if (state->state.x == nullptr || state->state.xb == nullptr ||
        state->state.xb2 == nullptr || state->state.hb == nullptr ||
        state->state.hb2 == nullptr || state->state.q == nullptr ||
        state->state.att == nullptr || state->state.logits == nullptr ||
        state->state.key_cache == nullptr || state->state.value_cache == nullptr ||
        state->candidates == nullptr) {
        impl_ = state;
        shutdown();
        return Status::kOutOfMemory;
    }

    const Status tokenizer_status =
        build_tokenizer(&state->tokenizer, tokenizer_path, vocab_size);
    if (tokenizer_status != Status::kOk) {
        impl_ = state;
        shutdown();
        return tokenizer_status;
    }

    state->ready = true;
    impl_ = state;
    return Status::kOk;
}

Status Adapter::generate(const char* prompt, const GenerationOptions& options,
                         TextCallback callback, void* context) {
    if (!initialized()) return Status::kNotInitialized;
    if (prompt == nullptr || callback == nullptr || options.max_tokens == 0 ||
        options.max_tokens > kMaxGenerationTokens ||
        !isfinite(options.temperature) || options.temperature < 0.0f ||
        options.temperature > 2.0f || !isfinite(options.top_p) ||
        options.top_p < 0.0f || options.top_p > 1.0f) {
        return Status::kInvalidArgument;
    }
    const size_t prompt_size = strnlen(prompt, kMaxPromptBytes + 1);
    if (prompt_size > kMaxPromptBytes) return Status::kPromptTooLong;

    const size_t token_capacity = prompt_size + 2;
    int32_t* prompt_tokens = static_cast<int32_t*>(
        internal_alloc(token_capacity * sizeof(int32_t)));
    if (prompt_tokens == nullptr) return Status::kOutOfMemory;
    size_t prompt_token_count = 0;
    if (!encode(impl_->tokenizer, prompt, prompt_tokens, token_capacity,
                &prompt_token_count)) {
        heap_caps_free(prompt_tokens);
        return Status::kInvalidFormat;
    }
    if (prompt_token_count == 0 ||
        prompt_token_count >= static_cast<size_t>(impl_->config.seq_len)) {
        heap_caps_free(prompt_tokens);
        return Status::kPromptTooLong;
    }

    const size_t remaining = static_cast<size_t>(impl_->config.seq_len) -
                             prompt_token_count;
    const size_t requested = options.max_tokens < remaining
                                 ? options.max_tokens
                                 : remaining;
    uint64_t rng_state = options.seed == 0
                             ? (static_cast<uint64_t>(esp_random()) << 32) |
                                   esp_random()
                             : options.seed;
    if (rng_state == 0) rng_state = 1;

    int32_t token = prompt_tokens[0];
    Status result = Status::kOk;
    const size_t final_position = prompt_token_count + requested - 1;
    for (size_t position = 0; position < final_position; ++position) {
        float* logits = forward(impl_->config, impl_->weights, impl_->state,
                                token, static_cast<int32_t>(position));
        const int32_t next = position + 1 < prompt_token_count
                                 ? prompt_tokens[position + 1]
                                 : sample(logits, impl_->config.vocab_size,
                                          options.temperature, options.top_p,
                                          impl_->candidates, &rng_state);
        if (next == 1 || next == 2) break;

        if (position + 1 >= prompt_token_count) {
            char byte_piece[2] = {};
            const char* piece = decode(impl_->tokenizer, token, next, byte_piece);
            char output[kMaxTokenLength + 1];
            const size_t output_size = safe_piece(piece, output, sizeof(output));
            if (output_size != 0 && !callback(output, output_size, context)) {
                result = Status::kCancelled;
                break;
            }
        }
        token = next;
    }
    heap_caps_free(prompt_tokens);
    return result;
}

}  // namespace tinystories
