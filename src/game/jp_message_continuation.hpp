#pragma once

#include <cstdint>
#include <optional>

#include "jp_guest_checkpoint_state.h"

namespace goemon_jp::savestate {

enum class MessageCall : std::uint8_t {
    Send = GOEMON_JP_MESSAGE_SEND,
    Jam = GOEMON_JP_MESSAGE_JAM,
    Receive = GOEMON_JP_MESSAGE_RECEIVE,
    Return = GOEMON_JP_MESSAGE_RETURN,
    IdleWait = GOEMON_JP_MESSAGE_IDLE_WAIT,
};

// Queue and output addresses use guest virtual addresses. For a send or jam,
// argument is the message value. For Return, it is the completed OS result.
struct MessageContinuation {
    MessageCall call;
    std::int32_t queue;
    std::int32_t argument;
};

} // namespace goemon_jp::savestate

namespace ultramodern {

[[noreturn]] void jp_idle_message_loop(std::uint8_t* rdram);

void jp_set_message_continuation(
    std::uint8_t* rdram, std::optional<goemon_jp::savestate::MessageContinuation> continuation);

// Inspect only with the corresponding RDRAM generation pinned.
std::optional<goemon_jp::savestate::MessageContinuation> jp_get_message_continuation(
    std::uint8_t* rdram, std::int32_t guest_thread);

// Dispatch on the reconstructed guest thread once the scheduler selects and
// detaches it from its queue. The supplied record belongs to restored RDRAM.
bool jp_resume_message_call(std::uint8_t* rdram,
    const goemon_jp::savestate::MessageContinuation& state, std::int32_t& result);

// The calling thread owns this CPU state until it clears the binding.
void jp_set_message_cpu_checkpoint(
    std::uint8_t* rdram, const goemon_jp_guest_checkpoint* cpu);

// Copies the matching CPU frames and message phase together while a guest is
// parked. The caller must pin the RDRAM generation and establish quiescence.
std::optional<goemon_jp_guest_checkpoint> jp_get_message_checkpoint(
    std::uint8_t* rdram, std::int32_t guest_thread);

} // namespace ultramodern
