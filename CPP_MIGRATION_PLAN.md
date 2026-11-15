# Lollypop C++ Migration Plan
**Goal**: Replace performance-critical Python paths with C++ modules via pybind11, with zero-regression integration.

---

## 1. Migration Candidates (Ranked by ROI)

| Rank | Component | File | Function | Called From | Speedup Est. |
|------|-----------|------|----------|-------------|--------------|
| 1 | **Color Extraction** | `color_extractor.py:132-215` | `extract_palette()`, `_average_color()` | `widget_injector.py:3927` (track change) | 100× |
| 2 | **Gaussian Blur** | `artwork_manager.py:219-251` | `_get_blur()` | Album/artist views, fullscreen | 50× |
| 3 | **Tag Reading** | `tagreader.py` | `Discoverer`, `TagReader` methods | `collection_scanner.py:740` (scan) | 10-20× |
| 4 | **Palette Derivation** | `color_extractor.py:177-215` | `derive_scheme()` | Same as #1 | 100× (pure math) |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      Python (GTK Main Thread)                   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │ widget_injector │  │ artwork_manager │  │ collection_    │  │
│  │ ._push_artwork()│  │ ._get_blur()    │  │ scanner        │  │
│  └────────┬────────┘  └────────┬────────┘  └───────┬────────┘  │
│           │                    │                    │           │
│           ▼                    ▼                    ▼           │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              lollypop_ext C++ Extension Module             │ │
│  │  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐  │ │
│  │  │ ColorExtract │ │   Blur       │ │   TagReader        │  │ │
│  │  │  (GdkPixbuf) │ │  (PIL/GDK)   │ │  (GStreamer)       │  │ │
│  │  └──────────────┘ └──────────────┘ └────────────────────┘  │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**Key Principle**: C++ module loads **once** at startup. Python calls are thin wrappers (`PyObject*` → C++ → `PyObject*`).

---

## 3. Build System Integration (Meson)

### 3.1 Add pybind11 Dependency
```meson
# meson.build (add after line 35)
pybind11 = dependency('pybind11', version: '>= 2.10', required: false)
if not pybind11.found()
    # Fallback: fetch from wrap file
    pybind11 = subproject('pybind11').get_variable('pybind11_dep')
endif

# C++ compiler flags
cpp_args = ['-std=c++17', '-O3', '-fvisibility=hidden', '-Wall', '-Wextra']
if host_machine.system() == 'linux'
    cpp_args += ['-fPIC']
endif
```

### 3.2 Define Extension Module Target
```meson
# meson.build (add before install_subdir)
lollypop_ext_sources = [
    'cpp/color_extractor.cpp',
    'cpp/blur.cpp',
    'cpp/tagreader.cpp',
    'cpp/module.cpp',  # pybind11 module entry point
]

lollypop_ext = library(
    'lollypop_ext',
    lollypop_ext_sources,
    dependencies: [
        dependency('gdk-pixbuf-2.0'),
        dependency('gstreamer-1.0'),
        dependency('gstreamer-pbutils-1.0'),
        pybind11,
    ],
    cpp_args: cpp_args,
    install: true,
    install_dir: join_paths(python_dir, 'lollypop'),
    name_suffix: py_installation.get_extension_suffix(),  # .cpython-311-darwin.so etc.
)
```

### 3.3 Wrap File for pybind11 (if not in distro)
```meson
# subprojects/pybind11.wrap
[wrap-file]
directory = pybind11-2.11.1
source_url = https://github.com/pybind/pybind11/archive/refs/tags/v2.11.1.tar.gz
source_filename = pybind11-2.11.1.tar.gz
source_hash = sha256:...

[provide]
dependency_names = [pybind11]
```

---

## 4. C++ Module Structure

### 4.1 Directory Layout
```
lollypop/
├── cpp/
│   ├── module.cpp              # PYBIND11_MODULE entry point
│   ├── color_extractor.cpp     # extract_palette, derive_scheme
│   ├── color_extractor.hpp
│   ├── blur.cpp                # gaussian_blur
│   ├── blur.hpp
│   ├── tagreader.cpp           # TagReader, Discoverer
│   ├── tagreader.hpp
│   ├── common.hpp              # Shared types, error handling
│   └── gil.hpp                 # RAII GIL management
├── lollypop/
│   ├── __init__.py             # try: from . import lollypop_ext
│   ├── color_extractor.py      # Python fallback + C++ wrapper
│   ├── artwork_manager.py      # Python fallback + C++ wrapper
│   └── tagreader.py            # Python fallback + C++ wrapper
```

