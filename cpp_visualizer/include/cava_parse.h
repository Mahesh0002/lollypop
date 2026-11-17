#pragma once

#include "visualizer_types.h"
#include <array>

namespace lollypop {

// Parse cava output: ";"-separated integers 0..1000
// Returns number of values parsed (max POINT_COUNT)
int parse_cava_frame(const char* data, size_t len,
                     std::array<float, POINT_COUNT>& out_values);

// Parse with pre-allocated buffer (avoids allocation in hot path)
int parse_cava_frame_unsafe(const char* data, size_t len, float* out_values);

}  // namespace lollypop