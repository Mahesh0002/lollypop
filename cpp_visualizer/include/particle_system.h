#pragma once

#include "visualizer_types.h"

namespace lollypop {

// Spawn particles from audio bars
void spawn_particles(VisualizerState& state, const VisualizerConfig& config,
                     const std::array<float, POINT_COUNT>& points,
                     int width, int height, int max_spawn);

// Update particle physics (full signature with dimensions)
void update_particles(VisualizerState& state, const VisualizerConfig& config,
                      const std::array<float, POINT_COUNT>& points,
                      int width, int height, bool spawn);

// Particle update called from visualizer_tick (simplified signature)
void update_particles(VisualizerState& state, const VisualizerConfig& config,
                      const std::array<float, POINT_COUNT>& points);

}  // namespace lollypop