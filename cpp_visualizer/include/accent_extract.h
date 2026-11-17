#pragma once

#include <cstdint>

namespace lollypop {

// Saturation-weighted accent colour from an RGBA buffer of any size.
// Returns components in 0..1, lifting dark results so the accent stays
// visible on a dark card.
void extract_accent(const uint8_t* pixels, int width, int height,
                    int stride, int channels,
                    float& out_r, float& out_g, float& out_b);

// Plain average colour, matching `color_extractor._average_color`:
// transparent pixels (alpha == 0) are skipped, components returned 0..255.
void extract_average(const uint8_t* pixels, int width, int height,
                     int stride, int channels,
                     float& out_r, float& out_g, float& out_b);

}  // namespace lollypop