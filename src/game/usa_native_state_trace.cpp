#include "usa_native_state_trace.hpp"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <optional>

namespace goemon64::usa_native_trace {
namespace {
constexpr std::uint32_t kSysPointerAddr = 0x8015C5C8;
constexpr std::uint32_t kExpectedSysPointer = 0x8008CCC0;
constexpr std::uint32_t kOuterStateAddr = 0x800C7A94;
constexpr std::uint32_t kSecondaryAddr = 0x800C7A96;
constexpr std::uint32_t kTertiaryAddr = 0x800C7A97;
constexpr std::uint32_t kRoomIdAddr = 0x800C7AB2;
constexpr std::uint32_t kSelectionAddr = 0x800C7AB4;
constexpr std::uint32_t kGuestMask = 0x007FFFFF;
constexpr std::uint64_t kMaxEvents = 4096;

std::atomic<bool> enabled{false};
std::mutex trace_lock;
std::optional<Snapshot> last_step_snapshot;
std::chrono::steady_clock::time_point started;
std::uint64_t observed_step_calls = 0;
std::uint64_t emitted_events = 0;

// All addresses above are fixed, authenticated USA RDRAM globals (<8 MiB).
// N64Recomp stores the guest in word-swapped order on x86-64.
std::uint8_t guest_byte(const std::uint8_t* ram, std::uint32_t addr) {
    return ram[(addr & kGuestMask) ^ 3u];
}

std::uint16_t guest_half(const std::uint8_t* ram, std::uint32_t addr) {
    return static_cast<std::uint16_t>((guest_byte(ram, addr) << 8) |
                                      guest_byte(ram, addr + 1u));
}

std::uint32_t guest_word(const std::uint8_t* ram, std::uint32_t addr) {
    return (std::uint32_t{guest_byte(ram, addr)} << 24) |
           (std::uint32_t{guest_byte(ram, addr + 1u)} << 16) |
           (std::uint32_t{guest_byte(ram, addr + 2u)} << 8) |
            std::uint32_t{guest_byte(ram, addr + 3u)};
}

std::uint64_t elapsed_us() {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started).count());
}

void emit_snapshot(const char* kind, std::uint64_t call_number, const Snapshot& value) {
    if (emitted_events >= kMaxEvents) return;
    ++emitted_events;
    std::fprintf(stderr,
        "[Goemon USA native state trace] type=%s elapsed_us=%llu step_call=%llu "
        "outer=%u secondary=%u tertiary=%u room=%u selection=%u "
        "scene_entry_confirmed=0 guest_pc=unavailable guest_frame=unavailable\n",
        kind, static_cast<unsigned long long>(elapsed_us()),
        static_cast<unsigned long long>(call_number),
        static_cast<unsigned>(value.outer_state),
        static_cast<unsigned>(value.secondary_state),
        static_cast<unsigned>(value.tertiary_state),
        static_cast<unsigned>(value.room_id),
        static_cast<unsigned>(value.room_group_selection));
}
} // namespace

bool decode_usa_snapshot(const std::uint8_t* rdram, Snapshot& out) {
    if (!rdram || guest_word(rdram, kSysPointerAddr) != kExpectedSysPointer) {
        return false;
    }
    // Validate the outer dispatcher selector; never interpret a mismatched
    // region's memory as USA state. Startup state zero remains admissible.
    const auto outer = guest_byte(rdram, kOuterStateAddr);
    if (outer >= 18) return false;
    out.outer_state = outer;
    out.secondary_state = guest_byte(rdram, kSecondaryAddr);
    out.tertiary_state = guest_byte(rdram, kTertiaryAddr);
    out.room_id = guest_half(rdram, kRoomIdAddr);
    out.room_group_selection = guest_half(rdram, kSelectionAddr);
    return true;
}

