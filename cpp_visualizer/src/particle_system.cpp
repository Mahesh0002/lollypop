#include "visualizer_types.h"
#include <algorithm>

namespace lollypop {

// Fast xorshift RNG for particle spawning (much lighter than mt19937).
// NOTE: shared static state is fine because the visualizer runs on the GTK
// main thread only.
static uint32_t& get_rng_state() {
    static uint32_t state = 0x9E3779B9u;
    return state;
}
static inline float rng_float() {
    uint32_t& s = get_rng_state();
    s ^= s << 13;
    s ^= s >> 17;
    s ^= s << 5;
    return float(s) / float(0xFFFFFFFFu);
}
static inline float rng_float_range(float min_val, float max_val) {
    return min_val + rng_float() * (max_val - min_val);
}

// Find the first dead particle slot.  MAX_PARTICLES is tiny (256) so a linear
// scan is cheap and avoids the bugs of a shared free list (duplicate slots,
// unbounded growth, stale slots across visualizer states).
static int find_dead_slot(const VisualizerState& state) {
    for (int p = 0; p < MAX_PARTICLES; ++p) {
        if (state.particles[p].life <= 0.0f) {
            return p;
        }
    }
    return -1;
}

// Spawn particles from audio bars
void spawn_particles(VisualizerState& state, const VisualizerConfig& config,
                     const std::array<float, POINT_COUNT>& points,
                     int width, int height, int max_spawn) {
    int spawned = 0;
    for (int i = 0; i < POINT_COUNT && spawned < max_spawn; ++i) {
        float h = points[i] / MAX_VALUE * static_cast<float>(height);
        if (h < static_cast<float>(height) * 0.1f) continue;  // Only spawn from significant bars

        int slot = find_dead_slot(state);
        if (slot == -1) break;

        // Spawn at top of bar
        float x = float(i) / float(POINT_COUNT - 1) * static_cast<float>(width);
        float y = static_cast<float>(height) - h;

        state.particles[slot] = {
            x, y,
            rng_float_range(-50.0f, 50.0f),          // vx
            -rng_float_range(50.0f, 250.0f),         // vy (upward)
            rng_float_range(0.4f, 1.2f),             // life (0.4-1.2s)
            rng_float_range(1.0f, 4.0f),             // size (1-4px)
            1.0f,                              // alpha
            config.accent_r * rng_float_range(0.5f, 1.0f),
            config.accent_g * rng_float_range(0.5f, 1.0f),
            config.accent_b * rng_float_range(0.5f, 1.0f)
        };
        state.particle_count++;
        spawned++;
    }
}

// Update particle physics (full signature with dimensions)
void update_particles(VisualizerState& state, const VisualizerConfig& config,
                      const std::array<float, POINT_COUNT>& points,
                      int width, int height, bool spawn) {
    constexpr float GRAVITY = 300.0f;
    constexpr float DRAG = 0.98f;
    constexpr float DT = FRAME_MS / 1000.0f;  // 0.016s

    int alive = 0;

    for (int p = 0; p < MAX_PARTICLES; ++p) {
        auto& pt = state.particles[p];
        if (pt.life <= 0.0f) {
            continue;
        }

        // Physics
        pt.vy += GRAVITY * DT;
        pt.vx *= DRAG;
        pt.vy *= DRAG;
        pt.x += pt.vx * DT;
        pt.y += pt.vy * DT;

        // Fade out
        pt.life -= DT;
        pt.alpha = std::max(0.0f, pt.life / 1.2f);

        // Shrink
        pt.size *= 0.995f;

        // Bounce off bottom
        if (pt.y > static_cast<float>(height) - pt.size) {
            pt.y = static_cast<float>(height) - pt.size;
            pt.vy *= -0.4f;
            pt.vx *= 0.8f;
        }

        // Bounce off sides
        if (pt.x < pt.size) {
            pt.x = pt.size;
            pt.vx *= -0.5f;
        } else if (pt.x > static_cast<float>(width) - pt.size) {
            pt.x = static_cast<float>(width) - pt.size;
            pt.vx *= -0.5f;
        }

        if (pt.life > 0.0f) alive++;
    }

    state.particle_count = alive;

    // Spawn new particles from audio
    if (spawn && config.mode == 1) {
        // Spawn up to 8 per frame from loudest bars
        int max_spawn = std::min(8, MAX_PARTICLES - alive);
        if (max_spawn > 0) {
            spawn_particles(state, config, points, width, height, max_spawn);
        }
    }
}

// Particle update called from visualizer_tick (simplified signature)
void update_particles(VisualizerState& state, const VisualizerConfig& config,
                      const std::array<float, POINT_COUNT>& points) {
    // Default dimensions - in real use, pass from on_draw
    int width = 300;   // typical panel width
    int height = 60;   // typical visualizer height

    // Determine if we should spawn (live and have audio)
    bool should_spawn = state.live;
    float max_point = 0.0f;
    for (int i = 0; i < POINT_COUNT; ++i) {
        if (state.points[i] > max_point) max_point = state.points[i];
    }
    should_spawn = should_spawn && (max_point > MAX_VALUE * 0.1f);

    update_particles(state, config, points, width, height, should_spawn);
}

}  // namespace lollypop
