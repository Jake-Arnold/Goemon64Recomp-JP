#pragma once

#include <cstdint>
#include <optional>
#include <span>
#include <vector>

#include "jp_timer_epoch_clock.hpp"

namespace goemon_jp::savestate {

enum class TimerCommandKind : std::uint8_t {
    Add,
    Remove,
};

struct TimerCommand {
    TimerCommandKind kind;
    std::int32_t address;
};

struct TimerCheckpoint {
    reset::TimerEpochClock::SavedTime clock;
    // Timer contents and timestamps stay in guest RAM.
    std::vector<std::int32_t> active;
    std::vector<TimerCommand> pending;
};

} // namespace goemon_jp::savestate

namespace ultramodern {

// The game snapshot owner must also stop guest timer publishers and copy RAM.
std::optional<goemon_jp::savestate::TimerCheckpoint> jp_snapshot_timers();

// Call after old-generation timer rearm, using the already restored RAM.
bool jp_prepare_timer_restore(const goemon_jp::savestate::TimerCheckpoint& state,
    std::uint64_t generation, std::span<const std::uint8_t> restored_ram);

} // namespace ultramodern
