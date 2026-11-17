#pragma once

#include <array>
#include <vector>
#include <cstdint>
#include <cmath>

namespace lollypop {

// Compile-time constants
constexpr int POINT_COUNT = 64;
constexpr int MEL_BANDS = 64;

// Ensure template-friendly integral constants
constexpr int POINT_COUNT_I = POINT_COUNT;
constexpr int MEL_BANDS_I = MEL_BANDS;
constexpr float MAX_VALUE = 1000.0f;
constexpr float FRAME_MS = 16.0f;
constexpr float SPECTRUM_FLOOR = -80.0f;
constexpr float LINE_ALPHA = 0.95f;
constexpr float FILL_ALPHA = 0.35f;
constexpr int BLUR_DOWNSCALE = 2;
constexpr int SMOOTHING_WINDOW = 2;  // 5-point moving average

// Smoothing factors (matching Python)
constexpr float ATTACK_FACTOR = 0.28f;
constexpr float RELEASE_FACTOR = 0.14f;
constexpr float STOP_FACTOR = 0.38f;

// Synthetic fallback
constexpr float SYNTH_PHASE_INC = 0.075f;
constexpr float SYNTH_BASE = 0.50f;
constexpr float SYNTH_AMP1 = 0.22f;
constexpr float SYNTH_FREQ1 = 13.0f;
constexpr float SYNTH_AMP2 = 0.15f;
constexpr float SYNTH_FREQ2 = 29.0f;
constexpr float SYNTH_AMP3 = 0.10f;
constexpr float SYNTH_FREQ3 = 47.0f;
constexpr float SYNTH_ENVELOPE_MIN = 0.04f;
constexpr float SYNTH_ENVELOPE_MAX = 0.92f;

// Gain control
constexpr float GAIN_ADAPT_RATE = 0.25f;
constexpr float GAIN_MAX = 2.0f;
constexpr float GAIN_TARGET_LEVEL = 0.62f;
constexpr float GAIN_MIN_REFERENCE = 0.08f;

// Tilt curve (mel-scale compensation)
constexpr float TILT_BASE = 0.65f;
constexpr float TILT_RANGE = 0.50f;

// Noise gate
constexpr float NOISE_GATE = 0.04f;

// Particle system
constexpr int MAX_PARTICLES = 256;

// Pre-computed mel filterbank (64 bands x 513 FFT bins)
extern const std::array<std::array<float, 513>, MEL_BANDS> MEL_FILTERBANK;
extern const std::array<float, MEL_BANDS> MEL_CENTER_HZ;

// Visualizer state (plain struct for zero-overhead)
struct VisualizerState {
    std::array<float, POINT_COUNT> points{};
    std::array<float, POINT_COUNT> target{};
    float phase = 0.0f;
    bool live = false;
    bool real_data = false;
    double last_real_time = 0.0;
    float gain = 1.0f;
    int spectrum_frames = 0;
    int cava_frames = 0;
    int cava_zero_frames = 0;
    bool seen_cava = false;
    bool seen_spectrum = false;

    // Particle system
    struct Particle {
        float x, y;
        float vx, vy;
        float life;
        float size;
        float alpha;
        float r, g, b;
    };
    std::array<Particle, MAX_PARTICLES> particles{};
    int particle_count = 0;
};

// Configuration (passed from Python)
struct VisualizerConfig {
    float accent_r = 0.65f;
    float accent_g = 0.55f;
    float accent_b = 0.98f;
    int mode = 0;  // 0=area, 1=particles, 2=bars
    float fill_alpha = FILL_ALPHA;
    float line_alpha = LINE_ALPHA;
};

}  // namespace lollypop