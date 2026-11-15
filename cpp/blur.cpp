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

namespace lollypop_ext {

// ============================================================================
// Kernel Generation
// ============================================================================

std::vector<double> make_gaussian_kernel(int radius) {
    if (radius <= 0) return {1.0};
    
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
    return kernel;
}

// ============================================================================
// Horizontal Pass (convolve each row)
// ============================================================================

static void convolve_horizontal(const guchar* src, guchar* dst,
                                 int width, int height, int channels, int stride,
                                 const std::vector<double>& kernel, int radius) {
    int half = radius;
    std::vector<double> acc(channels);
    
    for (int y = 0; y < height; ++y) {
        const guchar* src_row = src + y * stride;
        guchar* dst_row = dst + y * stride;
        
        for (int x = 0; x < width; ++x) {
            std::fill(acc.begin(), acc.end(), 0.0);
            
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
                dp[c] = static_cast<guchar>(std::clamp(int(acc[c] + 0.5), 0, 255));
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
    std::vector<double> acc(channels);
    
    for (int x = 0; x < width; ++x) {
        for (int y = 0; y < height; ++y) {
            std::fill(acc.begin(), acc.end(), 0.0);
            
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
                dp[c] = static_cast<guchar>(std::clamp(int(acc[c] + 0.5), 0, 255));
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
    
    // Need temporary buffer for intermediate result
    std::vector<guchar> temp(width * height * channels);
    guchar* temp_ptr = temp.data();
    
    // Horizontal pass: src -> temp
    convolve_horizontal(pixels, temp_ptr, width, height, channels, stride, kernel, kernel.size() / 2);
    
    // Vertical pass: temp -> dest (pixbuf is writable)
    guchar* dest = const_cast<guchar*>(gdk_pixbuf_get_pixels(pixbuf));
    int dest_stride = gdk_pixbuf_get_rowstride(pixbuf);
    convolve_vertical(temp_ptr, dest, width, height, channels, stride, kernel, kernel.size() / 2);
    
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
    
    std::vector<guchar> temp(width * height * channels);
    guchar* src = const_cast<guchar*>(pixels);
    guchar* dst = temp.data();
    int temp_stride = width * channels;
    
    // Box width for 3-pass approximation: radius * sqrt(12/3) / 3 ≈ radius * 0.577
    int box_w = std::max(1, int(radius * 0.577));
    
    for (int pass = 0; pass < 3; ++pass) {
        // Horizontal box blur
        for (int y = 0; y < height; ++y) {
            // Sliding window sum for each channel
            std::vector<int> sum(channels, 0);
            
            // Initialize first window
            for (int x = 0; x <= std::min(box_w, width - 1); ++x) {
                const guchar* p = src + y * stride + x * channels;
                for (int c = 0; c < channels; ++c) sum[c] += p[c];
            }
            
            int window_size = std::min(box_w + 1, width);
            for (int x = 0; x < width; ++x) {
                guchar* dp = dst + y * temp_stride + x * channels;
                for (int c = 0; c < channels; ++c) {
                    dp[c] = static_cast<guchar>(sum[c] / window_size);
                }
                
                // Slide window
                int left = x - box_w;
                int right = x + box_w + 1;
                
                if (left >= 0) {
                    const guchar* lp = src + y * stride + left * channels;
                    for (int c = 0; c < channels; ++c) sum[c] -= lp[c];
                    --window_size;
                }
                if (right < width) {
                    const guchar* rp = src + y * stride + right * channels;
                    for (int c = 0; c < channels; ++c) sum[c] += rp[c];
                    ++window_size;
                }
            }
        }
        
        // Vertical box blur (swap src/dst)
        std::swap(src, dst);
    }
    
    // Copy final result back to pixbuf (src now points to final buffer)
    guchar* dest = const_cast<guchar*>(gdk_pixbuf_get_pixels(pixbuf));
    int dest_stride = gdk_pixbuf_get_rowstride(pixbuf);
    for (int y = 0; y < height; ++y) {
        std::memcpy(dest + y * dest_stride, src + y * temp_stride, width * channels);
    }
    
    return make_ok(true);
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
    
    GilRelease no_gil;
    
    // Create writable copy
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