#pragma once
/** Gaussian blur implementation for GdkPixbuf.
 *
 *  Uses separable convolution (horizontal + vertical pass) for O(n*r) complexity
 *  instead of O(n*r^2). Supports radius up to ~100 efficiently.
 *  
 *  Port of PIL's ImageFilter.GaussianBlur but operating directly on GdkPixbuf
 *  pixel data to avoid Python/PIL round-trip overhead.
 */

#include "common.hpp"

namespace lollypop_ext {

enum class BlurMode {
    GAUSSIAN,   // True Gaussian via separable convolution
    BOX_APPROX  // Fast box blur approximation (3 passes = ~Gaussian)
};

/** In-place Gaussian blur on GdkPixbuf.
 *  Modifies the pixbuf directly. Returns error string on failure. */
Result<bool> gaussian_blur_inplace(GdkPixbuf* pixbuf, int radius, 
                                   BlurMode mode = BlurMode::GAUSSIAN);

/** Copy + blur: returns new pixbuf, leaves source unchanged. */
Result<PixbufPtr> gaussian_blur_copy(GdkPixbuf* src, int radius,
                                     BlurMode mode = BlurMode::GAUSSIAN);

/** Generate 1D Gaussian kernel for given radius (sigma = radius / 3). */
std::vector<double> make_gaussian_kernel(int radius);

/** Apply separable convolution: horizontal then vertical pass. */
Result<bool> separable_convolve(GdkPixbuf* pixbuf, const std::vector<double>& kernel);

/** Fast box blur approximation (3 passes of box filter ≈ Gaussian). */
Result<bool> box_blur_approximation(GdkPixbuf* pixbuf, int radius);

/** Blur raw pixel bytes and return a newly allocated copy.
 *
 *  ``pixels`` points to ``stride * height`` bytes (rowstride may include
 *  padding).  Returns the blurred pixels in a fresh buffer of the same size.
 *  Ownership: caller must not mutate ``pixels`` during the call. */
std::vector<guchar> blur_bytes(const guchar* pixels, int width, int height,
                               int channels, int stride, int radius,
                               BlurMode mode = BlurMode::GAUSSIAN);

}  // namespace lollypop_ext