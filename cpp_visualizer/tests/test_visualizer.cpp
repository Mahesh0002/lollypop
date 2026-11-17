#include <gtest/gtest.h>
#include "../include/visualizer_types.h"
#include "../include/visualizer_tick.h"
#include "../include/mel_remap.h"
#include "../include/cava_parse.h"
#include "../include/spectrum_normalize.h"
#include "../include/moving_average.h"
#include "../include/accent_extract.h"
#include "../include/particle_system.h"

using namespace lollypop;

TEST(VisualizerTick, SyntheticFallbackProducesValues) {
    VisualizerState state;
    VisualizerConfig config;
    state.live = true;   // synthetic fallback runs only while live (not real data)
    state.phase = 0.0;

    visualizer_tick(state, config, 0.0);

    // Should have synthetic values
    float max_val = 0.0f;
    for (int i = 0; i < POINT_COUNT; ++i) {
        EXPECT_GE(state.points[i], 0.0f);
        EXPECT_LE(state.points[i], MAX_VALUE);
        if (state.points[i] > max_val) max_val = state.points[i];
    }
    EXPECT_GT(max_val, 0.0f);
}

TEST(VisualizerTick, LiveWithTargetSmooths) {
    VisualizerState state;
    VisualizerConfig config;
    state.live = true;
    state.real_data = true;
    state.target.fill(500.0f);
    state.points.fill(0.0f);

    visualizer_tick(state, config, 0.0);

    // Should move toward target with attack factor
    for (int i = 0; i < POINT_COUNT; ++i) {
        EXPECT_NEAR(state.points[i], 500.0f * ATTACK_FACTOR, 1.0f);
    }
}

TEST(CavaParse, ParsesValidFrame) {
    std::array<float, POINT_COUNT> out;
    const char* data = "100;200;300;400;500;600;700;800;900;1000";
    int count = parse_cava_frame(data, strlen(data), out);

    EXPECT_EQ(count, 10);
    EXPECT_FLOAT_EQ(out[0], 100.0f);
    EXPECT_FLOAT_EQ(out[9], 1000.0f);
    // Rest should be zero
    for (int i = 10; i < POINT_COUNT; ++i) {
        EXPECT_FLOAT_EQ(out[i], 0.0f);
    }
}

TEST(CavaParse, ClampsValues) {
    std::array<float, POINT_COUNT> out;
    const char* data = "2000;-100;500";
    int count = parse_cava_frame(data, strlen(data), out);

    EXPECT_EQ(count, 3);
    EXPECT_FLOAT_EQ(out[0], 1000.0f);  // clamped
    EXPECT_FLOAT_EQ(out[1], 0.0f);     // clamped
    EXPECT_FLOAT_EQ(out[2], 500.0f);
}

TEST(MelRemap, IdentityFor64Bands) {
    // MEL_BANDS == POINT_COUNT, so output should be similar
    std::array<float, 513> spectrum{};
    spectrum[256] = 1.0f;  // Single peak at center

    std::array<float, MEL_BANDS> out;
    mel_remap(spectrum.data(), 513, out);

    // Energy should be distributed around middle bands
    float sum = 0.0f;
    for (float v : out) sum += v;
    EXPECT_GT(sum, 0.0f);
}

TEST(MovingAverage, ReducesVariance) {
    std::array<float, POINT_COUNT> input{};
    for (int i = 0; i < POINT_COUNT; ++i) {
        input[i] = (i % 2 == 0) ? 1000.0f : 0.0f;
    }

    std::array<float, POINT_COUNT> output;
    moving_average(input, output, 2);

    // Should smooth the alternating pattern
    for (int i = 2; i < POINT_COUNT - 2; ++i) {
        EXPECT_GT(output[i], 0.0f);
        EXPECT_LT(output[i], 1000.0f);
    }
}

TEST(SpectrumNormalize, ProducesValidOutput) {
    std::array<float, 513> magnitude{};
    // Simulate bass-heavy spectrum
    for (int i = 0; i < 50; ++i) magnitude[i] = -40.0f;  // Bass
    for (int i = 50; i < 513; ++i) magnitude[i] = -80.0f; // Silence

    std::array<float, MEL_BANDS> out;
    float gain = 1.0f;
    bool seen = false;

    normalize_spectrum(magnitude.data(), 513, out, gain, seen);

    EXPECT_TRUE(seen);
    EXPECT_GT(gain, 1.0f);  // Should boost to normalize

    for (float v : out) {
        EXPECT_GE(v, 0.0f);
        EXPECT_LE(v, MAX_VALUE);
    }
}

TEST(ParticleSystem, SpawnsAndUpdates) {
    VisualizerState state;
    VisualizerConfig config;
    state.live = true;

    std::array<float, POINT_COUNT> points{};
    points[32] = MAX_VALUE;  // Strong middle bar

    // Spawn
    spawn_particles(state, config, points, 300, 60, 8);
    EXPECT_GT(state.particle_count, 0);

    // Update (should apply gravity, fade)
    update_particles(state, config, points, 300, 60, false);

    // Particles should still be alive
    EXPECT_GT(state.particle_count, 0);

    // Advance time until dead
    for (int i = 0; i < 100; ++i) {
        update_particles(state, config, points, 300, 60, false);
    }
    EXPECT_EQ(state.particle_count, 0);
}

TEST(AccentExtract, HandlesColorfulImage) {
    // Create 32x32 red image
    std::vector<uint8_t> pixels(32 * 32 * 4, 0);
    for (int i = 0; i < 32 * 32; ++i) {
        pixels[i * 4 + 0] = 255;  // R
        pixels[i * 4 + 1] = 0;    // G
        pixels[i * 4 + 2] = 0;    // B
        pixels[i * 4 + 3] = 255;  // A
    }

    float r, g, b;
    extract_accent(pixels.data(), 32, 32, 32 * 4, 4, r, g, b);

    EXPECT_GT(r, 0.5f);
    EXPECT_LT(g, 0.1f);
    EXPECT_LT(b, 0.1f);
}

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}