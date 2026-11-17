#pragma once

#include "visualizer_types.h"
#include <array>

namespace lollypop {

// Full pipeline for raw GStreamer "spectrum" magnitudes (dB, any band count).
// Applies the dB floor, root curve, mel remap (only for 513 FFT bins),
// tilt, noise gate and adaptive gain, and scales to MAX_VALUE.
void normalize_spectrum(const float* magnitude, int magnitude_len,
                        std::array<float, MEL_BANDS>& out_values,
                        float& gain, bool& seen_spectrum);

// Second half of the pipeline, for values already normalised to 0..1 by the
// caller.  Lollypop's `_on_spectrum_message` applies the dB floor + root
// curve before coalescing frames, so the widget hands us 0..1 values.
// This mirrors Python `_apply_spectrum` exactly (tilt, gate, adaptive gain).
void tilt_gain(const float* values, int count,
               std::array<float, POINT_COUNT>& out_values,
               float& gain);

// Linear resample of an arbitrary-length band list to POINT_COUNT values.
void resample_to_points(const float* in, int count,
                        std::array<float, POINT_COUNT>& out_points);

}  // namespace lollypop