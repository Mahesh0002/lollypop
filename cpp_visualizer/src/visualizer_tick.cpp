#include "visualizer_types.h"
#include "mel_filterbank.h"
#include "particle_system.h"
#include <algorithm>
#include <cmath>

namespace lollypop {

// --- Internal helpers ------------------------------------------------------

inline float lerp(float a, float b, float t) {
    return a + (b - a) * t;
}

inline float smooth_factor(float target, float current, bool live) {
    if (!live) return STOP_FACTOR;
    return (target > current) ? ATTACK_FACTOR : RELEASE_FACTOR;
}

// Synthetic fallback (matches Python exactly)
void synthetic_fallback(std::array<float, POINT_COUNT>& target,
                        float phase, float max_value) {
    for (int i = 0; i < POINT_COUNT; ++i) {
        float x = float(i) / float(POINT_COUNT - 1);
        float envelope = 0.45f + 0.55f * std::pow(std::sin(M_PI * x), 0.8f);
        float wave = SYNTH_BASE
                   + SYNTH_AMP1 * std::sin(x * SYNTH_FREQ1 + phase)
                   + SYNTH_AMP2 * std::sin(x * SYNTH_FREQ2 - phase * 0.72f)
                   + SYNTH_AMP3 * std::sin(x * SYNTH_FREQ3 + phase * 1.3f);
        target[i] = max_value * std::clamp(wave * envelope, SYNTH_ENVELOPE_MIN, SYNTH_ENVELOPE_MAX);
    }
}

// --- Public API ------------------------------------------------------------

// Main tick function: call once per frame (16ms)
void visualizer_tick(VisualizerState& state, const VisualizerConfig& config,
                     double now) {
    // Update phase for synthetic animation
    state.phase += SYNTH_PHASE_INC;
    if (state.phase > 2.0f * M_PI) state.phase -= 2.0f * M_PI;

    // Determine if we have real data (cava or spectrum)
    bool real_data = state.live && state.real_data &&
                     (now - state.last_real_time) < 1.0;

    // If not live and settled, stop early
    if (!state.live) {
        bool settled = true;
        for (int i = 0; i < POINT_COUNT; ++i) {
            if (state.points[i] >= 0.5f) { settled = false; break; }
        }
        if (settled && state.particle_count == 0) {
            state.points.fill(0.0f);
            return;
        }
    }

    // Compute target for each point
    if (!state.live) {
        // Paused/stopped: ease to zero.  Python `_tick` never animates the
        // synthetic wave while stopped, so neither do we.
        state.target.fill(0.0f);
    } else if (real_data) {
        // Target already set by cava/spectrum callback
    } else {
        synthetic_fallback(state.target, state.phase, MAX_VALUE);
    }

    // Exponential smoothing
    for (int i = 0; i < POINT_COUNT; ++i) {
        float factor = smooth_factor(state.target[i], state.points[i], state.live);
        state.points[i] += (state.target[i] - state.points[i]) * factor;
    }

    // Update particles if in particle mode
    if (config.mode == 1 && (state.live || state.particle_count > 0)) {
        update_particles(state, config, state.points);
    }
}

// Called when cava frame arrives (already on main thread via GLib.idle)
void visualizer_set_cava_target(VisualizerState& state,
                                const float* values, int count) {
    if (!state.live) return;

    // Resample to POINT_COUNT if needed
    if (count != POINT_COUNT) {
        if (count == 0) {
            state.target.fill(0.0f);
            return;
        }
        float last = float(count - 1);
        for (int i = 0; i < POINT_COUNT; ++i) {
            float pos = float(i) * last / float(POINT_COUNT - 1);
            int lo = int(pos);
            int hi = std::min(count - 1, lo + 1);
            float frac = pos - float(lo);
            state.target[i] = std::clamp(
                lerp(values[lo], values[hi], frac), 0.0f, MAX_VALUE);
        }
    } else {
        for (int i = 0; i < POINT_COUNT; ++i) {
            state.target[i] = std::clamp(values[i], 0.0f, MAX_VALUE);
        }
    }

    state.last_real_time = 0.0;  // will be set by caller with actual time
    state.real_data = true;
    state.seen_cava = true;
    state.cava_frames++;

    // Track zero frames
    float max_val = 0.0f;
    for (int i = 0; i < POINT_COUNT; ++i) {
        if (state.target[i] > max_val) max_val = state.target[i];
    }
    if (max_val == 0.0f) state.cava_zero_frames++;
    else state.cava_zero_frames = 0;
}

}  // namespace lollypop