/** Gaussian blur implementation using separable convolution.
 *  
 *  Algorithm:
 *  1. Generate 1D Gaussian kernel (sigma = radius / 3)
 *  2. Horizontal pass: convolve each row
 *  3. Vertical pass: convolve each column
 *  
 *  Complexity: O(width * height * radius) vs O(width * height * radius^2) for 2D.
 *  For radius=25 on 200x200: ~1M ops vs ~25M ops.
 */

#include "blur.hpp"
#include <cmath>
#include <vector>
#include <algorithm>
#include <unordered_map>

namespace lollypop_ext {

// ============================================================================
// Kernel Generation
// ============================================================================

static thread_local std::unordered_map<int, std::vector<double>> kernel_cache;

std::vector<double> make_gaussian_kernel(int radius) {
    if (radius <= 0) return {1.0};
    
    // Check cache first
    auto cached = kernel_cache.find(radius);
    if (cached != kernel_cache.end()) {
        return cached->second;
    }
    
    // Sigma = radius / 3 gives ~99% within radius (3 sigma rule)
    double sigma = radius / 3.0;
    double two_sigma_sq = 2.0 * sigma * sigma;
    int size = radius * 2 + 1;
    
    std::vector<double> kernel(size);
    double sum = 0.0;
    
    for (int i = 0; i < size; ++i) {
        int x = i - radius;
        kernel[i] = std::exp(-(x * x) / two_sigma_sq);
        sum += kernel[i];
    }
    
    // Normalize
    for (double& v : kernel) v /= sum;
    
    kernel_cache[radius] = kernel;
    return kernel;
}

// ============================================================================
// Horizontal Pass (convolve each row)
// ============================================================================

static void convolve_horizontal(const guchar* src, guchar* dst,
                                 int width, int height, int channels, int stride,
                                 const std::vector<double>& kernel, int radius) {
    int half = radius;
    
#pragma omp parallel for
    for (int y = 0; y < height; ++y) {
        const guchar* src_row = src + y * stride;
        guchar* dst_row = dst + y * stride;
        // Thread-local accumulator: the parallel-for iterates over distinct rows,
        // so each thread has its own `acc`.  Declaring it here (and resetting
        // per pixel below) avoids the data race of a shared buffer.
        std::vector<double> acc(channels);
        
        for (int x = 0; x < width; ++x) {
            for (int c = 0; c < channels; ++c) acc[c] = 0.0;
            
            for (int k = -half; k <= half; ++k) {
                int sx = x + k;
                // Clamp to edge (replicate border)
                if (sx < 0) sx = 0;
                else if (sx >= width) sx = width - 1;
                
                const guchar* sp = src_row + sx * channels;
                double weight = kernel[k + half];
                
                for (int c = 0; c < channels; ++c) {
                    acc[c] += sp[c] * weight;
                }
            }
            
            guchar* dp = dst_row + x * channels;
            for (int c = 0; c < channels; ++c) {
                int v = int(acc[c] + 0.5);
                dp[c] = static_cast<guchar>(v < 0 ? 0 : (v > 255 ? 255 : v));
            }
        }
    }
}

// ============================================================================
// Vertical Pass (convolve each column)
// ============================================================================

static void convolve_vertical(const guchar* src, guchar* dst,
                               int width, int height, int channels, int stride,
                               const std::vector<double>& kernel, int radius) {
    int half = radius;
    
#pragma omp parallel for
    for (int x = 0; x < width; ++x) {
        // Thread-local accumulator: parallel-for iterates over distinct columns.
        std::vector<double> acc(channels);
        for (int y = 0; y < height; ++y) {
            for (int c = 0; c < channels; ++c) acc[c] = 0.0;
            
            for (int k = -half; k <= half; ++k) {
                int sy = y + k;
                if (sy < 0) sy = 0;
                else if (sy >= height) sy = height - 1;
                
                const guchar* sp = src + sy * stride + x * channels;
                double weight = kernel[k + half];
                
                for (int c = 0; c < channels; ++c) {
                    acc[c] += sp[c] * weight;
                }
            }
            
            guchar* dp = dst + y * stride + x * channels;
            for (int c = 0; c < channels; ++c) {
                int v = int(acc[c] + 0.5);
                dp[c] = static_cast<guchar>(v < 0 ? 0 : (v > 255 ? 255 : v));
            }
        }
    }
}

// ============================================================================
// Separable Convolution Entry Point
// ============================================================================

Result<bool> separable_convolve(GdkPixbuf* pixbuf, const std::vector<double>& kernel) {
    int width, height, channels, stride;
    const guchar* pixels;
    get_pixbuf_info(pixbuf, width, height, channels, stride, pixels);
    
    if (width <= 0 || height <= 0 || channels < 3) {
        return make_error<bool>("Invalid pixbuf dimensions");
    }
    
    // Need temporary buffer for intermediate result.  Use the pixbuf's real
    // rowstride so rows stay aligned even when GdkPixbuf adds padding.
    std::vector<guchar> temp(static_cast<size_t>(stride) * height);
    guchar* temp_ptr = temp.data();
    int temp_stride = stride;
    
    // Horizontal pass: src -> temp
    convolve_horizontal(pixels, temp_ptr, width, height, channels, stride, kernel, kernel.size() / 2);
    
    // Vertical pass: temp -> dest (pixbuf is writable)
    guchar* dest = const_cast<guchar*>(gdk_pixbuf_get_pixels(pixbuf));
    convolve_vertical(temp_ptr, dest, width, height, channels, temp_stride, kernel, kernel.size() / 2);
    
    return make_ok(true);
}

// ============================================================================
// Box Blur Approximation (3 passes = Gaussian-like)
// ============================================================================

Result<bool> box_blur_approximation(GdkPixbuf* pixbuf, int radius) {
    if (radius <= 0) return make_ok(true);
    
    int width, height, channels, stride;
    const guchar* pixels;
    get_pixbuf_info(pixbuf, width, height, channels, stride, pixels);
    
    if (width <= 0 || height <= 0 || channels < 3) {
        return make_error<bool>("Invalid pixbuf dimensions");
    }
    
    // Two scratch buffers sized to the real rowstride so rows stay aligned
    // even when GdkPixbuf adds padding.  Each pass does a horizontal box
    // blur then a vertical box blur; the buffers swap roles each pass so the
    // final result lands back in the pixbuf's own memory (no extra copy).
    std::vector<guchar> buf1(static_cast<size_t>(stride) * height);
    std::vector<guchar> buf2(static_cast<size_t>(stride) * height);
    guchar* src = const_cast<guchar*>(pixels);
    guchar* dst = buf1.data();
    
    // Box width for 3-pass approximation: radius * sqrt(12/3) / 3 ≈ radius * 0.577
    int box_w = std::max(1, int(radius * 0.577));
    
    for (int pass = 0; pass < 3; ++pass) {
        // ---------- Horizontal box blur: src -> dst ----------
#pragma omp parallel for
        for (int y = 0; y < height; ++y) {
            // Sliding window sum for each channel
            std::vector<int> sum(channels, 0);
            
            // Initialize first window
            int x_end = std::min(box_w, width - 1);
            for (int x = 0; x <= x_end; ++x) {
                const guchar* p = src + y * stride + x * channels;
                for (int c = 0; c < channels; ++c) sum[c] += p[c];
            }
            
            int window_size = std::min(box_w + 1, width);
            float inv_window_size = 1.0f / float(window_size);
            for (int x = 0; x < width; ++x) {
                guchar* dp = dst + y * stride + x * channels;
                for (int c = 0; c < channels; ++c) {
                    dp[c] = static_cast<guchar>(float(sum[c]) * inv_window_size);
                }
                
                // Slide window
                int left = x - box_w;
                int right = x + box_w + 1;
                
                if (left >= 0) {
                    const guchar* lp = src + y * stride + left * channels;
                    for (int c = 0; c < channels; ++c) sum[c] -= lp[c];
                    --window_size;
                    inv_window_size = 1.0f / float(window_size);
                }
                if (right < width) {
                    const guchar* rp = src + y * stride + right * channels;
                    for (int c = 0; c < channels; ++c) sum[c] += rp[c];
                    ++window_size;
                    inv_window_size = 1.0f / float(window_size);
                }
            }
        }
        
        // ---------- Vertical box blur: src -> dst ----------
        std::swap(src, dst);
#pragma omp parallel for
        for (int x = 0; x < width; ++x) {
            std::vector<int> sum(channels, 0);
            
            // Initialize first window
            int y_end = std::min(box_w, height - 1);
            for (int y = 0; y <= y_end; ++y) {
                const guchar* p = src + y * stride + x * channels;
                for (int c = 0; c < channels; ++c) sum[c] += p[c];
            }
            
            int window_size = std::min(box_w + 1, height);
            float inv_window_size = 1.0f / float(window_size);
            for (int y = 0; y < height; ++y) {
                guchar* dp = dst + y * stride + x * channels;
                for (int c = 0; c < channels; ++c) {
                    dp[c] = static_cast<guchar>(float(sum[c]) * inv_window_size);
                }
                
                // Slide window
                int top = y - box_w;
                int bottom = y + box_w + 1;
                
                if (top >= 0) {
                    const guchar* tp = src + top * stride + x * channels;
                    for (int c = 0; c < channels; ++c) sum[c] -= tp[c];
                    --window_size;
                    inv_window_size = 1.0f / float(window_size);
                }
                if (bottom < height) {
                    const guchar* bp = src + bottom * stride + x * channels;
                    for (int c = 0; c < channels; ++c) sum[c] += bp[c];
                    ++window_size;
                    inv_window_size = 1.0f / float(window_size);
                }
            }
        }
        std::swap(src, dst);
    }
    
    // After 3 passes `src` points back into the pixbuf's pixel memory, which
    // now holds the final blurred result.  Nothing more to copy.
    return make_ok(true);
}

// ============================================================================
// Raw pixel-bytes blur (used by the Python bytes binding)
// ============================================================================

static void destroy_notify(guchar* pixels, gpointer user_data) {
    (void)user_data;
    delete[] pixels;
}

std::vector<guchar> blur_bytes(const guchar* pixels, int width, int height,
                               int channels, int stride, int radius,
                               BlurMode mode) {
    if (!pixels || width <= 0 || height <= 0 || channels < 3 || stride <= 0 ||
        radius <= 0) {
        return {};
    }

    // Copy the input so GdkPixbuf owns its own buffer.
    guchar* owned = new (std::nothrow) guchar[static_cast<size_t>(stride) * height];
    if (!owned) return {};
    for (int y = 0; y < height; ++y) {
        std::memcpy(owned + y * stride, pixels + y * stride,
                    static_cast<size_t>(width) * channels);
    }

    bool has_alpha = (channels == 4);
    GdkPixbuf* pixbuf = gdk_pixbuf_new_from_data(
        owned, GDK_COLORSPACE_RGB, has_alpha, 8,
        width, height, stride, destroy_notify, owned);
    if (!pixbuf) {
        delete[] owned;
        return {};
    }

    // Blur in place.
    auto result = gaussian_blur_inplace(pixbuf, radius, mode);
    if (std::holds_alternative<Error>(result)) {
        g_object_unref(pixbuf);
        return {};
    }

    // Copy the blurred pixels out while the pixbuf (and its buffer) is alive.
    const guchar* src = gdk_pixbuf_get_pixels(pixbuf);
    std::vector<guchar> out(static_cast<size_t>(stride) * height);
    for (int y = 0; y < height; ++y) {
        std::memcpy(out.data() + y * stride, src + y * stride,
                    static_cast<size_t>(width) * channels);
    }

    g_object_unref(pixbuf);
    return out;
}

// ============================================================================
// Public API
// ============================================================================

Result<bool> gaussian_blur_inplace(GdkPixbuf* pixbuf, int radius, BlurMode mode) {
    if (!pixbuf) return make_error<bool>("Null pixbuf");
    if (radius <= 0) return make_ok(true);
    
    GilRelease no_gil;
    
    if (mode == BlurMode::BOX_APPROX) {
        return box_blur_approximation(pixbuf, radius);
    }
    
    auto kernel = make_gaussian_kernel(radius);
    return separable_convolve(pixbuf, kernel);
}

Result<PixbufPtr> gaussian_blur_copy(GdkPixbuf* src, int radius, BlurMode mode) {
    if (!src) return make_error<PixbufPtr>("Null pixbuf");
    if (radius <= 0) return make_ok(make_pixbuf_ptr(src));  // no-op
    
    // Create writable copy while holding the GIL (GObject ref counting).
    // `gaussian_blur_inplace` releases the GIL for the computation itself.
    int width = gdk_pixbuf_get_width(src);
    int height = gdk_pixbuf_get_height(src);
    bool has_alpha = gdk_pixbuf_get_has_alpha(src);
    
    GdkPixbuf* copy = gdk_pixbuf_new(GDK_COLORSPACE_RGB, has_alpha, 8, width, height);
    if (!copy) return make_error<PixbufPtr>("Failed to create pixbuf copy");
    
    gdk_pixbuf_copy_area(src, 0, 0, width, height, copy, 0, 0);
    
    auto result = gaussian_blur_inplace(copy, radius, mode);
    if (std::holds_alternative<Error>(result)) {
        g_object_unref(copy);
        return make_error<PixbufPtr>(std::get<Error>(result).message);
    }
    
    return make_ok(adopt_pixbuf_ptr(copy));
}

}  // namespace lollypop_ext