#include "usa_native_state_trace.hpp"
#include "usa_file22_reference.hpp"

#include <atomic>
#include <array>
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

// Only instrumentation metadata; no guest memory is modified.
bool file22_copy_verified = false;
bool file22_post_verified = false;
bool file22_slot_active = false;
std::array<std::uint32_t, 12> file22_seen_rom{};
std::size_t file22_seen_count = 0;

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
    file22_copy_verified = false;
    file22_post_verified = false;
    file22_slot_active = false;
    file22_seen_count = 0;
    file22_seen_rom.fill(0);
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

void observe_file22_copy_complete(const std::uint8_t* rdram,
                                  std::uint32_t file_id,
                                  std::uint32_t rom_source,
                                  std::uint32_t guest_destination,
                                  std::uint32_t source_length) {
    if (!enabled.load(std::memory_order_acquire) || file_id != 22u) return;
    // This callback is placed after copy/decompress, before relocation.
    const auto evidence = usa_file22_reference::inspect(
        rdram, guest_destination, rom_source, source_length);
    Snapshot current;
    const bool valid = decode_usa_snapshot(rdram, current);
    std::lock_guard lock(trace_lock);
    if (!enabled.load(std::memory_order_relaxed) || emitted_events >= kMaxEvents) return;
    file22_copy_verified = evidence.reference_ok && evidence.exact_bytes;
    file22_post_verified = false;
    file22_slot_active = false;
    file22_seen_count = 0;
    ++emitted_events;
    std::fprintf(stderr,
        "[Goemon USA native state trace] type=file22_image_attestation "
        "stage=before_relocation elapsed_us=%llu file_id=22 "
        "source_rom=0x%08X guest_destination=0x%08X source_length=%u "
        "reference_ok=%u guest_window_ok=%u exact_bytes=%u "
        "mismatches=%u first_mismatch=%u "
        "expected_relocation_delta=0 rom_sha1_preflight_required=1 "
        "usa_sys_valid=%u outer=%u guest_pc=unavailable\n",
        static_cast<unsigned long long>(elapsed_us()),
        rom_source, guest_destination, source_length,
        static_cast<unsigned>(evidence.reference_ok),
        static_cast<unsigned>(evidence.guest_window_ok),
        static_cast<unsigned>(evidence.exact_bytes),
        evidence.mismatched_bytes, evidence.first_mismatch,
        static_cast<unsigned>(valid),
        static_cast<unsigned>(valid ? current.outer_state : 0));
}

