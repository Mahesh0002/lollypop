#pragma once

#include "visualizer_types.h"
#include "mel_filterbank.h"

namespace lollypop {

// Remap linear FFT spectrum (513 bins) to mel bands (64)
void mel_remap(const float* spectrum, int spectrum_len,
               std::array<float, MEL_BANDS>& out_mel);

// Non-zero index ranges for each mel band (computed at startup)
struct MelBandIndices {
    int start = 513;
    int end = 0;
};

extern std::array<MelBandIndices, MEL_BANDS> MEL_BAND_INDICES;

// Initialize band indices (call once at startup)
void init_mel_band_indices();

// Fast remap using pre-computed ranges
void mel_remap_fast(const float* spectrum, int spectrum_len,
                    std::array<float, MEL_BANDS>& out_mel);

}  // namespace lollypop