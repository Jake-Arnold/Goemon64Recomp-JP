#include "../../src/game/usa_native_state_trace.hpp"

#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <memory>

using namespace goemon64::usa_native_trace;

static void guest_write_u8(std::uint8_t* ram, std::uint32_t addr, std::uint8_t value) {
    ram[(addr & 0x7FFFFFu) ^ 3u] = value;
}
static void guest_write_u16(std::uint8_t* ram, std::uint32_t addr, std::uint16_t value) {
    guest_write_u8(ram, addr, static_cast<std::uint8_t>(value >> 8));
    guest_write_u8(ram, addr + 1, static_cast<std::uint8_t>(value));
}
static void guest_write_u32(std::uint8_t* ram, std::uint32_t addr, std::uint32_t value) {
    guest_write_u8(ram, addr, static_cast<std::uint8_t>(value >> 24));
    guest_write_u8(ram, addr + 1, static_cast<std::uint8_t>(value >> 16));
    guest_write_u8(ram, addr + 2, static_cast<std::uint8_t>(value >> 8));
    guest_write_u8(ram, addr + 3, static_cast<std::uint8_t>(value));
}

int main() {
    // Mock-RDRAM only. This is not a native USA recomp gameplay test.
    auto ram = std::make_unique<std::uint8_t[]>(0x800000);
    Snapshot result{99, 99, 99, 999, 999};
    assert(!decode_usa_snapshot(nullptr, result));
    assert(!decode_usa_snapshot(ram.get(), result));
    assert(result.outer_state == 99); // failure leaves data untouched

    guest_write_u32(ram.get(), 0x8015C5C8, 0x8008CCC0);
    guest_write_u8(ram.get(), 0x800C7A94, 13);
    guest_write_u8(ram.get(), 0x800C7A96, 0);
    guest_write_u8(ram.get(), 0x800C7A97, 0);
    guest_write_u16(ram.get(), 0x800C7AB2, 465);
    guest_write_u16(ram.get(), 0x800C7AB4, 65);
    assert(decode_usa_snapshot(ram.get(), result));
    assert(result.outer_state == 13);
    assert(result.room_id == 465);
    assert(result.room_group_selection == 65);
    // Verify unaligned, separate halves on little-endian host layout.
    guest_write_u16(ram.get(), 0x800C7AB2, 351);
    guest_write_u16(ram.get(), 0x800C7AB4, 1);
    assert(decode_usa_snapshot(ram.get(), result));
    assert(result.room_id == 351 && result.room_group_selection == 1);

    // 18-way bound: reject invalid outer dispatcher values.
    guest_write_u8(ram.get(), 0x800C7A94, 18);
    assert(!decode_usa_snapshot(ram.get(), result));
    guest_write_u8(ram.get(), 0x800C7A94, 0);
    guest_write_u16(ram.get(), 0x800C7AB2, 0); // boot default is a field value
    assert(decode_usa_snapshot(ram.get(), result) && result.room_id == 0);

    // Gate off for Japan even when explicitly requested.
    setenv("GOEMON_USA_NATIVE_STATE_TRACE", "1", 1);
    select_region(true);
    observe_guest_step_callback(ram.get());
    observe_overlay_registration_request(ram.get(), 0x69CBD0, 0x801CB460, 0x100);
    observe_overlay_loader_postprocess(ram.get(), 22, 0x69CBD0, 0x801CB460, 0x100);

    // Gate off by default for USA and for any non-exact opt-in string.
    unsetenv("GOEMON_USA_NATIVE_STATE_TRACE");
    select_region(false);
    observe_guest_step_callback(ram.get());
    setenv("GOEMON_USA_NATIVE_STATE_TRACE", "0", 1);
    select_region(false);
    observe_guest_step_callback(ram.get());

    // USA opt-in emits only numeric metadata and never modifies guest memory.
    setenv("GOEMON_USA_NATIVE_STATE_TRACE", "1", 1);
    select_region(false);
    const auto before = ram[0x15C5C8];
    observe_guest_step_callback(ram.get());
    observe_guest_step_callback(ram.get()); // unchanged sample should be silent
    observe_overlay_registration_request(ram.get(), 0x69CBD0, 0x801CB460, 0x100);
    observe_overlay_loader_postprocess(ram.get(), 22, 0x69CBD0, 0x801CB460, 0x100);
    assert(ram[0x15C5C8] == before);
}
