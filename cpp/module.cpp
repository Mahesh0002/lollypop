/** Pybind11 module entry point for lollypop_ext.
 *  
 *  Exposes:
 *  - color_extractor: extract_palette(path) -> (bg, accent1, accent2)
 *  - blur: gaussian_blur(pixbuf, radius) -> None (in-place)
 *          gaussian_blur_copy(pixbuf, radius) -> new_pixbuf
 *  - tagreader: Discoverer class with discover_uri()
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/functional.h>

#include "common.hpp"
#include "color_extractor.hpp"
#include "blur.hpp"

namespace py = pybind11;
using namespace lollypop_ext;

// ============================================================================
// Pybind11 type caster for GdkPixbuf*
// ============================================================================

struct PixbufCaster {
    using type = GdkPixbuf*;
    
    static bool check(PyObject* obj) {
        return PyCapsule_CheckExact(obj) && 
               std::string(PyCapsule_GetName(obj)) == "lollypop.GdkPixbuf";
    }
    
    static GdkPixbuf* cast(PyObject* obj) {
        return static_cast<GdkPixbuf*>(PyCapsule_GetPointer(obj, "lollypop.GdkPixbuf"));
    }
    
    static PyObject* wrap(GdkPixbuf* pixbuf) {
        g_object_ref(pixbuf);
        return PyCapsule_New(pixbuf, "lollypop.GdkPixbuf", [](PyObject* cap) {
            GdkPixbuf* p = static_cast<GdkPixbuf*>(PyCapsule_GetPointer(cap, "lollypop.GdkPixbuf"));
            if (p) g_object_unref(p);
        });
    }
};

namespace pybind11 {
namespace detail {

template<>
struct type_caster<GdkPixbuf*> : type_caster_base<GdkPixbuf*> {
public:
    PYBIND11_TYPE_CASTER(GdkPixbuf*, _("GdkPixbuf*"));
    
    bool load(handle src, bool) {
        if (!PixbufCaster::check(src.ptr())) return false;
        value = PixbufCaster::cast(src.ptr());
        return true;
    }
    
    static handle cast(GdkPixbuf* src, return_value_policy, handle) {
        return PixbufCaster::wrap(src);
    }
};

}  // namespace detail
}  // namespace pybind11

using namespace lollypop_ext;

// ============================================================================
// Color Extractor Bindings
// ============================================================================

static py::tuple palette_to_tuple(const Palette& p) {
    return py::make_tuple(
        py::make_tuple(p.background[0], p.background[1], p.background[2]),
        py::make_tuple(p.accent1[0], p.accent1[1], p.accent1[2]),
        py::make_tuple(p.accent2[0], p.accent2[1], p.accent2[2])
    );
}

static void bind_color_extractor(py::module& m) {
    m.def("extract_palette", [](const std::string& path) -> py::tuple {
        auto result = extract_palette(path);
        if (std::holds_alternative<Error>(result)) {
            throw py::value_error(std::get<Error>(result).message);
        }
        return palette_to_tuple(std::get<Palette>(std::move(result)));
    }, py::arg("path"), 
    "Extract color palette from image file.\n"
    "Returns tuple of 3 RGB tuples: (background, accent1, accent2).");
    
    m.def("derive_scheme", [](const py::tuple& seed_tuple) -> py::tuple {
        if (py::len(seed_tuple) != 3) {
            throw py::value_error("seed must be 3-tuple of floats 0-1");
        }
        std::array<double, 3> seed = {
            seed_tuple[0].cast<double>(),
            seed_tuple[1].cast<double>(),
            seed_tuple[2].cast<double>()
        };
        return palette_to_tuple(derive_scheme(seed));
    }, py::arg("seed"),
    "Derive palette from seed color (RGB floats 0-1).");
}

// ============================================================================
// Blur Bindings
// ============================================================================

static void bind_blur(py::module& m) {
    py::enum_<BlurMode>(m, "BlurMode")
        .value("GAUSSIAN", BlurMode::GAUSSIAN)
        .value("BOX_APPROX", BlurMode::BOX_APPROX)
        .export_values();
    
    m.def("gaussian_blur", [](py::object pixbuf_obj, int radius, BlurMode mode) {
        // Extract GdkPixbuf* from capsule
        if (!PyCapsule_CheckExact(pixbuf_obj.ptr())) {
            throw py::type_error("Expected GdkPixbuf capsule");
        }
        GdkPixbuf* pixbuf = static_cast<GdkPixbuf*>(
            PyCapsule_GetPointer(pixbuf_obj.ptr(), "lollypop.GdkPixbuf")
        );
        if (!pixbuf) throw py::value_error("Null pixbuf in capsule");
        
        auto result = gaussian_blur_inplace(pixbuf, radius, mode);
        if (std::holds_alternative<Error>(result)) {
            throw py::value_error(std::get<Error>(result).message);
        }
    }, py::arg("pixbuf"), py::arg("radius"), py::arg("mode") = BlurMode::GAUSSIAN,
    "In-place Gaussian blur on GdkPixbuf (modifies original).");
    
    m.def("gaussian_blur_copy", [](py::object pixbuf_obj, int radius, BlurMode mode) 
        -> py::object {
        if (!PyCapsule_CheckExact(pixbuf_obj.ptr())) {
            throw py::type_error("Expected GdkPixbuf capsule");
        }
        GdkPixbuf* src = static_cast<GdkPixbuf*>(
            PyCapsule_GetPointer(pixbuf_obj.ptr(), "lollypop.GdkPixbuf")
        );
        if (!src) throw py::value_error("Null pixbuf in capsule");
        
        auto result = gaussian_blur_copy(src, radius, mode);
        if (std::holds_alternative<Error>(result)) {
            throw py::value_error(std::get<Error>(result).message);
        }
        
        PixbufPtr new_pixbuf = std::get<PixbufPtr>(std::move(result));
        // Return as capsule (ownership transferred to Python)
        return py::capsule(new_pixbuf.release(), "lollypop.GdkPixbuf", [](PyObject* cap) {
            GdkPixbuf* p = static_cast<GdkPixbuf*>(PyCapsule_GetPointer(cap, "lollypop.GdkPixbuf"));
            if (p) g_object_unref(p);
        });
    }, py::arg("pixbuf"), py::arg("radius"), py::arg("mode") = BlurMode::GAUSSIAN,
    "Copy pixbuf and apply Gaussian blur (original unchanged).");

    m.def("blur_bytes", [](py::buffer pixels, int width, int height, int channels,
                           int stride, int radius, BlurMode mode) 
        -> py::bytes {
        py::buffer_info info = pixels.request();
        if (info.itemsize != 1) {
            throw py::value_error("Expected a byte (uint8) pixel buffer");
        }
        if (width <= 0 || height <= 0 || channels < 3) {
            throw py::value_error("Expected positive dimensions and >= 3 channels");
        }
        const int use_stride = (stride > 0 && stride >= width * channels)
            ? stride : static_cast<int>(info.strides[0]);
        if (use_stride < width * channels) {
            throw py::value_error("Stride is smaller than width*channels");
        }
        const size_t needed = static_cast<size_t>(use_stride) * height;
        if (static_cast<size_t>(info.size) < needed) {
            throw py::value_error("Pixel buffer is smaller than stride*height");
        }

        auto out = lollypop_ext::blur_bytes(
            static_cast<const guchar*>(info.ptr), width, height, channels,
            use_stride, radius, mode);
        if (out.empty()) {
            throw py::value_error("Blur failed");
        }
        return py::bytes(reinterpret_cast<const char*>(out.data()), out.size());
    }, py::arg("pixels"), py::arg("width"), py::arg("height"), py::arg("channels"),
       py::arg("stride"), py::arg("radius"), py::arg("mode") = BlurMode::GAUSSIAN,
    "Blur raw pixel bytes (stride*height). Accepts any contiguous byte buffer. "
    "Returns a new bytes object.");
}

// ============================================================================
// Tag Reader Bindings (stub for Phase 3)
// ============================================================================

struct TrackTags {
    std::string title;
    std::vector<std::string> artists;
    std::vector<std::string> album_artists;
    std::string album;
    std::vector<std::string> genres;
    std::string mb_track_id;
    std::string mb_album_id;
    std::string mb_artist_id;
    std::string mb_album_artist_id;
    int duration_ms = 0;
    int track_number = 0;
    int disc_number = 0;
    std::string disc_name;
    int year = 0;
    int bpm = 0;
    bool compilation = false;
    std::string lyrics;
};

class Discoverer {
public:
    explicit Discoverer(int timeout_ms = 10000) : timeout_ms_(timeout_ms) {
        // GStreamer discoverer created lazily on first use
    }
    
    ~Discoverer() {
        if (discoverer_) g_object_unref(discoverer_);
    }
    
    TrackTags discover_uri(const std::string& uri) {
        // TODO: Implement in Phase 3
        throw std::runtime_error("TagReader not yet implemented");
    }
    
private:
    int timeout_ms_;
    void* discoverer_ = nullptr;  // Opaque pointer to GstDiscoverer
};

static void bind_tagreader(py::module& m) {
    py::class_<TrackTags>(m, "TrackTags")
        .def_readwrite("title", &TrackTags::title)
        .def_readwrite("artists", &TrackTags::artists)
        .def_readwrite("album_artists", &TrackTags::album_artists)
        .def_readwrite("album", &TrackTags::album)
        .def_readwrite("genres", &TrackTags::genres)
        .def_readwrite("mb_track_id", &TrackTags::mb_track_id)
        .def_readwrite("mb_album_id", &TrackTags::mb_album_id)
        .def_readwrite("mb_artist_id", &TrackTags::mb_artist_id)
        .def_readwrite("mb_album_artist_id", &TrackTags::mb_album_artist_id)
        .def_readwrite("duration_ms", &TrackTags::duration_ms)
        .def_readwrite("track_number", &TrackTags::track_number)
        .def_readwrite("disc_number", &TrackTags::disc_number)
        .def_readwrite("disc_name", &TrackTags::disc_name)
        .def_readwrite("year", &TrackTags::year)
        .def_readwrite("bpm", &TrackTags::bpm)
        .def_readwrite("compilation", &TrackTags::compilation)
        .def_readwrite("lyrics", &TrackTags::lyrics);
    
    py::class_<Discoverer>(m, "Discoverer")
        .def(py::init<int>(), py::arg("timeout_ms") = 10000)
        .def("discover_uri", &Discoverer::discover_uri, py::arg("uri"));
    
    m.def("read_tags_sync", [](const std::string& uri, int timeout_ms) -> TrackTags {
        Discoverer d(timeout_ms);
        return d.discover_uri(uri);
    }, py::arg("uri"), py::arg("timeout_ms") = 10000);
}

// ============================================================================
// Module Definition
// ============================================================================

PYBIND11_MODULE(lollypop_ext, m) {
    m.doc() = R"pbdoc(
        Lollypop C++ Acceleration Module
        ================================
        
        Provides high-performance implementations of:
        - Color palette extraction from album artwork
        - Gaussian blur for artwork effects
        - Audio tag reading via GStreamer (stub)
        
        All functions release the GIL during computation.
    )pbdoc";
    
    m.attr("__version__") = "0.1.0";
    
    // Submodules
    auto color_mod = m.def_submodule("color", "Color extraction functions");
    bind_color_extractor(color_mod);
    
    auto blur_mod = m.def_submodule("blur", "Gaussian blur functions");
    bind_blur(blur_mod);
    
    auto tag_mod = m.def_submodule("tagreader", "Audio tag reading (stub)");
    bind_tagreader(tag_mod);
    
    // Top-level convenience functions
    m.def("extract_palette", [color_mod](const std::string& path) {
        return color_mod.attr("extract_palette")(path);
    }, "Extract palette from image (convenience).");
    
    m.def("gaussian_blur", [blur_mod](py::object pixbuf, int radius) {
        return blur_mod.attr("gaussian_blur")(pixbuf, radius);
    }, "In-place blur (convenience).");
}