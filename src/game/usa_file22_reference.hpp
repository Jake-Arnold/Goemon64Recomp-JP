#pragma once
#include <array>
#include <cstdint>
#include <fstream>

// Independent byte-for-byte comparison with the authenticated original-USA
// decompressed ROM at ./mnsg.z64. The runner MUST verify the complete 32 MiB
// SHA-1 before launch. FNV-1a fingerprints check the reference slice against
// the separately reconstructed reference, but are not cryptographic proofs.
namespace goemon64::usa_file22_reference {
inline constexpr std::uint32_t kFile22Rom = 0x0069E080u;
inline constexpr std::uint32_t kFile22Vram = 0x801CB460u;
inline constexpr std::uint32_t kFile22Size = 47264u;
inline constexpr std::uint32_t kFile22CodeSize = 4992u;
inline constexpr std::uint64_t kFile22FileFnv64 = 0xF9F3DD859DC68E14ull;
inline constexpr std::uint64_t kFile22CodeFnv64 = 0x8512C70D7D6C1A83ull;

struct Evidence {
    bool guest_window_ok = false;
    bool reference_ok = false;
    bool exact_bytes = false;
    std::uint32_t mismatched_bytes = 0;
    std::uint32_t first_mismatch = 0xFFFFFFFFu;
};

inline std::uint64_t fnv64(const std::uint8_t* bytes, std::uint32_t count) {
    std::uint64_t hash = 0xcbf29ce484222325ull;
    for (std::uint32_t i = 0; i < count; ++i) {
        hash ^= bytes[i];
        hash *= 0x100000001b3ull;
    }
    return hash;
}

inline Evidence inspect(const std::uint8_t* ram,
                        std::uint32_t guest_address,
                        std::uint32_t rom_source,
                        std::uint32_t source_length,
                        const char* path = "mnsg.z64") {
    Evidence out;
    const std::uint32_t physical = guest_address & 0x1FFFFFFFu;
    if (!ram || guest_address != kFile22Vram || rom_source != kFile22Rom ||
        source_length != kFile22Size || physical > 0x800000u ||
        source_length > 0x800000u - physical) return out;
    out.guest_window_ok = true;
    std::ifstream source(path, std::ios::in | std::ios::binary | std::ios::ate);
    if (!source || source.tellg() != std::streampos{33554432}) return out;
    source.seekg(kFile22Rom);
    std::array<std::uint8_t, kFile22Size> expected{};
    source.read(reinterpret_cast<char*>(expected.data()), expected.size());
    if (!source || source.gcount() != static_cast<std::streamsize>(expected.size())) return out;
    // This reference check is additional defence against selecting an
    // unrelated ROM, and does not replace the runner's full-ROM SHA-1 gate.
    if (fnv64(expected.data(), kFile22Size) != kFile22FileFnv64 ||
        fnv64(expected.data(), kFile22CodeSize) != kFile22CodeFnv64) return out;
    out.reference_ok = true;
    for (std::uint32_t i = 0; i < kFile22Size; ++i) {
        // Native x86-64 recomp RDRAM uses word-swapped guest bytes.
        const std::uint8_t observed = ram[((physical + i) & 0x7FFFFFu) ^ 3u];
        if (observed != expected[i]) {
            ++out.mismatched_bytes;
            if (out.first_mismatch == 0xFFFFFFFFu) out.first_mismatch = i;
        }
    }
    out.exact_bytes = out.mismatched_bytes == 0;
    return out;
}
} // namespace goemon64::usa_file22_reference