void select_region(bool japan) {
    const char* env = std::getenv("GOEMON_USA_NATIVE_STATE_TRACE");
    const bool opt_in = env && std::strcmp(env, "1") == 0;
    // Publish region selection after all shared state is reset.
    std::lock_guard lock(trace_lock);
    enabled.store(false, std::memory_order_release);
    last_step_snapshot.reset();
    observed_step_calls = 0;
    emitted_events = 0;
    started = std::chrono::steady_clock::now();
    if (opt_in && !japan) {
        std::fprintf(stderr,
            "[Goemon USA native state trace] enabled=1 region=USA "
            "evidence=guest_callback_not_frame_atomic "
            "overlay_events=registration_requests_only max_events=%llu\n",
            static_cast<unsigned long long>(kMaxEvents));
        enabled.store(true, std::memory_order_release);
    }
}

void observe_guest_step_callback(const std::uint8_t* rdram) {
    if (!enabled.load(std::memory_order_acquire)) return;
    Snapshot current;
    if (!decode_usa_snapshot(rdram, current)) return;
    std::lock_guard lock(trace_lock);
    if (!enabled.load(std::memory_order_relaxed)) return;
    ++observed_step_calls;
    if (!last_step_snapshot || *last_step_snapshot != current) {
        emit_snapshot("step_callback_snapshot_changed", observed_step_calls, current);
        last_step_snapshot = current;
    }
}

void observe_overlay_registration_request(const std::uint8_t* rdram,
                                          std::uint32_t rom_source,
                                          std::uint32_t guest_destination,
                                          std::uint32_t source_length) {
    if (!enabled.load(std::memory_order_acquire)) return;
    Snapshot current;
    const bool valid = decode_usa_snapshot(rdram, current);
    std::lock_guard lock(trace_lock);
    if (!enabled.load(std::memory_order_relaxed) || emitted_events >= kMaxEvents) return;
    ++emitted_events;
    std::fprintf(stderr,
        "[Goemon USA native state trace] type=overlay_registration_request "
        "elapsed_us=%llu source_rom=0x%08X guest_destination=0x%08X "
        "source_length=%u usa_sys_valid=%u "
        "outer=%u room=%u selection=%u "
        "copy_completed=0 relocation_confirmed=0 guest_pc=unavailable\n",
        static_cast<unsigned long long>(elapsed_us()),
        rom_source, guest_destination, source_length,
        static_cast<unsigned>(valid),
        static_cast<unsigned>(valid ? current.outer_state : 0),
        static_cast<unsigned>(valid ? current.room_id : 0),
        static_cast<unsigned>(valid ? current.room_group_selection : 0));
}
void observe_file_loader_postprocess(const std::uint8_t* rdram,
                                        std::uint32_t file_id,
                                        std::uint32_t rom_source,
                                        std::uint32_t guest_destination,
                                        std::uint32_t source_length) {
    if (!enabled.load(std::memory_order_acquire)) return;
    Snapshot current;
    const bool valid = decode_usa_snapshot(rdram, current);
    std::lock_guard lock(trace_lock);
    if (!enabled.load(std::memory_order_relaxed) || emitted_events >= kMaxEvents) return;
    ++emitted_events;
    std::fprintf(stderr,
        "[Goemon USA native state trace] type=file_loader_postprocess_returned "
        "elapsed_us=%llu file_id=%u source_rom=0x%08X guest_destination=0x%08X "
        "source_length=%u usa_sys_valid=%u outer=%u room=%u selection=%u "
        "copy_or_decompress_returned=1 relocation_call_returned=1 "
        "tail_zero_fill_done=1 data_verified=0 guest_pc=unavailable guest_frame=unavailable\n",
        static_cast<unsigned long long>(elapsed_us()), file_id,
        rom_source, guest_destination, source_length,
        static_cast<unsigned>(valid),
        static_cast<unsigned>(valid ? current.outer_state : 0),
        static_cast<unsigned>(valid ? current.room_id : 0),
        static_cast<unsigned>(valid ? current.room_group_selection : 0));
}

} // namespace goemon64::usa_native_trace
