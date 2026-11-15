#pragma once
/** Common infrastructure for lollypop C++ extension.
 *
 *  Provides:
 *  - RAII GIL management (GilGuard, GilRelease)
 *  - Safe GdkPixbuf handling with custom deleter
 *  - Result<T> type for error handling without exceptions across FFI
 *  - Pybind11 type casters for GLib/GObject types
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/functional.h>
#include <pybind11/numpy.h>

#include <gdk-pixbuf/gdk-pixbuf.h>
#include <glib.h>
#include <gst/gst.h>
#include <gst/pbutils/pbutils.h>

#include <string>
#include <optional>
#include <variant>
#include <memory>
#include <stdexcept>
#include <array>
#include <vector>

namespace lollypop_ext {

namespace py = pybind11;

// ============================================================================
// GIL Management
// ============================================================================

/** Acquire GIL for the scope. Use when calling Python API from C++. */
struct GilGuard {
    PyGILState_STATE state_;
    GilGuard() noexcept : state_(PyGILState_Ensure()) {}
    ~GilGuard() noexcept { PyGILState_Release(state_); }
    GilGuard(const GilGuard&) = delete;
    GilGuard& operator=(const GilGuard&) = delete;
};

/** Release GIL for the scope. Use for long-running C++ computations. */
struct GilRelease {
    PyThreadState* thread_state_;
    GilRelease() noexcept : thread_state_(PyEval_SaveThread()) {}
    ~GilRelease() noexcept { PyEval_RestoreThread(thread_state_); }
    GilRelease(const GilRelease&) = delete;
    GilRelease& operator=(const GilRelease&) = delete;
};

// ============================================================================
// Error Handling
// ============================================================================

/** Error type for Result. */
struct Error {
    std::string message;
};

/** Result type: either a value or an Error.
 *  Avoids C++ exceptions crossing the pybind11 boundary. */
template<typename T>
using Result = std::variant<T, Error>;

/** Helper to create error result. */
template<typename T>
inline Result<T> make_error(const std::string& msg) {
    return Result<T>(Error{msg});
}

/** Helper to create success result. */
template<typename T>
inline Result<T> make_ok(T&& value) {
    return Result<T>(std::forward<T>(value));
}

/** Extract value or throw py::value_error with contained message. */
template<typename T>
T unwrap_or_throw(Result<T>&& result, const char* func_name = "") {
    if (std::holds_alternative<Error>(result)) {
        std::string msg = std::get<Error>(result).message;
        if (func_name && *func_name) msg = std::string(func_name) + ": " + msg;
        throw py::value_error(msg);
    }
    return std::get<T>(std::move(result));
}

// ============================================================================
// GdkPixbuf Smart Pointer
// ============================================================================

struct PixbufDeleter {
    void operator()(GdkPixbuf* p) const noexcept {
        if (p) g_object_unref(p);
    }
};
using PixbufPtr = std::unique_ptr<GdkPixbuf, PixbufDeleter>;

/** Create PixbufPtr from floating reference (e.g., gdk_pixbuf_new_from_file). */
inline PixbufPtr make_pixbuf_ptr(GdkPixbuf* p) noexcept {
    return PixbufPtr(p);  // takes ownership of floating ref
}

/** Create PixbufPtr from full reference (already ref'd). */
inline PixbufPtr adopt_pixbuf_ptr(GdkPixbuf* p) noexcept {
    return PixbufPtr(p);  // assumes caller transferred ownership
}

// ============================================================================
// GObject Smart Pointer
// ============================================================================

template<typename T>
struct GObjectDeleter {
    void operator()(T* p) const noexcept {
        if (p) g_object_unref(p);
    }
};

// ============================================================================
// Utility Functions
// ============================================================================

/** Convert GError to string and free it. */
inline std::string gerror_to_string(GError* error) {
    std::string msg = error ? error->message : "Unknown GError";
    if (error) g_error_free(error);
    return msg;
}

/** Safe gdk_pixbuf_new_from_file with error handling. */
inline Result<PixbufPtr> load_pixbuf_file(const std::string& path) {
    GError* error = nullptr;
    GdkPixbuf* pixbuf = gdk_pixbuf_new_from_file(path.c_str(), &error);
    if (!pixbuf) {
        return make_error<PixbufPtr>(gerror_to_string(error));
    }
    return make_ok(make_pixbuf_ptr(pixbuf));
}

/** Scale pixbuf maintaining aspect ratio. */
inline Result<PixbufPtr> scale_pixbuf(GdkPixbuf* src, int width, int height, 
                                      int interp = GDK_INTERP_BILINEAR) {
    GdkPixbuf* dest = gdk_pixbuf_scale_simple(src, width, height, static_cast<GdkInterpType>(interp));
    if (!dest) {
        return make_error<PixbufPtr>("gdk_pixbuf_scale_simple failed");
    }
    return make_ok(make_pixbuf_ptr(dest));
}

/** Get pixel data access info. */
inline void get_pixbuf_info(GdkPixbuf* pixbuf, int& width, int& height, 
                            int& channels, int& stride, const guchar*& pixels) {
    width = gdk_pixbuf_get_width(pixbuf);
    height = gdk_pixbuf_get_height(pixbuf);
    channels = gdk_pixbuf_get_n_channels(pixbuf);
    stride = gdk_pixbuf_get_rowstride(pixbuf);
    pixels = gdk_pixbuf_get_pixels(pixbuf);
}

/** Check if pixbuf has alpha channel. */
inline bool pixbuf_has_alpha(GdkPixbuf* pixbuf) {
    return gdk_pixbuf_get_has_alpha(pixbuf);
}

}  // namespace lollypop_ext