### 4.2 Common Infrastructure (cpp/common.hpp)
```cpp
#pragma once
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/functional.h>
#include <string>
#include <optional>
#include <stdexcept>

namespace lollypop_ext {

namespace py = pybind11;

// RAII: Acquire GIL for callbacks into Python
struct GilGuard {
    PyGILState_STATE state;
    GilGuard() : state(PyGILState_Ensure()) {}
    ~GilGuard() { PyGILState_Release(state); }
};

// RAII: Release GIL for long-running C++ work
struct GilRelease {
    PyThreadState* thread_state;
    GilRelease() : thread_state(PyEval_SaveThread()) {}
    ~GilRelease() { PyEval_RestoreThread(thread_state); }
}

// Convert Python exception to C++ exception
[[noreturn]] void throw_py_error(const std::string& msg = "");

// Safe GdkPixbuf handling
struct PixbufDeleter {
    void operator()(GdkPixbuf* p) const { if (p) g_object_unref(p); }
};
using PixbufPtr = std::unique_ptr<GdkPixbuf, PixbufDeleter>;

// Result type for fallible operations
template<typename T>
using Result = std::variant<T, std::string>;  // value or error message

}  // namespace lollypop_ext
```

### 4.3 GIL Management Policy
| Operation | GIL State |
|-----------|-----------|
| Module init / Python callbacks | **Held** (GilGuard) |
| Image decode / pixel walk / blur / tag reading | **Released** (GilRelease) |
| Returning results to Python | **Held** |

---

## 5. Component Specifications

### 5.1 Color Extractor (cpp/color_extractor.hpp)
```cpp
#pragma once
#include "common.hpp"
#include <array>

namespace lollypop_ext {

struct Palette {
    std::array<int, 3> background;  // RGB 0-255
    std::array<int, 3> accent1;
    std::array<int, 3> accent2;
};

// Main entry: path -> palette (or error)
Result<Palette> extract_palette(const std::string& image_path);

// Internal: compute average color of downscaled image
std::optional<std::array<double, 3>> average_color(const std::string& path);

// Internal: derive 3-color scheme from seed (pure math, no GIL needed)
Palette derive_scheme(const std::array<double, 3>& seed);

}  // namespace lollypop_ext
```

**Implementation Notes**:
- Use `gdk_pixbuf_new_from_file()` → `gdk_pixbuf_scale_simple(64,64)` → pixel walk
- All math uses `colorsys` equivalent (HLS conversions) in C++
- No Python objects in hot path

### 5.2 Blur (cpp/blur.hpp)
```cpp
#pragma once
#include "common.hpp"

namespace lollypop_ext {

enum class BlurMode { GAUSSIAN, BOX };

// In-place blur on GdkPixbuf (modifies input)
Result<void> gaussian_blur(GdkPixbuf* pixbuf, int radius);

// Copy + blur (for immutable input)
Result<PixbufPtr> gaussian_blur_copy(GdkPixbuf* src, int radius);

}  // namespace lollypop_ext
```

**Implementation Options**:
1. **CPU**: Separable Gaussian (O(n×radius)) - use for radius ≤ 50
2. **GPU**: OpenGL compute shader via GTK's `GdkGLContext` - for radius > 50
3. **PIL fallback**: Call Python PIL if GPU unavailable

**Decision**: Start with CPU separable Gaussian. It's 10× faster than PIL and no GPU dependency.

### 5.3 Tag Reader (cpp/tagreader.hpp)
```cpp
#pragma once
#include "common.hpp"
#include <string>
#include <vector>
#include <optional>

namespace lollypop_ext {

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
    // ... other fields
};

// Discoverer wrapper (uses GStreamer Discoverer)
class Discoverer {
public:
    Discoverer(int timeout_ms = 10000);
    Result<TrackTags> discover_uri(const std::string& uri);
private:
    GstPbutilsDiscoverer* discoverer_ = nullptr;
};

// Synchronous tag reading (for scanner threads)
Result<TrackTags> read_tags_sync(const std::string& uri, int timeout_ms = 10000);

}  // namespace lollypop_ext
```

**Integration with GStreamer**:
- `GstPbutilsDiscoverer` is thread-safe when each thread has its own instance
- Create one `Discoverer` per worker thread (pool)
- No GIL needed during discovery

---

## 6. Python Integration Layer