void observe_file_loader_postprocess(const std::uint8_t* rdram,
                                     std::uint32_t file_id,
                                     std::uint32_t rom_source,
                                     std::uint32_t guest_destination,
                                     std::uint32_t source_length) {
    if (!enabled.load(std::memory_order_acquire)) return;
    const bool is_file22 = file_id == 22u;
    const auto evidence = is_file22
        ? usa_file22_reference::inspect(rdram, guest_destination, rom_source, source_length)
        : usa_file22_reference::Evidence{};
    Snapshot current;
    const bool valid = decode_usa_snapshot(rdram, current);
    std::lock_guard lock(trace_lock);
    if (!enabled.load(std::memory_order_relaxed) || emitted_events >= kMaxEvents) return;
    if (is_file22) {
        file22_post_verified = evidence.reference_ok && evidence.exact_bytes
                            && file22_copy_verified;
        file22_slot_active = true;
        file22_seen_count = 0;
    } else if (guest_destination == usa_file22_reference::kFile22Vram) {
        // Other static overlays replace file 22 at the same execution slot.
        file22_slot_active = false;
        file22_post_verified = false;
    }
    ++emitted_events;
    std::fprintf(stderr,
        "[Goemon USA native state trace] type=file_loader_postprocess_returned "
        "elapsed_us=%llu file_id=%u source_rom=0x%08X guest_destination=0x%08X "
        "source_length=%u usa_sys_valid=%u outer=%u room=%u selection=%u "
        "copy_or_decompress_returned=1 relocation_call_returned=1 "
        "tail_zero_fill_done=1 data_verified=%u guest_pc=unavailable guest_frame=unavailable\n",
        static_cast<unsigned long long>(elapsed_us()), file_id,
        rom_source, guest_destination, source_length,
        static_cast<unsigned>(valid),
        static_cast<unsigned>(valid ? current.outer_state : 0),
        static_cast<unsigned>(valid ? current.room_id : 0),
        static_cast<unsigned>(valid ? current.room_group_selection : 0),
        static_cast<unsigned>(is_file22 && file22_post_verified));
    if (is_file22 && emitted_events < kMaxEvents) {
        ++emitted_events;
        std::fprintf(stderr,
            "[Goemon USA native state trace] type=file22_image_attestation "
            "stage=after_relocation elapsed_us=%llu file_id=22 "
            "source_rom=0x%08X guest_destination=0x%08X source_length=%u "
            "reference_ok=%u guest_window_ok=%u exact_bytes=%u "
            "mismatches=%u first_mismatch=%u pre_relocation_match=%u "
            "post_relocation_match=%u expected_relocation_delta=0 "
            "rom_sha1_preflight_required=1 usa_sys_valid=%u outer=%u "
            "guest_pc=unavailable\n",
            static_cast<unsigned long long>(elapsed_us()),
            rom_source, guest_destination, source_length,
            static_cast<unsigned>(evidence.reference_ok),
            static_cast<unsigned>(evidence.guest_window_ok),
            static_cast<unsigned>(evidence.exact_bytes),
            evidence.mismatched_bytes, evidence.first_mismatch,
            static_cast<unsigned>(file22_copy_verified),
            static_cast<unsigned>(file22_post_verified),
            static_cast<unsigned>(valid),
            static_cast<unsigned>(valid ? current.outer_state : 0));
    }
}

void observe_file22_translated_entry(const std::uint8_t* rdram,
                                     std::uint32_t original_rom_function,
                                     std::uint32_t guest_function_entry) {
    if (!enabled.load(std::memory_order_acquire)) return;
    // Reject accidentally instrumented functions outside the independently
    // authenticated original-USA file-22 code segment.
    if (original_rom_function < usa_file22_reference::kFile22Rom ||
        original_rom_function >= usa_file22_reference::kFile22Rom
                               + usa_file22_reference::kFile22CodeSize ||
        guest_function_entry != usa_file22_reference::kFile22Vram
                                + original_rom_function - usa_file22_reference::kFile22Rom) return;
    Snapshot current;
    const bool valid = decode_usa_snapshot(rdram, current);
    std::lock_guard lock(trace_lock);
    if (!enabled.load(std::memory_order_relaxed) || emitted_events >= kMaxEvents) return;
    for (std::size_t i = 0; i < file22_seen_count; ++i) {
        if (file22_seen_rom[i] == original_rom_function) return;
    }
    if (file22_seen_count < file22_seen_rom.size()) {
        file22_seen_rom[file22_seen_count++] = original_rom_function;
    }
    ++emitted_events;
    std::fprintf(stderr,
        "[Goemon USA native state trace] type=file22_translated_function_entry "
        "elapsed_us=%llu original_rom_function=0x%08X "
        "guest_function_entry=0x%08X translated_entry_reached=1 "
        "file22_slot_active=%u pre_relocation_match=%u post_relocation_match=%u "
        "usa_sys_valid=%u outer=%u guest_pc=unavailable guest_frame=unavailable\n",
        static_cast<unsigned long long>(elapsed_us()),
        original_rom_function, guest_function_entry,
        static_cast<unsigned>(file22_slot_active),
        static_cast<unsigned>(file22_copy_verified),
        static_cast<unsigned>(file22_post_verified),
        static_cast<unsigned>(valid),
        static_cast<unsigned>(valid ? current.outer_state : 0));
}

} // namespace goemon64::usa_native_trace
