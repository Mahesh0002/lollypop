#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "../include/visualizer_types.h"
#include "../include/visualizer_tick.h"
#include "../include/mel_remap.h"
#include "../include/cava_parse.h"
#include "../include/spectrum_normalize.h"
#include "../include/moving_average.h"
#include "../include/particle_system.h"
#include "../include/accent_extract.h"
#include "../include/pip_geometry.h"

namespace py = pybind11;
using namespace lollypop;

namespace {

template <std::size_t N>
py::array_t<float> array_to_numpy(const std::array<float, N>& arr) {
    py::array_t<float> out(N);
    std::copy_n(arr.begin(), N, static_cast<float*>(out.request().ptr));
    return out;
}

py::array_t<float> vector_to_numpy(const std::vector<float>& values) {
    py::array_t<float> out(static_cast<py::ssize_t>(values.size()));
    std::copy(values.begin(), values.end(),
              static_cast<float*>(out.request().ptr));
    return out;
}

// Accept any float sequence (list, tuple, numpy array) - no numpy needed.
std::vector<float> to_vector(const py::object& value) {
    return value.cast<std::vector<float>>();
}

// Shared buffer validation for the pixel-based helpers.
struct PixelBuffer {
    const uint8_t* data;
    int width;
    int height;
    int stride;
    int channels;
};

PixelBuffer require_pixels(const py::buffer& buffer) {
    py::buffer_info info = buffer.request();
    if (info.ndim != 3) {
        throw py::value_error("Expected a 3D (height, width, channels) buffer");
    }
    const int height = static_cast<int>(info.shape[0]);
    const int width = static_cast<int>(info.shape[1]);
    const int channels = static_cast<int>(info.shape[2]);
    if (width <= 0 || height <= 0 || channels < 3) {
        throw py::value_error("Expected at least 3 channels and a non-empty image");
    }
    if (info.itemsize != 1) {
        throw py::value_error("Expected a byte (uint8) pixel buffer");
    }
    // Require a tight C-contiguous (row-major) buffer: row stride must equal
    // width*channels and the innermost stride must equal channels.
    if (!(info.strides[1] == static_cast<py::ssize_t>(channels) &&
          info.strides[0] == static_cast<py::ssize_t>(width) * channels)) {
        throw py::value_error("Expected a C-contiguous (row-major) pixel buffer");
    }
    return PixelBuffer{
        static_cast<const uint8_t*>(info.ptr),
        width,
        height,
        static_cast<int>(info.strides[0]),
        channels,
    };
}

}  // namespace

// ------------------------------------------------------------------ wrappers

std::array<float, MEL_BANDS> mel_remap_wrapper(const py::object& spectrum) {
    const std::vector<float> values = to_vector(spectrum);
    if (values.size() != 513) {
        throw py::value_error("mel_remap expects a 513-bin linear FFT spectrum");
    }
    std::array<float, MEL_BANDS> out{};
    mel_remap(values.data(), static_cast<int>(values.size()), out);
    return out;
}

py::tuple normalize_spectrum_wrapper(const py::object& magnitude, float gain,
                                     bool seen) {
    const std::vector<float> values = to_vector(magnitude);
    std::array<float, MEL_BANDS> out{};
    normalize_spectrum(values.data(), static_cast<int>(values.size()), out, gain, seen);
    return py::make_tuple(array_to_numpy(out), gain, seen);
}

py::tuple tilt_gain_wrapper(const py::object& input, float gain) {
    const std::vector<float> values = to_vector(input);
    std::array<float, POINT_COUNT> out{};
    tilt_gain(values.data(), static_cast<int>(values.size()), out, gain);
    return py::make_tuple(array_to_numpy(out), gain);
}

std::array<float, POINT_COUNT> moving_average_wrapper(const py::object& input,
                                                      int window) {
    std::vector<float> values = to_vector(input);
    if (values.empty()) {
        return {};
    }
    if (values.size() != POINT_COUNT) {
        // Resample to the widget's point count before smoothing.
        std::array<float, POINT_COUNT> resampled{};
        resample_to_points(values.data(), static_cast<int>(values.size()), resampled);
        values.assign(resampled.begin(), resampled.end());
    }
    std::array<float, POINT_COUNT> arr{};
    std::copy_n(values.begin(), POINT_COUNT, arr.begin());
    std::array<float, POINT_COUNT> out{};
    moving_average(arr, out, window);
    return out;
}

py::tuple parse_cava_frame_wrapper(const std::string& data) {
    std::array<float, POINT_COUNT> out{};
    const int count = parse_cava_frame(data.c_str(), data.size(), out);
    return py::make_tuple(array_to_numpy(out), count);
}