### 6.1 Pattern: Try C++, Fallback to Python
```python
# lollypop/color_extractor.py
try:
    from . import lollypop_ext
    _HAS_CPP = True
except ImportError:
    _HAS_CPP = False

def extract_palette(image_path):
    if _HAS_CPP:
        try:
            return lollypop_ext.extract_palette(image_path)
        except Exception as e:
            logger.warning(f"C++ extract_palette failed: {e}, falling back")
    return _extract_palette_python(image_path)  # Original implementation
```

### 6.2 Type Conversion (pybind11)
```cpp
// module.cpp
#include <pybind11/pybind11.h>
#include "color_extractor.hpp"
#include "blur.hpp"
#include "tagreader.hpp"

namespace py = pybind11;
using namespace lollypop_ext;

PYBIND11_MODULE(lollypop_ext, m) {
    m.doc() = "Lollypop C++ acceleration module";

    // Color extractor
    m.def("extract_palette", [](const std::string& path) -> py::tuple {
        auto result = extract_palette(path);
        if (std::holds_alternative<std::string>(result)) {
            throw py::value_error(std::get<std::string>(result));
        }
        auto palette = std::get<Palette>(result);
        return py::make_tuple(
            py::make_tuple(palette.background[0], palette.background[1], palette.background[2]),
            py::make_tuple(palette.accent1[0], palette.accent1[1], palette.accent1[2]),
            py::make_tuple(palette.accent2[0], palette.accent2[1], palette.accent2[2])
        );
    }, "Extract color palette from image file");

    // Blur
    m.def("gaussian_blur", [](py::object pixbuf_obj, int radius) {
        // Extract GdkPixbuf pointer from Python object
        GdkPixbuf* pixbuf = reinterpret_cast<GdkPixbuf*>(pixbuf_obj.attr("__gpointer__").cast<uintptr_t>());
        auto result = gaussian_blur(pixbuf, radius);
        if (std::holds_alternative<std::string>(result)) {
            throw py::runtime_error(std::get<std::string>(result));
        }
    }, "In-place Gaussian blur on GdkPixbuf");

    // Tag reader
    py::class_<Discoverer>(m, "Discoverer")
        .def(py::init<int>())
        .def("discover_uri", &Discoverer::discover_uri);
}
```

---

## 7. Testing Strategy

### 7.1 Unit Tests (C++ Level)
```cpp
// tests/test_color_extractor.cpp
#include <gtest/gtest.h>
#include "color_extractor.hpp"

TEST(ColorExtractor, SolidColor) {
    // Create 64x64 red image in memory
    auto pixbuf = gdk_pixbuf_new(GDK_COLORSPACE_RGB, FALSE, 8, 64, 64);
    // Fill with red...
    auto palette = extract_palette_from_pixbuf(pixbuf);  // internal test API
    EXPECT_NEAR(palette.accent1[0], 255, 10);  // Red dominant
    g_object_unref(pixbuf);
}

TEST(ColorExtractor, DeriveScheme) {
    auto palette = derive_scheme({255, 0, 0});
    // Background should be dark red-tinted
    EXPECT_LT(palette.background[0], 60);
    EXPECT_GT(palette.accent1[0], 150);
}
```

### 7.2 Integration Tests (Python Level)
```python
# tests/test_cpp_integration.py
import pytest
from lollypop.color_extractor import extract_palette
from lollypop.artwork_manager import ArtworkManager

def test_extract_palette_matches_python():
    """C++ and Python implementations produce identical results."""
    path = "test/data/cover.jpg"
    cpp_result = extract_palette(path)  # Uses C++ if available
    py_result = _extract_palette_python(path)  # Direct Python call
    for c, p in zip(cpp_result, py_result):
        for cv, pv in zip(c, p):
            assert abs(cv - pv) <= 1, f"Mismatch: {cv} vs {pv}"

def test_blur_preserves_dimensions():
    mgr = ArtworkManager()
    pixbuf = GdkPixbuf.Pixbuf.new_from_file("test/data/cover.jpg")
    orig_w, orig_h = pixbuf.get_width(), pixbuf.get_height()
    mgr._get_blur(pixbuf, 25)
    assert pixbuf.get_width() == orig_w
    assert pixbuf.get_height() == orig_h
```

### 7.3 Performance Benchmarks
```python
# benchmarks/bench_color.py
import timeit
from lollypop.color_extractor import extract_palette

def bench():
    for _ in range(100):
        extract_palette("test/data/large_cover.jpg")

# Run: python -m pytest benchmarks/bench_color.py --benchmark-only
```

