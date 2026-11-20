#pragma once

#include <cstdint>
#include <optional>

namespace goemon_jp::savestate {

struct GuestThreadStart {
    std::int32_t address;
    std::int32_t entrypoint;
    std::int32_t stack_pointer;
    std::int32_t argument;
};

struct GuestCheckpointCoverage {
    unsigned int threads = 0;
    unsigned int not_started = 0;
    unsigned int message_checkpoints = 0;
};

} // namespace goemon_jp::savestate

namespace ultramodern {

// These reads cover the native bindings. The owner must separately pin
// the guest generation and stop all RDRAM writers for a snapshot.
goemon_jp::savestate::GuestCheckpointCoverage jp_guest_checkpoint_coverage();
std::optional<goemon_jp::savestate::GuestThreadStart> jp_get_unstarted_guest_thread(std::int32_t address);

} // namespace ultramodern
