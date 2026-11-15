/** Color palette extraction implementation.
 *  
 *  This file contains the performance-critical pixel walking loop
 *  and the HLS color math. Compiled with -O3, no GIL held during computation.
 */

#include "color_extractor.hpp"
#include "common.hpp"
#include <algorithm>

namespace lollypop_ext {

// ============================================================================
// Average Color Extraction
// ============================================================================

std::optional<std::array<double, 3>> average_color(const std::string& path) {
    // Load and downscale to 64x64 (matching Python's ColorQuantizer rescaleSize)
    auto load_result = load_pixbuf_file(path);
    if (!std::holds_alternative<PixbufPtr>(load_result)) {
        return std::nullopt;
    }
    PixbufPtr src = std::get<PixbufPtr>(std::move(load_result));
    
    // Scale to 64x64
    auto scale_result = scale_pixbuf(src.get(), 64, 64, GDK_INTERP_BILINEAR);
    if (!std::holds_alternative<PixbufPtr>(scale_result)) {
        return std::nullopt;
    }
    PixbufPtr small = std::get<PixbufPtr>(std::move(scale_result));
    
    // Get pixel info
    int width, height, channels, stride;
    const guchar* pixels;
    get_pixbuf_info(small.get(), width, height, channels, stride, pixels);
    
    // Verify dimensions
    if (width != 64 || height != 64 || channels < 3) {
        return std::nullopt;
    }
    
    bool has_alpha = pixbuf_has_alpha(small.get());
    int pixel_stride = has_alpha ? 4 : 3;
    
    // Accumulate in double for precision
    double r_sum = 0.0, g_sum = 0.0, b_sum = 0.0;
    long count = 0;
    
    // Hot loop: walk 64x64 = 4096 pixels
    for (int y = 0; y < 64; ++y) {
        const guchar* row = pixels + y * stride;
        for (int x = 0; x < 64; ++x) {
            const guchar* p = row + x * pixel_stride;
            
            // Skip transparent pixels (matching Python ColorQuantizer behavior)
            if (has_alpha && p[3] == 0) continue;
            
            r_sum += p[0];
            g_sum += p[1];
            b_sum += p[2];
            ++count;
        }
    }
    
    if (count == 0) return std::nullopt;
    
    return std::array<double, 3>{r_sum / count / 255.0, 
                                 g_sum / count / 255.0, 
                                 b_sum / count / 255.0};
}

// ============================================================================
// Palette Derivation (AdaptedMaterialScheme)
// ============================================================================

Palette derive_scheme(const std::array<double, 3>& seed) {
    using namespace detail;
    
    // Background: dark base tinted by seed (50% blend), forced dark
    auto background = make_background(mix(BASE_BG, seed, 0.5));
    
    // Accent1: seed hue+sat at base accent lightness, then blend back to seed
    auto adapted = adapt_to_accent(BASE_ACCENT, seed);
    auto accent1 = make_accent(mix(adapted, seed, 0.5));
    
    // Accent2: hue-shifted companion for selection highlights
    auto accent2 = make_accent(hue_shift(mix(adapted, seed, 0.5), 0.08));
    
    // Ensure accent2 is distinct from accent1
    if (distance_rgb(
            {c8(accent2[0]), c8(accent2[1]), c8(accent2[2])},
            {c8(accent1[0]), c8(accent1[1]), c8(accent1[2])}
        ) < 40.0) {
        accent2 = hue_shift(accent1, 0.12);
        accent2 = make_accent(accent2);
    }
    
    return Palette{
        {c8(background[0]), c8(background[1]), c8(background[2])},
        {c8(accent1[0]), c8(accent1[1]), c8(accent1[2])},
        {c8(accent2[0]), c8(accent2[1]), c8(accent2[2])}
    };
}

// ============================================================================
// Public API
// ============================================================================

Result<Palette> extract_palette(const std::string& path) {
    // Release GIL for the entire computation (I/O + CPU)
    GilRelease no_gil;
    
    auto seed = average_color(path);
    if (!seed) {
        return make_error<Palette>("Failed to extract average color from image");
    }
    
    Palette palette = derive_scheme(*seed);
    return make_ok(std::move(palette));
}

}  // namespace lollypop_ext