**Target Metrics**:
| Operation | Python (ms) | C++ Target (ms) |
|-----------|-------------|-----------------|
| extract_palette (500×500 JPEG) | 15-25 | < 0.5 |
| gaussian_blur (200×200, r=25) | 80-120 | < 5 |
| discover_uri (FLAC) | 200-500 | < 50 |

---

## 8. Deployment & Compatibility

### 8.1 Installation
```meson
# meson.build - install .so alongside Python modules
install_subdir(
    'lollypop',
    install_dir: python_dir,
)
# lollypop_ext.cpython-311-darwin.so gets installed to:
# /usr/lib/python3.11/site-packages/lollypop/lollypop_ext.*.so
```

### 8.2 Runtime Detection
```python
# lollypop/__init__.py
import sys
import platform

def _load_native_module():
    # Try platform-specific names
    patterns = [
        f"lollypop_ext.cpython-{sys.version_info.major}{sys.version_info.minor}*.so",
        "lollypop_ext.so",  # fallback
    ]
    for pattern in patterns:
        for path in sys.path:
            matches = glob.glob(os.path.join(path, "lollypop", pattern))
            if matches:
                return matches[0]
    return None

_NATIVE_PATH = _load_native_module()
if _NATIVE_PATH:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("lollypop_ext", _NATIVE_PATH)
        lollypop_ext = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lollypop_ext)
        sys.modules["lollypop.lollypop_ext"] = lollypop_ext
        NATIVE_AVAILABLE = True
    except Exception as e:
        logger.warning(f"Failed to load native module: {e}")
        NATIVE_AVAILABLE = False
else:
    NATIVE_AVAILABLE = False
```

### 8.3 Flatpak/Flathub Considerations
- Add `pybind11` as module dependency in `org.gnome.Lollypop.json`
- Ensure `gdk-pixbuf-2.0`, `gstreamer-1.0` are in SDK
- Build C++ module as part of Flatpak manifest (not host)

---

## 9. Rollout Plan

| Phase | Tasks | Duration | Risk |
|-------|-------|----------|------|
| **0. Foundation** | Add pybind11 to meson, create `cpp/` skeleton, CI build | 2 days | Low |
| **1. Color Extractor** | Port `extract_palette` + `derive_scheme`, add tests, benchmark | 3 days | Low |
| **2. Blur** | Port `_get_blur` (separable Gaussian), test with various radii | 2 days | Medium |
| **3. Tag Reader** | Wrap `GstPbutilsDiscoverer`, thread pool integration | 5 days | High |
| **4. Integration** | Update all call sites, add fallback logic, CI/CD | 2 days | Low |
| **5. Validation** | Full test suite, memory leak check (valgrind), Flatpak build | 3 days | Medium |

**Total**: ~17 days for complete migration.

---

## 10. Rollback Procedure

If C++ module causes crashes/regressions:

```python
# Emergency disable via environment variable
import os
if os.environ.get("LOLLYPOP_DISABLE_CPP") == "1":
    NATIVE_AVAILABLE = False
```

No code changes needed - users can set `LOLLYPOP_DISABLE_CPP=1` in `.bashrc`.

---

## 11. Monitoring & Observability

Add to each C++ function:
```cpp
// Telemetry (optional, behind define)
#ifdef LOLLYPOP_TELEMETRY
    auto start = std::chrono::high_resolution_clock::now();
    // ... work ...
    auto elapsed = std::chrono::high_resolution_clock::now() - start;
    PySys_WriteStdout("lollypop_ext: %s took %lld us\n", __func__, 
                      std::chrono::duration_cast<std::chrono::microseconds>(elapsed).count());
#endif
```

Python side logs when fallback triggers:
```python
except Exception as e:
    logger.warning(f"C++ {func.__name__} failed: {e}, falling back to Python")
    metrics.increment("cpp_fallback", tags={"function": func.__name__})
```

---

## 12. Appendix: Pybind11 Gotchas

| Issue | Solution |
|-------|----------|
| GdkPixbuf refcount | Use `PyCapsule` with destructor, not raw pointer |
| GStreamer init | Call `gst_init()` once in module init |
| Thread safety | Each thread needs own `GstDiscoverer`; use thread-local storage |
| ABI stability | Compile against same Python/GTK/GStreamer as runtime |
| Debug builds | Add `-DDEBUG` to see pybind11 type caster errors |

---

**Next Step**: Create `cpp/common.hpp` and `meson.build` additions, then implement color extractor as proof-of-concept.