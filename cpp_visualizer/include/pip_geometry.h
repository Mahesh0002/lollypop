#pragma once

#include <vector>

// PiP geometry hot paths.
//
// `lollypop/pip/window.py` builds two animated Cairo paths with tight Python
// per-frame loops: the volume dial's wavy arc (`PipVolumeDial._on_draw`) and
// the seek bar's wavy fill (`WaveSlider._on_draw`).  Both ran a Python
// `sin`/`cos` loop at 30/20 fps on the GTK main thread.  These helpers produce
// the identical point sequences as flat `[x0,y0,x1,y1,...]` buffers so the
// Python side only has to issue the Cairo calls.

namespace lollypop {

// Volume dial: points along the accent arc from `start` sweeping `sweep`
// radians.  `wave_amp_base` is the pre-value amplitude constant; the final
// amplitude is scaled by `0.25 + 0.75 * value` and tapered at both ends so the
// wave cannot overshoot the dial ends.  Returns an empty vector when the arc
// is too short to draw.
std::vector<float> dial_arc_points(
    float cx, float cy, float radius, float start, float sweep,
    float wave_amp_base, float value, float wave_length, float phase);

// Seek bar: the wavy accent fill between `start` and `filled_end`, sampled
// every `step` pixels, terminated by a flat segment at `filled_end` to meet the
// thumb.  Returns an empty vector when there is nothing to fill.
std::vector<float> wave_line_points(
    float start, float filled_end, float centre,
    float wave_amplitude, float wave_length, float phase, float step);

}  // namespace lollypop