// pybind11 binds a raw `const float*` as a single float, which made the old
// export uncallable.  Take a sequence, and let the caller hand in the
// monotonic timestamp so the freshness check cannot drift from `_tick`.
//
// This is the *only* way audio reaches the C++ state machine, and both
// sources (cava and the GStreamer spectrum) use it.  Feeding only one of them
// leaves the native target stale, which freezes the graph as soon as the
// tick switches to real data.
void set_target_wrapper(VisualizerState& state, const py::object& values,
                        int count, double now) {
    const std::vector<float> frame = to_vector(values);
    if (count < 0) {
        count = static_cast<int>(frame.size());
    }
    count = std::min<int>(count, static_cast<int>(frame.size()));
    if (!state.live) {
        // Python always sets `live` before feeding frames; mirror the guard so
        // a paused widget cannot latch stale data.
        return;
    }
    visualizer_set_cava_target(state, frame.data(), count);
    if (now > 0.0) {
        state.last_real_time = now;
        state.real_data = true;
    }
}

py::tuple extract_accent_wrapper(const py::buffer& pixels) {
    const PixelBuffer buf = require_pixels(pixels);
    float r = 0.0f, g = 0.0f, b = 0.0f;
    extract_accent(buf.data, buf.width, buf.height, buf.stride, buf.channels,
                   r, g, b);
    return py::make_tuple(r, g, b);
}

py::tuple extract_average_wrapper(const py::buffer& pixels) {
    const PixelBuffer buf = require_pixels(pixels);
    float r = 0.0f, g = 0.0f, b = 0.0f;
    extract_average(buf.data, buf.width, buf.height, buf.stride, buf.channels,
                    r, g, b);
    return py::make_tuple(r, g, b);
}

// Bytes-based variant for callers that already hold raw pixel bytes
// (GdkPixbuf.get_pixels()) and do not have a shaped buffer to hand over.
py::tuple extract_average_raw_wrapper(const py::buffer& pixels, int width,
                                     int height, int channels) {
    if (width <= 0 || height <= 0 || channels < 3) {
        throw py::value_error("Expected positive dimensions and >= 3 channels");
    }
    py::buffer_info info = pixels.request();
    if (info.itemsize != 1) {
        throw py::value_error("Expected a byte (uint8) pixel buffer");
    }
    const size_t needed = size_t(width) * size_t(height) * size_t(channels);
    if (size_t(info.size) * size_t(info.itemsize) < needed) {
        throw py::value_error("Pixel buffer is smaller than width*height*channels");
    }
    // Use the buffer's actual row stride (GdkPixbuf rows may be padded).
    int stride = static_cast<int>(info.strides[0]);
    if (stride < width * channels) {
        stride = width * channels;
    }
    float r = 0.0f, g = 0.0f, b = 0.0f;
    extract_average(static_cast<const uint8_t*>(info.ptr), width, height,
                    stride, channels, r, g, b);
    return py::make_tuple(r, g, b);
}

void update_particles_wrapper(VisualizerState& state,
                              const VisualizerConfig& config) {
    update_particles(state, config, state.points);
}

py::array_t<float> dial_arc_points_wrapper(float cx, float cy, float radius,
                                           float start, float sweep,
                                           float wave_amp_base, float value,
                                           float wave_length, float phase) {
    return vector_to_numpy(dial_arc_points(cx, cy, radius, start, sweep,
                                           wave_amp_base, value, wave_length,
                                           phase));
}

py::array_t<float> wave_line_points_wrapper(float start, float filled_end,
                                            float centre, float wave_amplitude,
                                            float wave_length, float phase,
                                            float step) {
    return vector_to_numpy(wave_line_points(start, filled_end, centre,
                                            wave_amplitude, wave_length, phase,
                                            step));
}

