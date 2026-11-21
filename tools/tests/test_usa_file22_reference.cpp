#include "../../src/game/usa_file22_reference.hpp"
#include <cassert>
#include <memory>
#include <fstream>
#include <iostream>
int main(int argc, char** argv) {
    using namespace goemon64::usa_file22_reference;
    auto ram = std::make_unique<std::uint8_t[]>(0x800000);
    assert(!inspect(nullptr, kFile22Vram, kFile22Rom, kFile22Size).guest_window_ok);
    assert(!inspect(ram.get(), kFile22Vram, kFile22Rom, kFile22Size - 1).guest_window_ok);
    assert(!inspect(ram.get(), kFile22Vram, kFile22Rom, kFile22Size, "/missing").reference_ok);
    if (argc > 1) {
        std::ifstream f(argv[1], std::ios::binary);
        assert(f);
        f.seekg(kFile22Rom);
        for (std::uint32_t i = 0; i < kFile22Size; i++) {
            ram[((kFile22Vram + i) & 0x7FFFFFu) ^ 3u] = static_cast<std::uint8_t>(f.get());
        }
        auto ok = inspect(ram.get(), kFile22Vram, kFile22Rom, kFile22Size, argv[1]);
        assert(ok.guest_window_ok && ok.reference_ok && ok.exact_bytes && ok.mismatched_bytes == 0);
        ram[((kFile22Vram + 100) & 0x7FFFFFu) ^ 3u] ^= 1;
        auto bad = inspect(ram.get(), kFile22Vram, kFile22Rom, kFile22Size, argv[1]);
        assert(bad.reference_ok && !bad.exact_bytes && bad.mismatched_bytes == 1 && bad.first_mismatch == 100);
    }
    std::cout << "file22 reference whole-image exact comparison: PASS\n";
}
