#pragma once

#include "visualizer_types.h"

namespace lollypop {

// Main tick function: call once per frame (16ms)
void visualizer_tick(VisualizerState& state, const VisualizerConfig& config,
                     double now);

// Called when cava frame arrives (already on main thread via GLib.idle)
void visualizer_set_cava_target(VisualizerState& state,
                                const float* values, int count);

}  // namespace lollypop