PYBIND11_MODULE(lollypop_visualizer, m) {
    m.doc() = "Lollypop visualizer C++ acceleration module";

    py::class_<VisualizerConfig>(m, "VisualizerConfig")
        .def(py::init<>())
        .def_readwrite("accent_r", &VisualizerConfig::accent_r)
        .def_readwrite("accent_g", &VisualizerConfig::accent_g)
        .def_readwrite("accent_b", &VisualizerConfig::accent_b)
        .def_readwrite("mode", &VisualizerConfig::mode)
        .def_readwrite("fill_alpha", &VisualizerConfig::fill_alpha)
        .def_readwrite("line_alpha", &VisualizerConfig::line_alpha);

    py::class_<VisualizerState>(m, "VisualizerState")
        .def(py::init<>())
        .def_readwrite("points", &VisualizerState::points)
        .def_readwrite("target", &VisualizerState::target)
        .def_readwrite("phase", &VisualizerState::phase)
        .def_readwrite("live", &VisualizerState::live)
        .def_readwrite("real_data", &VisualizerState::real_data)
        .def_readwrite("last_real_time", &VisualizerState::last_real_time)
        .def_readwrite("gain", &VisualizerState::gain)
        .def_readwrite("spectrum_frames", &VisualizerState::spectrum_frames)
        .def_readwrite("cava_frames", &VisualizerState::cava_frames)
        .def_readwrite("cava_zero_frames", &VisualizerState::cava_zero_frames)
        .def_readwrite("seen_cava", &VisualizerState::seen_cava)
        .def_readwrite("seen_spectrum", &VisualizerState::seen_spectrum)
        .def_readwrite("particle_count", &VisualizerState::particle_count);

    m.attr("POINT_COUNT") = POINT_COUNT;
    m.attr("MEL_BANDS") = MEL_BANDS;
    m.attr("MAX_VALUE") = MAX_VALUE;
    m.attr("FRAME_MS") = FRAME_MS;
    m.attr("MODE_AREA") = 0;
    m.attr("MODE_PARTICLES") = 1;
    m.attr("MODE_BARS") = 2;

    m.def("visualizer_tick", &visualizer_tick,
          py::arg("state"), py::arg("config"), py::arg("now"),
          "Advance visualizer by one frame (call at 60fps)");

    m.def("visualizer_set_target", &set_target_wrapper,
          py::arg("state"), py::arg("values"), py::arg("count") = -1,
          py::arg("now") = -1.0,
          "Set the target curve (0..1000 floats) and mark the frame fresh");

    // Back-compat alias from the first revision.
    m.def("visualizer_set_cava_target", &set_target_wrapper,
          py::arg("state"), py::arg("values"), py::arg("count") = -1,
          py::arg("now") = -1.0,
          "Alias of visualizer_set_target");

    m.def("mel_remap", &mel_remap_wrapper,
          "Remap a 513-bin linear FFT spectrum to 64 mel bands");

    m.def("normalize_spectrum", &normalize_spectrum_wrapper,
          py::arg("magnitude"), py::arg("gain"), py::arg("seen_spectrum"),
          "Normalize raw GStreamer dB magnitudes (mel-aware, any band count)");

    m.def("tilt_gain", &tilt_gain_wrapper,
          py::arg("values"), py::arg("gain"),
          "Tilt/gate/adaptive gain for values already normalised to 0..1");

    m.def("moving_average", &moving_average_wrapper,
          py::arg("input"), py::arg("window") = SMOOTHING_WINDOW);

    m.def("parse_cava_frame", &parse_cava_frame_wrapper,
          "Parse a raw cava frame string into a float array");

    m.def("extract_accent", &extract_accent_wrapper,
          py::arg("pixels"),
          "Saturation-weighted accent (0..1) from an RGBA buffer");

    m.def("extract_average", &extract_average_wrapper,
          py::arg("pixels"),
          "Average colour (0..255) from a (height, width, channels) buffer");

    m.def("extract_average_raw", &extract_average_raw_wrapper,
          py::arg("pixels"), py::arg("width"), py::arg("height"),
          py::arg("channels"),
          "Average colour (0..255) from raw pixel bytes");

    m.def("dial_arc_points", &dial_arc_points_wrapper,
          py::arg("cx"), py::arg("cy"), py::arg("radius"), py::arg("start"),
          py::arg("sweep"), py::arg("wave_amp_base"), py::arg("value"),
          py::arg("wave_length"), py::arg("phase"),
          "Volume-dial wavy arc as a flat [x0,y0,x1,y1,...] array");

    m.def("wave_line_points", &wave_line_points_wrapper,
          py::arg("start"), py::arg("filled_end"), py::arg("centre"),
          py::arg("wave_amplitude"), py::arg("wave_length"), py::arg("phase"),
          py::arg("step") = 2.0f,
          "Seek-bar wavy fill as a flat [x0,y0,x1,y1,...] array");

    m.def("update_particles", &update_particles_wrapper,
          py::arg("state"), py::arg("config"),
          "Update the particle system (visualizer_tick does this in mode 1)");

    m.def("init_mel_band_indices", &init_mel_band_indices,
          "Pre-compute the mel filterbank non-zero ranges");
}