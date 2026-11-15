#pragma once
/** Color palette extraction from album artwork.
 *
 *  Port of Python color_extractor.py:
 *  - _average_color(): downscale to 64x64, compute mean RGB
 *  - derive_scheme(): blend seed into base roles (AdaptedMaterialScheme)
 *  - extract_palette(): public API combining both
 *
 *  All math uses double precision HLS color space (colorsys equivalent).
 *  No Python dependencies in hot path.
 */

#include "common.hpp"
#include <array>
#include <cmath>

namespace lollypop_ext {

// ============================================================================
// Constants (match Python color_extractor.py)
// ============================================================================

// Base colours for AdaptedMaterialScheme
inline constexpr std::array<double, 3> BASE_BG     = {24.0/255.0, 24.0/255.0, 34.0/255.0};
inline constexpr std::array<double, 3> BASE_ACCENT = {176.0/255.0, 156.0/255.0, 236.0/255.0};

// Lightness/saturation bounds
inline constexpr double BG_L_MIN = 0.11, BG_L_MAX = 0.34, BG_S_MAX = 0.75;
inline constexpr double ACC_L_MIN = 0.52, ACC_L_MAX = 0.76, ACC_S_MIN = 0.40;

// Neutral fallback palette (RGB 0-255)
inline constexpr std::array<std::array<int, 3>, 3> NEUTRAL_PALETTE = {{
    {{34, 34, 44}},    // background
    {{176, 156, 236}}, // accent1
    {{126, 176, 226}}  // accent2
}};

// ============================================================================
// Public Types
// ============================================================================

/** Three-colour palette for panel theming. */
struct Palette {
    std::array<int, 3> background;  // RGB 0-255
    std::array<int, 3> accent1;
    std::array<int, 3> accent2;
};

// ============================================================================
// Internal Color Math (colorsys equivalents)
// ============================================================================

namespace detail {

/** Clamp to [0, 1]. */
inline double clamp01(double v) noexcept {
    return v < 0.0 ? 0.0 : (v > 1.0 ? 1.0 : v);
}

/** Clamp to [0, 255] and round. */
inline int c8(double v) noexcept {
    int i = int(std::round(v * 255.0));
    return i < 0 ? 0 : (i > 255 ? 255 : i);
}

/** RGB [0,1] -> HLS [0,1]. */
inline std::array<double, 3> rgb_to_hls(const std::array<double, 3>& rgb) noexcept {
    double r = rgb[0], g = rgb[1], b = rgb[2];
    double cmax = std::max({r, g, b});
    double cmin = std::min({r, g, b});
    double delta = cmax - cmin;
    
    double l = (cmax + cmin) / 2.0;
    double h = 0.0, s = 0.0;
    
    if (delta > 1e-10) {
        if (l <= 0.5) s = delta / (cmax + cmin);
        else          s = delta / (2.0 - cmax - cmin);
        
        if (cmax == r) h = (g - b) / delta + (g < b ? 6.0 : 0.0);
        else if (cmax == g) h = (b - r) / delta + 2.0;
        else              h = (r - g) / delta + 4.0;
        h /= 6.0;
    }
    return {h, l, s};
}

/** HLS [0,1] -> RGB [0,1]. */
inline std::array<double, 3> hls_to_rgb(const std::array<double, 3>& hls) noexcept {
    double h = hls[0], l = hls[1], s = hls[2];
    if (s < 1e-10) return {l, l, l};
    
    auto hue_to_rgb = [](double p, double q, double t) {
        if (t < 0.0) t += 1.0;
        if (t > 1.0) t -= 1.0;
        if (t < 1.0/6.0) return p + (q - p) * 6.0 * t;
        if (t < 0.5) return q;
        if (t < 2.0/3.0) return p + (q - p) * (2.0/3.0 - t) * 6.0;
        return p;
    };
    
    double q = l < 0.5 ? l * (1.0 + s) : l + s - l * s;
    double p = 2.0 * l - q;
    
    return {
        hue_to_rgb(p, q, h + 1.0/3.0),
        hue_to_rgb(p, q, h),
        hue_to_rgb(p, q, h - 1.0/3.0)
    };
}

/** Euclidean distance in RGB space (0-255). */
inline double distance_rgb(const std::array<int, 3>& a, const std::array<int, 3>& b) noexcept {
    double dr = a[0] - b[0], dg = a[1] - b[1], db = a[2] - b[2];
    return std::sqrt(dr*dr + dg*dg + db*db);
}

/** Linear blend: t=1 -> all a, t=0 -> all b. */
inline std::array<double, 3> mix(const std::array<double, 3>& a, 
                                  const std::array<double, 3>& b, 
                                  double t) noexcept {
    return {a[0]*t + b[0]*(1.0-t), a[1]*t + b[1]*(1.0-t), a[2]*t + b[2]*(1.0-t)};
}

/** Adapt base color to seed's hue/saturation, keep base lightness. */
inline std::array<double, 3> adapt_to_accent(const std::array<double, 3>& base, 
                                              const std::array<double, 3>& seed) noexcept {
    auto bhsl = rgb_to_hls(base);
    auto shsl = rgb_to_hls(seed);
    return hls_to_rgb({shsl[0], bhsl[1], shsl[2]});
}

/** Force dark background: clamp lightness, desaturate. */
inline std::array<double, 3> make_background(const std::array<double, 3>& color) noexcept {
    auto hls = rgb_to_hls(color);
    hls[1] = clamp01(std::max(BG_L_MIN, std::min(BG_L_MAX, hls[1])));
    hls[2] = std::min(hls[2], BG_S_MAX);
    return hls_to_rgb(hls);
}

/** Force visible accent: clamp lightness, ensure minimum saturation. */
inline std::array<double, 3> make_accent(const std::array<double, 3>& color) noexcept {
    auto hls = rgb_to_hls(color);
    hls[1] = clamp01(std::max(ACC_L_MIN, std::min(ACC_L_MAX, hls[1])));
    if (hls[2] >= 0.12) hls[2] = std::max(hls[2], ACC_S_MIN);
    return hls_to_rgb(hls);
}

/** Shift hue by delta (0-1). */
inline std::array<double, 3> hue_shift(const std::array<double, 3>& color, double delta) noexcept {
    auto hls = rgb_to_hls(color);
    hls[0] = std::fmod(hls[0] + delta + 1.0, 1.0);
    return hls_to_rgb(hls);
}

/** Blend toward white. */
inline std::array<double, 3> lighten(const std::array<double, 3>& color, double amount) noexcept {
    // amount=0 -> no change, amount=1 -> white
    return mix({1.0, 1.0, 1.0}, color, 1.0 - amount);
}

}  // namespace detail

// ============================================================================
// Public API
// ============================================================================

/** Compute average colour of image file.
 *  Downscales to 64x64 first for speed.
 *  Returns RGB in [0,1] range, or nullopt on failure. */
std::optional<std::array<double, 3>> average_color(const std::string& path);

/** Derive 3-colour scheme from seed colour (RGB in [0,1]).
 *  Pure math, no I/O, no GIL needed. */
Palette derive_scheme(const std::array<double, 3>& seed);

/** Main entry point: extract palette from image file.
 *  Returns Palette with RGB in 0-255, or error string. */
Result<Palette> extract_palette(const std::string& path);

}  // namespace lollypop_ext