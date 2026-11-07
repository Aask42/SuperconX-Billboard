/*
 * Thin C-ABI shim around the SHIPPED tinystories::Adapter so a ctypes host test
 * can drive the exact firmware parsing/generation code (compiled with
 * -DTINYSTORIES_HOST_BUILD). This file adds no logic of its own beyond marshaling
 * arguments and collecting the generated text — it must not reimplement anything
 * the adapter does. See tests/unit/test_tinystories_adapter.py.
 */
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <new>

#include "tinystories_adapter.hpp"

using tinystories::Adapter;
using tinystories::GenerationOptions;
using tinystories::Status;

namespace {

// Context passed through Adapter::generate's callback. Captures the emitted
// bytes into a caller-owned buffer and optionally cancels after N callbacks so
// the test can exercise clean cancellation.
struct GenSink {
    char* buffer;
    size_t capacity;   // size of `buffer`, including room for the NUL
    size_t length;     // bytes written so far (excluding NUL)
    int cancel_after;  // cancel once calls >= this; < 0 means never
    int calls;         // number of callback invocations
    int truncated;     // set if output did not fit in `buffer`
};

bool collect(const char* text, size_t length, void* context) {
    GenSink* sink = static_cast<GenSink*>(context);
    sink->calls += 1;
    if (sink->buffer != nullptr) {
        if (sink->length + length + 1 <= sink->capacity) {
            memcpy(sink->buffer + sink->length, text, length);
            sink->length += length;
            sink->buffer[sink->length] = '\0';
        } else {
            sink->truncated = 1;
        }
    }
    if (sink->cancel_after >= 0 && sink->calls >= sink->cancel_after) {
        return false;  // cancel
    }
    return true;
}

}  // namespace

extern "C" {

void* ts_adapter_new(void) { return new (std::nothrow) Adapter(); }

void ts_adapter_delete(void* handle) {
    delete static_cast<Adapter*>(handle);
}

int ts_adapter_initialize(void* handle, const char* model_path,
                          const char* tokenizer_path) {
    Adapter* adapter = static_cast<Adapter*>(handle);
    return static_cast<int>(adapter->initialize(model_path, tokenizer_path));
}

int ts_adapter_initialized(void* handle) {
    return static_cast<Adapter*>(handle)->initialized() ? 1 : 0;
}

void ts_adapter_shutdown(void* handle) {
    static_cast<Adapter*>(handle)->shutdown();
}

// Returns the Status as an int. `out`/`out_cap` receive the decoded text (NUL
// terminated). `cancel_after` < 0 disables cancellation. `out_calls`,
// `out_len`, `out_truncated` are optional (may be null).
int ts_adapter_generate(void* handle, const char* prompt, unsigned int max_tokens,
                        float temperature, float top_p, unsigned long long seed,
                        char* out, size_t out_cap, int cancel_after,
                        int* out_calls, size_t* out_len, int* out_truncated) {
    Adapter* adapter = static_cast<Adapter*>(handle);
    GenerationOptions options;
    options.max_tokens = max_tokens;
    options.temperature = temperature;
    options.top_p = top_p;
    options.seed = seed;
    GenSink sink{out, out_cap, 0, cancel_after, 0, 0};
    if (out != nullptr && out_cap > 0) out[0] = '\0';
    const Status status = adapter->generate(prompt, options, collect, &sink);
    if (out_calls != nullptr) *out_calls = sink.calls;
    if (out_len != nullptr) *out_len = sink.length;
    if (out_truncated != nullptr) *out_truncated = sink.truncated;
    return static_cast<int>(status);
}

// Expose the generation caps so the test asserts against the shipped values
// instead of hard-coding them.
unsigned int ts_max_generation_tokens(void) {
    return Adapter::kMaxGenerationTokens;
}

size_t ts_max_prompt_bytes(void) { return Adapter::kMaxPromptBytes; }

}  // extern "C"
