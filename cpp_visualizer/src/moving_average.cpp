#include "visualizer_types.h"
#include <algorithm>

namespace lollypop {

// 5-point moving average with clamped edges.
//
// Deliberately matches Python `SimpleVisualizer._smoothed`, which repeats
// the edge sample instead of shrinking the window: the first point averages
// [0, 0, 0, 1, 2] and the last one [n-3, n-2, n-1, n-1, n-1].
void moving_average(const std::array<float, POINT_COUNT>& input,
                    std::array<float, POINT_COUNT>& output,
                    int window) {
    const int win = std::max(0, window);
    const float divisor = float(2 * win + 1);
    for (int i = 0; i < POINT_COUNT; ++i) {
        float total = 0.0f;
        for (int j = -win; j <= win; ++j) {
            const int idx = std::max(0, std::min(POINT_COUNT - 1, i + j));
            total += input[idx];
        }
        output[i] = total / divisor;
    }
}

// In-place version (output can alias input)
void moving_average_inplace(std::array<float, POINT_COUNT>& data,
                            int window = SMOOTHING_WINDOW) {
    std::array<float, POINT_COUNT + 1> prefix{};
    prefix[0] = 0.0f;
    for (int i = 0; i < POINT_COUNT; ++i) {
        prefix[i + 1] = prefix[i] + data[i];
    }

    for (int i = 0; i < POINT_COUNT; ++i) {
        int left = std::max(0, i - window);
        int right = std::min(POINT_COUNT, i + window + 1);
        int count = right - left;
        data[i] = (prefix[right] - prefix[left]) / float(count);
    }
}

// Separable 2D moving average (for particle trails if needed)
// Not used currently but available
void moving_average_2d(float* data, int width, int height, int window) {
    // Horizontal pass
    std::vector<float> temp(width * height);
    for (int y = 0; y < height; ++y) {
        float* row = data + y * width;
        float* trow = temp.data() + y * width;

        float sum = 0.0f;
        for (int x = 0; x < width; ++x) {
            sum += row[x];
            if (x > window) sum -= row[x - window - 1];
            trow[x] = sum / float(std::min(x + window + 1, width) - std::max(0, x - window));
        }
    }

    // Vertical pass
    for (int x = 0; x < width; ++x) {
        float sum = 0.0f;
        for (int y = 0; y < height; ++y) {
            sum += temp[y * width + x];
            if (y > window) sum -= temp[(y - window - 1) * width + x];
            data[y * width + x] = sum / float(std::min(y + window + 1, height) - std::max(0, y - window));
        }
    }
}

}  // namespace lollypop