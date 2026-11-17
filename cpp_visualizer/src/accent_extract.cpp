#include "visualizer_types.h"
#include <algorithm>

namespace lollypop {

namespace {

inline int clamp_channel(int value) {
    return std::max(0, std::min(255, value));
}

}  // namespace

// Saturation-weighted accent.  Same curve the original 32x32 version used,
// but the scan is generalised so callers can pass the 64x64 buffer that
// `color_extractor._average_color` already builds (or any other size).
void extract_accent(const uint8_t* pixels, int width, int height,
                    int stride, int channels,
                    float& out_r, float& out_g, float& out_b) {
    if (!pixels || width <= 0 || height <= 0 || channels < 3) {
        out_r = out_g = out_b = 0.0f;
        return;
    }

    double tr = 0.0, tg = 0.0, tb = 0.0;
    double ar = 0.0, ag = 0.0, ab = 0.0;
    double total_weight = 0.0;
    long long count = 0;

    for (int y = 0; y < height; ++y) {
        const uint8_t* row = pixels + y * stride;
        for (int x = 0; x < width; ++x) {
            const uint8_t* px = row + x * channels;
            if (channels == 4 && px[3] == 0) {
                continue;  // transparent pixel
            }
            const int r = px[0];
            const int g = px[1];
            const int b = px[2];

            ar += r;
            ag += g;
            ab += b;
            ++count;

            const int peak = std::max({r, g, b});
            const int low = std::min({r, g, b});
            if (peak > 0) {
                const float sat = float(peak - low) / float(peak);
                if (sat > 0.2f) {
                    const float weight = sat * sat;
                    tr += r * weight;
                    tg += g * weight;
                    tb += b * weight;
                    total_weight += weight;
                }
            }
        }
    }

    if (total_weight > 0.0) {
        out_r = float(tr / total_weight / 255.0);
        out_g = float(tg / total_weight / 255.0);
        out_b = float(tb / total_weight / 255.0);
    } else if (count > 0) {
        out_r = float(ar / double(count) / 255.0);
        out_g = float(ag / double(count) / 255.0);
        out_b = float(ab / double(count) / 255.0);
    } else {
        out_r = out_g = out_b = 0.0f;
        return;
    }

    // Lift dark colours so the accent reads against a dark card.
    const float peak = std::max({out_r, out_g, out_b});
    if (peak > 0.0f && peak < 0.6f) {
        const float lift = std::min(4.0f, 0.6f / peak);
        out_r = std::min(1.0f, out_r * lift);
        out_g = std::min(1.0f, out_g * lift);
        out_b = std::min(1.0f, out_b * lift);
    }
}

void extract_average(const uint8_t* pixels, int width, int height,
                     int stride, int channels,
                     float& out_r, float& out_g, float& out_b) {
    if (!pixels || width <= 0 || height <= 0 || channels < 3) {
        out_r = out_g = out_b = 0.0f;
        return;
    }

    long long tr = 0, tg = 0, tb = 0, count = 0;
    for (int y = 0; y < height; ++y) {
        const uint8_t* row = pixels + y * stride;
        for (int x = 0; x < width; ++x) {
            const uint8_t* px = row + x * channels;
            if (channels == 4 && px[3] == 0) {
                continue;
            }
            tr += px[0];
            tg += px[1];
            tb += px[2];
            ++count;
        }
    }

    if (count == 0) {
        out_r = out_g = out_b = 0.0f;
        return;
    }
    out_r = float(tr) / float(count);
    out_g = float(tg) / float(count);
    out_b = float(tb) / float(count);
}

}  // namespace lollypop