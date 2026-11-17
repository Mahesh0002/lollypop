#include "visualizer_types.h"
#include "mel_filterbank.h"
#include <algorithm>

namespace lollypop {

// Global pre-computed filterbank
const std::array<std::array<float, 513>, MEL_BANDS> MEL_FILTERBANK = build_mel_filterbank();
const std::array<float, MEL_BANDS> MEL_CENTER_HZ = build_mel_center_hz();

// Non-zero index ranges for each mel band (computed at startup)
struct MelBandIndices {
    int start = 513;
    int end = 0;
};

std::array<MelBandIndices, MEL_BANDS> MEL_BAND_INDICES;

// --- Mel filterbank construction ---

std::array<std::array<float, 513>, MEL_BANDS> build_mel_filterbank() {
    std::array<std::array<float, 513>, MEL_BANDS> filterbank{};

    // Mel-spaced band edges
    std::array<float, MEL_BANDS + 1> mel_edges;
    for (int i = 0; i <= MEL_BANDS; ++i) {
        float mel_min = hz_to_mel(20.0f);
        float mel_max = hz_to_mel(20000.0f);
        mel_edges[i] = mel_min + i * (mel_max - mel_min) / MEL_BANDS;
    }

    // Convert to Hz then FFT bins
    std::array<int, MEL_BANDS + 1> bin_edges;
    for (int i = 0; i <= MEL_BANDS; ++i) {
        float mel_min = hz_to_mel(20.0f);
        float mel_max = hz_to_mel(20000.0f);
        float mel_edge = mel_min + i * (mel_max - mel_min) / MEL_BANDS;
        float hz = mel_to_hz(mel_edge);
        bin_edges[i] = std::clamp(int(hz * 1024 / 44100.0f), 0, 512);
    }

    // Ensure strictly increasing
    for (int i = 1; i <= MEL_BANDS; ++i) {
        if (bin_edges[i] <= bin_edges[i - 1]) {
            bin_edges[i] = bin_edges[i - 1] + 1;
        }
    }

    // Build triangular filters
    for (int b = 0; b < MEL_BANDS; ++b) {
        int start = bin_edges[b];
        int end = std::min(512, bin_edges[b + 1]);
        int center = (start + end) / 2;

        for (int k = start; k <= end; ++k) {
            if (k < center) {
                filterbank[b][k] = float(k - start) / float(center - start);
            } else {
                filterbank[b][k] = float(end - k) / float(end - center);
            }
        }
    }

    return filterbank;
}

// Center frequencies for tilt curve
std::array<float, MEL_BANDS> build_mel_center_hz() {
    std::array<float, MEL_BANDS> centers;
    const float mel_min = hz_to_mel(20.0f);
    const float mel_max = hz_to_mel(20000.0f);

    for (int i = 0; i < MEL_BANDS; ++i) {
        float mel_center = hz_to_mel(20.0f) + (i + 0.5f) * (hz_to_mel(20000.0f) - hz_to_mel(20.0f)) / MEL_BANDS;
        centers[i] = mel_to_hz(mel_center);
    }
    return centers;
}

// Remap linear FFT spectrum (513 bins) to mel bands (64)
void mel_remap(const float* spectrum, int spectrum_len,
               std::array<float, MEL_BANDS>& out_mel) {
    int n_bins = std::min(spectrum_len, 513);

    for (int b = 0; b < MEL_BANDS; ++b) {
        float sum = 0.0f;
        float weight_sum = 0.0f;

        for (int k = 0; k < n_bins; ++k) {
            float w = MEL_FILTERBANK[b][k];
            if (w > 0.0f) {
                sum += spectrum[k] * w;
                weight_sum += w;
            }
        }
        out_mel[b] = (weight_sum > 0.0f) ? sum / weight_sum : 0.0f;
    }
}

// Initialize band indices (call once at startup)
void init_mel_band_indices() {
    for (int b = 0; b < MEL_BANDS; ++b) {
        int start = 513, end = 0;
        for (int k = 0; k < 513; ++k) {
            if (MEL_FILTERBANK[b][k] > 0.0f) {
                start = std::min(start, k);
                end = std::max(end, k);
            }
        }
        MEL_BAND_INDICES[b] = {start, end};
    }
}

// Fast remap using pre-computed ranges
void mel_remap_fast(const float* spectrum, int spectrum_len,
                    std::array<float, MEL_BANDS>& out_mel) {
    int n_bins = std::min(spectrum_len, 513);

    for (int b = 0; b < MEL_BANDS; ++b) {
        const auto& idx = MEL_BAND_INDICES[b];
        if (idx.start >= n_bins) {
            out_mel[b] = 0.0f;
            continue;
        }
        int end = std::min(idx.end, n_bins - 1);
        float sum = 0.0f, weight_sum = 0.0f;

        for (int k = idx.start; k <= end; ++k) {
            float w = MEL_FILTERBANK[b][k];
            if (w > 0.0f) {
                sum += spectrum[k] * w;
                weight_sum += w;
            }
        }
        out_mel[b] = (weight_sum > 0.0f) ? sum / weight_sum : 0.0f;
    }
}

}  // namespace lollypop