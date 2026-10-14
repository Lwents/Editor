// Fused caption masks. Thresholds and morphology match the Python reference.
#include <stdint.h>
#include <stddef.h>
#ifdef _WIN32
#define EXPORT extern "C" __declspec(dllexport)
#else
#define EXPORT extern "C" __attribute__((visibility("default")))
#endif

EXPORT int subtitle_masks(const uint8_t* bgr, const uint8_t* gray,
                         size_t height, size_t width, uint8_t* white,
                         uint8_t* signature, uint8_t* horizontal) {
    if (!bgr || !gray || !white || !signature || !horizontal || !height || !width) return -1;
    for (size_t y = 0; y < height; ++y) {
        for (size_t x = 0; x < width; ++x) {
            const size_t i = y * width + x;
            const uint8_t* p = bgr + i * 3;
            unsigned high = p[0], low = p[0];
            for (int c = 1; c < 3; ++c) { if (p[c] > high) high = p[c]; if (p[c] < low) low = p[c]; }
            white[i] = gray[i] >= 190 && high - low <= 65;
            uint8_t dark = 0;
            const size_t left = x > 2 ? x - 2 : 0;
            const size_t right = x + 2 < width ? x + 2 : width - 1;
            for (size_t k = left; k <= right; ++k) dark |= gray[y * width + k] < 135;
            horizontal[i] = dark;
        }
    }
    size_t count = 0;
    for (size_t y = 0; y < height; ++y) {
        const size_t top = y > 2 ? y - 2 : 0;
        const size_t bottom = y + 2 < height ? y + 2 : height - 1;
        for (size_t x = 0; x < width; ++x) {
            uint8_t dark = 0;
            for (size_t k = top; k <= bottom; ++k) dark |= horizontal[k * width + x];
            const size_t i = y * width + x;
            signature[i] = white[i] & dark;
            count += signature[i];
        }
    }
    if (count < 30) for (size_t i = 0; i < height * width; ++i) signature[i] = white[i];
    return 0;
}

EXPORT void subtitle_compare(const uint8_t* a, const uint8_t* b, size_t height,
                             size_t width, size_t stride_a, size_t stride_b,
                             uint64_t* changed, uint64_t* combined) {
    uint64_t diff = 0, uni = 0;
    for (size_t y = 0; y < height; ++y) for (size_t x = 0; x < width; ++x) {
        const uint8_t av = a[y * stride_a + x], bv = b[y * stride_b + x];
        diff += av != bv;
        uni += (av | bv) != 0;
    }
    *changed = diff; *combined = uni;
}
