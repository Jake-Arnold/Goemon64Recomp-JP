#include "guest_checkpoint_probe.h"
#include "jp_message_continuation.hpp"

namespace {

class MessageCpuScope {
    uint8_t* rdram;

public:
    MessageCpuScope(uint8_t* rdram, const goemon_jp_guest_checkpoint* cpu) : rdram(rdram) {
        ultramodern::jp_set_message_cpu_checkpoint(rdram, cpu);
    }
    MessageCpuScope(const MessageCpuScope&) = delete;
    MessageCpuScope& operator=(const MessageCpuScope&) = delete;

    ~MessageCpuScope() {
        ultramodern::jp_set_message_cpu_checkpoint(rdram, nullptr);
    }
};

} // namespace

void goemon_jp_probe_message_call(uint8_t* rdram, recomp_context* ctx,
        const goemon_jp_guest_checkpoint* checkpoint, recomp_func_t* call) {
    const MessageCpuScope scope(rdram, checkpoint);
    call(rdram, ctx);
}

void goemon_jp_probe_idle_wait(uint8_t* rdram, const goemon_jp_guest_checkpoint* checkpoint) {
    const MessageCpuScope scope(rdram, checkpoint);
    ultramodern::jp_idle_message_loop(rdram);
}

bool goemon_jp_probe_resume_message_call(
        uint8_t* rdram, goemon_jp_guest_checkpoint* checkpoint) {
    using goemon_jp::savestate::MessageCall;

    const auto message = checkpoint->message;
    const goemon_jp::savestate::MessageContinuation state{
        static_cast<MessageCall>(message.call), message.queue, message.argument};
    std::int32_t result;
    {
        const MessageCpuScope scope(rdram, checkpoint);
        if (!ultramodern::jp_resume_message_call(rdram, state, result)) {
            return false;
        }
    }
    checkpoint->registers[2] = static_cast<std::int64_t>(result);
    checkpoint->message = {};
    return true;
}
