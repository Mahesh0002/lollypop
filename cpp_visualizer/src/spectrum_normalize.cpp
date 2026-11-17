#include "visualizer_types.h"
#include "mel_remap.h"
#include <algorithm>
#include <array>
#include <cmath>

namespace lollypop {

namespace {

// Python: tilt = 0.35 + 1.30 * i / (count - 1)
inline float tilt_for(int i, int count) {
    return 0.35f + 1.30f * float(i) / float(std::max(1, count - 1));
}

// Python: target_gain = min(2.0, 0.62 / reference) if reference > 0.08 else 1
// Precomputed pow table for level^0.7f (256 entries, level in [0,1])
inline float pow_table[256];
inline bool pow_table_init = []() {
    for (int i = 0; i < 256; ++i) {
        pow_table[i] = std::pow(float(i) / 255.0f, 0.7f);
    }
    return true;
}();

inline float fast_pow07(float level) {
    int idx = int(level * 255.0f + 0.5f);
    if (idx < 0) idx = 0;
    if (idx > 255) idx = 255;
    return pow_table[idx];
}

inline float next_gain(const float* values, int count, float gain) {
    const int n = std::min(count, POINT_COUNT);
    // No input to measure: keep the current gain unchanged.
    if (n <= 0) {
        return gain;
    }
    std::array<float, POINT_COUNT> sorted{};
    std::copy_n(values, n, sorted.begin());
    // nth_element is O(n) vs std::sort O(n log n) for 90th percentile
    int nth = int(0.90f * float(n - 1));
    if (nth < 0) nth = 0;
    if (nth >= n) nth = n - 1;
    std::nth_element(sorted.begin(), sorted.begin() + nth, sorted.begin() + n);
    const float reference =
        n > 0 ? sorted[nth] : 0.0f;
    const float target = reference > GAIN_MIN_REFERENCE
        ? std::min(GAIN_MAX, GAIN_TARGET_LEVEL / reference)
        : 1.0f;
    return gain + (target - gain) * GAIN_ADAPT_RATE;
}

// Tilt + gate + gain on 0..1 values, output scaled to MAX_VALUE.
// Order matters: Python computes the 90th-percentile reference from the
// *tilted* values, not the raw input.
void apply_curve(const float* values, int count,
                 std::array<float, POINT_COUNT>& out, float& gain) {
    const int n = std::min(count, POINT_COUNT);
    std::array<float, POINT_COUNT> tilted{};
    for (int i = 0; i < n; ++i) {
        const float gated = values[i] >= NOISE_GATE ? values[i] : 0.0f;
        tilted[i] = std::min(1.0f, gated * tilt_for(i, n));
    }
    gain = next_gain(tilted.data(), n, gain);
    for (int i = 0; i < n; ++i) {
        out[i] = std::min(1.0f, tilted[i] * gain) * MAX_VALUE;
    }
    for (int i = n; i < POINT_COUNT; ++i) {
        out[i] = 0.0f;
    }
}

}  // namespace

// Linear resample of an arbitrary-length band list down to POINT_COUNT.
void resample_to_points(const float* in, int count,
                        std::array<float, POINT_COUNT>& out) {
    if (count <= 0) {
        out.fill(0.0f);
        return;
    }
    if (count == POINT_COUNT) {
        std::copy_n(in, POINT_COUNT, out.begin());
        return;
    }
    const float last = float(count - 1);
    for (int i = 0; i < POINT_COUNT; ++i) {
        const float pos = float(i) * last / float(POINT_COUNT - 1);
        const int lo = int(pos);
        const int hi = std::min(count - 1, lo + 1);
        const float frac = pos - float(lo);
        out[i] = in[lo] * (1.0f - frac) + in[hi] * frac;
    }
}

void tilt_gain(const float* values, int count,
               std::array<float, POINT_COUNT>& out_values, float& gain) {
    apply_curve(values, count, out_values, gain);
}

void normalize_spectrum(const float* magnitude, int magnitude_len,
                        std::array<float, MEL_BANDS>& out_values,
                        float& gain, bool& seen_spectrum) {
    if (magnitude_len <= 0) {
        out_values.fill(0.0f);
        return;
    }

    // 1. Mel remap only makes sense for a linear FFT (513 bins).  Anything
    //    else (GStreamer is configured with 64 bands) is already perceptual,
    //    so it is resampled as-is.
    std::array<float, POINT_COUNT> bands{};
    if (magnitude_len == 513) {
        std::array<float, MEL_BANDS> mel{};
        mel_remap(magnitude, magnitude_len, mel);
        std::copy_n(mel.begin(), MEL_BANDS, bands.begin());
    } else {
        resample_to_points(magnitude, magnitude_len, bands);
    }

    // 2. dB normalisation + root curve (same as Python `_on_spectrum_message`).
    constexpr float floor_db = SPECTRUM_FLOOR;
    constexpr float inv_floor = -1.0f / floor_db;
    for (int i = 0; i < POINT_COUNT; ++i) {
        float level = (bands[i] - floor_db) * inv_floor;
        level = std::clamp(level, 0.0f, 1.0f);
        bands[i] = fast_pow07(level);
    }

    if (!seen_spectrum) {
        seen_spectrum = true;
    }

    // 3. Tilt, gate, adaptive gain.
    std::array<float, POINT_COUNT> curve{};
    apply_curve(bands.data(), POINT_COUNT, curve, gain);
    std::copy_n(curve.begin(), MEL_BANDS, out_values.begin());
}

}  // namespace lollypop