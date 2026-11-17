#pragma once

#include <array>
#include <cmath>
#include <algorithm>

namespace lollypop {

// Mel scale conversion (not constexpr due to log10/pow)
inline float hz_to_mel(float hz) {
    return 2595.0f * std::log10(1.0f + hz / 700.0f);
}

inline float mel_to_hz(float mel) {
    return 700.0f * (std::pow(10.0f, mel / 2595.0f) - 1.0f);
}

// Pre-computed at startup (defined in mel_remap.cpp)
std::array<std::array<float, 513>, MEL_BANDS> build_mel_filterbank();

// Center frequencies for tilt curve (defined in mel_remap.cpp)
std::array<float, MEL_BANDS> build_mel_center_hz();

}  // namespace lollypop