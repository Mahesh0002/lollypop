#include "visualizer_types.h"
#include <charconv>
#include <cstring>
#include <algorithm>

namespace lollypop {

// Parse cava output: ";"-separated integers 0..1000
// Returns number of values parsed (max POINT_COUNT)
int parse_cava_frame(const char* data, size_t len,
                     std::array<float, POINT_COUNT>& out_values) {
    if (!data || len == 0) {
        out_values.fill(0.0f);
        return 0;
    }

    int count = 0;
    const char* start = data;
    const char* end = data + len;

    while (start < end && count < POINT_COUNT) {
        // Find next semicolon or end
        const char* semi = static_cast<const char*>(std::memchr(start, ';', end - start));
        const char* token_end = semi ? semi : end;

        // Parse integer (fast path: no exceptions)
        int value = 0;
        auto result = std::from_chars(start, token_end, value);
        if (result.ec == std::errc()) {
            out_values[count] = std::clamp(float(value), 0.0f, 1000.0f);
            count++;
        }

        if (!semi) break;
        start = semi + 1;
    }

    // Fill remaining with zeros
    for (int i = count; i < POINT_COUNT; ++i) {
        out_values[i] = 0.0f;
    }

    return count;
}

// Parse with pre-allocated buffer (avoids allocation in hot path)
int parse_cava_frame_unsafe(const char* data, size_t len, float* out_values) {
    if (!data || len == 0) {
        std::fill_n(out_values, POINT_COUNT, 0.0f);
        return 0;
    }

    int count = 0;
    const char* start = data;
    const char* end = data + len;

    while (start < end && count < POINT_COUNT) {
        const char* semi = static_cast<const char*>(std::memchr(start, ';', end - start));
        const char* token_end = semi ? semi : end;

        int value = 0;
        auto result = std::from_chars(start, token_end, value);
        if (result.ec == std::errc()) {
            out_values[count] = std::clamp(float(value), 0.0f, 1000.0f);
            count++;
        }

        if (!semi) break;
        start = semi + 1;
    }

    for (int i = count; i < POINT_COUNT; ++i) {
        out_values[i] = 0.0f;
    }
    return count;
}

}  // namespace lollypop