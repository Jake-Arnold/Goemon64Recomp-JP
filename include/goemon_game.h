#ifndef __GOEMON_GAME_H__
#define __GOEMON_GAME_H__

#include <cstdint>
#include <span>
#include <vector>

namespace goemon64 {
    std::vector<uint8_t> decompress_mnsg(std::span<const uint8_t> compressed_rom);
    std::vector<uint8_t> decompress_mnsg_japan(std::span<const uint8_t> compressed_rom);
};

#endif
