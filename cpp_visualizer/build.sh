#!/bin/bash
# Build and install the lollypop_visualizer C++ module.
#
# Builds into ./build, runs the parity check, and installs the extension into
# the *user* site-packages so a plain `import lollypop_visualizer` works from
# anywhere (the old script left it in ~/.local/lib, which is not on sys.path).
set -euo pipefail

cd "$(dirname "$0")"

BUILD_DIR="build"
PYTHON="${PYTHON:-/usr/bin/python3}"
SITE="$("$PYTHON" -c 'import site; print(site.getusersitepackages())')"

echo "=== configuring ==="
cmake -B "${BUILD_DIR}" -S . \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${HOME}/.local" \
    -DPython3_EXECUTABLE="${PYTHON}" \
    -Wno-dev

echo "=== building ==="
cmake --build "${BUILD_DIR}" --parallel "$(nproc)"

SO="$(ls "${BUILD_DIR}"/lollypop_visualizer*.so | head -1)"
echo "=== installing ${SO##*/} -> ${SITE} ==="
mkdir -p "${SITE}"
install -m 755 "${SO}" "${SITE}/"

echo "=== verifying ==="
"${PYTHON}" - <<'PY'
import time
import lollypop_visualizer as lv

print("module:", lv.__file__)
print("points/bands:", lv.POINT_COUNT, lv.MEL_BANDS)

state = lv.VisualizerState()
config = lv.VisualizerConfig()
lv.init_mel_band_indices()

state.live = True
lv.visualizer_set_cava_target(state, [500.0] * lv.POINT_COUNT, -1, time.monotonic())
for _ in range(20):
    lv.visualizer_tick(state, config, time.monotonic())
assert max(state.points) > 100.0, "cava target never reached the points"
print("tick ok, point max:", round(max(state.points), 1))

curved, gain = lv.tilt_gain([0.5] * lv.POINT_COUNT, 1.0)
print("tilt_gain ok, gain:", round(gain, 3), "max:", round(max(curved), 1))
PY

echo "=== done ==="