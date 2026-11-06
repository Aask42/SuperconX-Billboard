#pragma once

#include <cstddef>
#include <cstdint>

namespace tinystories {

enum class Status : uint8_t {
    kOk,
    kInvalidArgument,
    kIoError,
    kInvalidFormat,
    kOutOfMemory,
    kPromptTooLong,
    kNotInitialized,
    kCancelled,
};

struct GenerationOptions {
    uint32_t max_tokens = 128;
    float temperature = 0.8f;
    float top_p = 0.9f;
    uint64_t seed = 0;
};

using TextCallback = bool (*)(const char* text, size_t length, void* context);

class Adapter {
public:
    static constexpr uint32_t kMaxGenerationTokens = 512;
    static constexpr size_t kMaxPromptBytes = 4096;

    Adapter() = default;
    ~Adapter();

    Adapter(const Adapter&) = delete;
    Adapter& operator=(const Adapter&) = delete;

    // Both files must be llama2.c-format files on the mounted /data volume.
    Status initialize(const char* model_path, const char* tokenizer_path);
    void shutdown();
    bool initialized() const;

    // Emits decoded token chunks; return false from the callback to cancel.
    // Generation is capped by both kMaxGenerationTokens and the model context.
    Status generate(const char* prompt, const GenerationOptions& options,
                    TextCallback callback, void* context);

private:
    struct Impl;
    Impl* impl_ = nullptr;
};

}  // namespace tinystories
