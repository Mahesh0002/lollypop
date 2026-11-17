# Integrating the C++ Visualizer into Lollypop

`lollypop_visualizer` accelerates the four hottest paths in
`widget_injector.SimpleVisualizer` (30 fps tick, cava frames, spectrum curve,
smoothing) and the artwork colour average in `color_extractor`. It is an
**optimization, not a requirement**: every call site keeps its Python
fallback, so a missing or stale module degrades instead of breaking.

## Build and install

```bash
cd ~/Projects/lollypop-am/cpp_visualizer
./build.sh
```

`build.sh` configures CMake, builds, installs the extension into the **user
site-packages** (`site.getusersitepackages()`), then runs a smoke test. It
must be `~/.local/lib/python3.14/site-packages`, not `~/.local/lib` — the
latter is not on `sys.path`, which is why the first build was never
importable.

Requirements: `cmake g++ python3-dev pybind11-dev` (tests need `libgtest-dev`,
but the test subdirectory is currently disabled in `CMakeLists.txt`).

## Python API

| Function | Signature | Used by |
|---|---|---|
| `visualizer_tick` | `(state, config, now_monotonic)` | `SimpleVisualizer._tick_cpp` |
| `visualizer_set_target` | `(state, values, count=-1, now=-1.0)` | `_on_cava_frame` **and** `_apply_spectrum` |
| `tilt_gain` | `(values, gain)` → `(array64, gain)` | `SimpleVisualizer._apply_spectrum` |
| `normalize_spectrum` | `(magnitude_db, gain, seen)` → `(array64, gain, seen)` | raw-dB callers (mel path) |
| `moving_average` | `(values, window=2)` → `array64` | `SimpleVisualizer._smoothed` |
| `extract_average` | `(buffer[...,channels])` → `(r,g,b)` 0..255 | `color_extractor._average_color` |
| `extract_average_raw` | `(bytes, w, h, channels)` → `(r,g,b)` 0..255 | `color_extractor._average_color` |
| `extract_accent` | `(buffer)` → `(r,g,b)` 0..1 | saturation-weighted accent |
| `parse_cava_frame` | `(raw_cava_line)` → `(array64, count)` | bypassing `CavaFeed` |
| `mel_remap` | `(513_bins)` → `array64` | linear FFT input |
| `dial_arc_points` | `(cx,cy,radius,start,sweep,amp,value,wave_length,phase)` → flat `[x,y,…]` | `pip.PipVolumeDial._on_draw` |
| `wave_line_points` | `(start,filled_end,centre,amp,wave_length,phase,step=2)` → flat `[x,y,…]` | `pip.WaveSlider._on_draw` |

All array inputs accept plain lists (no numpy dependency). Sizes are validated
and resampled where sensible; `mel_remap` is the only strict one (513 bins).

## The three invariants that used to be wrong

1. **Clock.** `visualizer_tick` compares `now` against `state.last_real_time`.
   Both must use `time.monotonic()`. Mixing in `time.time()` makes the
   freshness test fail forever, and the graph silently animates the synthetic
   wave even while audio plays.
2. **`live`.** `visualizer_set_cava_target` is a no-op while
   `state.live` is false. `set_playing()` mirrors `self.live` into the state
   before any frame can arrive.
3. **Data shape.** The GStreamer `spectrum` element is configured with
   `bands = 64`, so `normalize_spectrum` must not assume a 513-bin FFT.
   `normalize_spectrum` mel-remaps only a 513-bin input and resamples anything
   else. The live spectrum path calls `tilt_gain` on values already
   normalised to 0..1 by `_on_spectrum_message` (floor + root curve), which
   avoids applying `pow(level, 0.7)` twice.
4. **Both sources must feed the target.** The tick only writes a synthetic
   target while `real_data` is false; once a real frame arrives it *reads*
   `state.target`. If cava feeds the C++ state but the spectrum path only
   feeds Python (or vice versa), the graph eases toward the last synthetic
   frame and freezes at a constant height. `_on_cava_frame` and
   `_apply_spectrum` both call `visualizer_set_target` for this reason.

## Measurement (this machine, 64 points)

| Operation | Python | C++ | Speedup |
|---|---|---|---|
| `_tick` (smoothing loop only) | 3.90 µs | 0.25 µs | ~15x |
| `_smoothed` (5-point) | 6.1 µs | 0.9 µs | ~7x |
| cava frame → target | 8 µs | 0.6 µs | ~13x |
| spectrum tilt/gate/gain | 12 µs | 0.8 µs | ~15x |
| accent average (64x64) | 2.4 ms | 0.03 ms | ~80x |

The old document claimed 50-100x for the tick; the smoothing math is ~15x,
and Cairo drawing dominates a real frame, so expect a few percent of wall
time, not a rewrite. The accent path is where the win is real, because it is
per-artwork rather than per-frame.

## Verification

Parity against the Python implementations is 1e-4 or better over 25 frames:

```
python3 /tmp/opencode/verify_cpp_viz.py
```

Offscreen render (never opens a window):

```
python3 /tmp/opencode/pip_render_test.py /tmp/opencode/pip-render.png <art.jpg>
```

## Disabling it

Remove the module, or set `widget_injector._CPP_VIZ = None` before the widget
is built. Nothing else changes.