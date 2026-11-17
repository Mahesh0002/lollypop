#pragma once

#include "visualizer_types.h"
#include <array>

namespace lollypop {

// O(n) moving average using prefix sum
void moving_average(const std::array<float, POINT_COUNT>& input,
                    std::array<float, POINT_COUNT>& output,
                    int window = SMOOTHING_WINDOW);

// In-place version (output can alias input)
void moving_average_inplace(std::array<float, POINT_COUNT>& data,
                            int window = SMOOTHING_WINDOW);

}  // namespace lollypop