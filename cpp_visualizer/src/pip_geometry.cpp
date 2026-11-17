#include "pip_geometry.h"

#include <algorithm>
#include <cmath>
#include <cstddef>

namespace lollypop {

namespace {
constexpr float TWO_PI = 6.28318530717958647692f;
}  // namespace

std::vector<float> dial_arc_points(
    float cx, float cy, float radius, float start, float sweep,
    float wave_amp_base, float value, float wave_length, float phase) {
    std::vector<float> out;
    if (!(sweep > 0.002f) || radius <= 0.0f || wave_length <= 0.0f) {
        return out;
    }

    // Mirror PipVolumeDial._on_draw exactly.
    const int steps = std::max(28, static_cast<int>(sweep * 22.0f));
    out.reserve(static_cast<std::size_t>(steps + 1) * 2);

    const float amplitude = wave_amp_base * (0.25f + 0.75f * value);
    const float edge_span = std::max(1.0f, static_cast<float>(steps) * 0.07f);

    for (int index = 0; index <= steps; ++index) {
        const float fraction = static_cast<float>(index) / static_cast<float>(steps);
        const float angle = start + sweep * fraction;

        const int edge_index = std::min(index, steps - index);
        const float edge = std::min(1.0f, static_cast<float>(edge_index) / edge_span);
        const float local = amplitude * edge;

        const float wave = local * std::sin(
            (fraction * sweep * radius / wave_length) * TWO_PI + phase);

        const float current = radius + wave;
        out.push_back(cx + current * std::cos(angle));
        out.push_back(cy + current * std::sin(angle));
    }
    return out;
}

std::vector<float> wave_line_points(
    float start, float filled_end, float centre,
    float wave_amplitude, float wave_length, float phase, float step) {
    std::vector<float> out;
    if (!(filled_end > start + 0.5f) || wave_length <= 0.0f) {
        return out;
    }
    if (!(step > 0.0f)) {
        step = 2.0f;
    }

    // Mirror WaveSlider._on_draw: sampled every `step` px, then a flat
    // segment into the thumb position.
    out.reserve(
        static_cast<std::size_t>((filled_end - start) / step) * 2 + 4);
    for (float x = start; x < filled_end; x += step) {
        const float y = centre +
            std::sin((x / wave_length) * TWO_PI + phase) * wave_amplitude;
        out.push_back(x);
        out.push_back(y);
    }
    out.push_back(filled_end);
    out.push_back(centre);
    return out;
}

}  // namespace lollypop
