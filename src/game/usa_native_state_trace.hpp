#pragma once

#include <cstdint>

// Guest-thread-only instrumentation for the original USA region in the
// dual-region native recomp. All observations are read-only. No guest PC,
// completed overlay load, or frame number is inferred by this interface.
namespace goemon64::usa_native_trace {

struct Snapshot {
    std::uint8_t outer_state = 0;
    std::uint8_t secondary_state = 0;
    std::uint8_t tertiary_state = 0;
    std::uint16_t room_id = 0;
    std::uint16_t room_group_selection = 0;

    bool operator==(const Snapshot&) const = default;
};

// `rdram` is the native N64Recomp word-swapped guest mapping. This function
// only reads fixed USA symbols after validating its system-root pointer.
// `false` means uninitialised/other ROM/unsupported mapping.
bool decode_usa_snapshot(const std::uint8_t* rdram, Snapshot& out);

// Called before the selected guest starts, from the unified launcher thread.
// Requires exact opt-in GOEMON_USA_NATIVE_STATE_TRACE=1 AND USA selection.
void select_region(bool japan);

// Called only where the native recomp already supplies the guest RDRAM pointer.
// These are callback observations, not necessarily per-frame observations.
void observe_guest_step_callback(const std::uint8_t* rdram);

// Called before `load_overlays`. Reports a requested host-side overlay
// registration, NOT completion of ROM copy/decompress/relocation.
void observe_overlay_registration_request(const std::uint8_t* rdram,
                                          std::uint32_t rom_source,
                                          std::uint32_t guest_destination,
                                          std::uint32_t source_length);

// Synchronously emitted at the tail of patched USA func_80001C00_2800,
// after ROM copy/decompression, relocation call, state restore and zero-fill.
// File ID comes directly from the guest loader. Reaching this point does NOT
// verify copied/relocated bytes or prove execution of the loaded overlay.
void observe_overlay_loader_postprocess(const std::uint8_t* rdram,
                                        std::uint32_t file_id,
                                        std::uint32_t rom_source,
                                        std::uint32_t guest_destination,
                                        std::uint32_t source_length);

} // namespace goemon64::usa_native_trace
