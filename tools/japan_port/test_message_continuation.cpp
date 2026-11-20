#include <atomic>
#include <cassert>
#include <chrono>
#include <cstring>
#include <thread>

#include "guest_checkpoint_probe.h"
#include "jp_message_continuation.hpp"
#include "jp_guest_checkpoint_status.hpp"
#include "ultramodern/ultramodern.hpp"

namespace ultramodern {
void guest_shutdown_checkpoint();
void request_guest_shutdown_for_future_reset();
bool wait_for_guest_joins_for_future_reset(std::uint32_t timeout_ms);
bool jp_rearm_guest_after_verified_exit();
}

std::atomic_bool exited{false};
extern "C" void entry(uint8_t* rdram, recomp_context* ctx);

namespace {

using goemon_jp::savestate::MessageCall;
using goemon_jp::savestate::MessageContinuation;
using namespace std::chrono_literals;

constexpr std::int32_t guest_address(std::uint32_t offset) {
    return static_cast<std::int32_t>(0x80000000u + offset);
}

constexpr auto driver_thread = guest_address(0x100);
constexpr auto original_thread = guest_address(0x300);
constexpr auto restored_thread = guest_address(0x500);
constexpr auto queue = guest_address(0x800);
constexpr auto messages = guest_address(0x900);
constexpr auto received_message = guest_address(0xA00);

constexpr auto marker = guest_address(0xB00);
constexpr std::size_t ram_size = 0x1000;

enum Entry : std::uint64_t {
    Driver, Receive, Restore, InspectSender, Jam, ProbeReceive, RestoreProbe, ProbeDriver, InspectProbeSender,
};

std::atomic_bool finished{false};
MessageContinuation waiting_call{};
MessageContinuation completed_call{};
goemon_jp_guest_checkpoint saved_probe{};
goemon_jp_guest_checkpoint completed_probe{};
alignas(OSThread) uint8_t saved_ram[ram_size]{};
unsigned probe_receives = 0;
unsigned probe_sends = 0;


void start_thread(uint8_t* rdram, std::int32_t address, Entry entry, OSPri priority) {
    const auto stack_top = guest_address(0xF00);
    osCreateThread(rdram, address, 1, static_cast<std::int32_t>(entry), 0, stack_top, priority);
    const auto initial = ultramodern::jp_get_unstarted_guest_thread(address);
    assert(initial && initial->address == address && initial->entrypoint == static_cast<int32_t>(entry));
    assert(initial->stack_pointer == stack_top - 0x10 && initial->argument == 0);
    assert(ultramodern::jp_guest_checkpoint_coverage().not_started == 1);
    osStartThread(rdram, address);
}

void wait_for_shutdown() {
    finished.store(true);
    for (;;) {
        ultramodern::guest_shutdown_checkpoint();
        std::this_thread::sleep_for(1ms);
    }
}

void start_probe(uint8_t* rdram, bool send) {
    recomp_context cpu{};
    cpu.r4 = static_cast<std::int64_t>(queue);
    cpu.r5 = send ? 62 : static_cast<std::int64_t>(received_message);
    cpu.r6 = OS_MESG_BLOCK;
    cpu.r7 = send;
    cpu.r8 = static_cast<std::int64_t>(marker);
    entry(rdram, &cpu);
    assert(cpu.r2 == 0 && cpu.r16 == 20 && cpu.r17 == 7);
}

void restore_probe(uint8_t* rdram, const goemon_jp_guest_checkpoint& checkpoint) {
    recomp_context cpu{};
    assert(goemon_jp_guest_checkpoint_probe(rdram, &cpu, &checkpoint, nullptr, nullptr) ==
        GOEMON_JP_CHECKPOINT_FINISHED);
    assert(cpu.r2 == 0 && cpu.r16 == 20 && cpu.r17 == 7);
}

void exercise_scheduler(uint8_t* rdram) {
    osCreateMesgQueue(rdram, queue, messages, 2);
    start_thread(rdram, original_thread, Receive, 10);
    const auto receive = ultramodern::jp_get_message_continuation(rdram, original_thread);
    assert(receive && receive->call == MessageCall::Receive);
    assert(receive->queue == queue && receive->argument == received_message);
    assert(TO_PTR(OSMesgQueue, queue)->validCount == 0);
    osDestroyThread(rdram, original_thread);
    assert(!ultramodern::jp_get_message_continuation(rdram, original_thread));

    waiting_call = *receive;
    start_thread(rdram, restored_thread, Restore, 10);
    const auto reblocked = ultramodern::jp_get_message_continuation(rdram, restored_thread);
    assert(reblocked && reblocked->call == MessageCall::Receive);
    assert(osSendMesg(rdram, queue, 41, OS_MESG_NOBLOCK) == 0);
    assert(*TO_PTR(OSMesg, received_message) == 41);
    assert(!ultramodern::jp_get_message_continuation(rdram, restored_thread));
    assert(TO_PTR(OSMesgQueue, queue)->validCount == 0);

    start_thread(rdram, original_thread, InspectSender, 10);
    assert(osSendMesg(rdram, queue, 42, OS_MESG_NOBLOCK) == 0);
    assert(*TO_PTR(OSMesg, received_message) == 42);
    assert(!ultramodern::jp_get_message_continuation(rdram, driver_thread));
    std::int32_t result = -1;
    assert(ultramodern::jp_resume_message_call(rdram, completed_call, result) && result == 0);
    assert(TO_PTR(OSMesgQueue, queue)->validCount == 0);

    assert(osSendMesg(rdram, queue, 51, OS_MESG_NOBLOCK) == 0);
    assert(osSendMesg(rdram, queue, 52, OS_MESG_NOBLOCK) == 0);
    start_thread(rdram, original_thread, Jam, 10);
    const auto jam = ultramodern::jp_get_message_continuation(rdram, original_thread);
    assert(jam && jam->call == MessageCall::Jam && jam->queue == queue && jam->argument == 53);
    osDestroyThread(rdram, original_thread);
    assert(osRecvMesg(rdram, queue, received_message, OS_MESG_NOBLOCK) == 0);
    assert(*TO_PTR(OSMesg, received_message) == 51);

    waiting_call = *jam;
    start_thread(rdram, restored_thread, Restore, 10);
    assert(osRecvMesg(rdram, queue, received_message, OS_MESG_NOBLOCK) == 0);
    assert(*TO_PTR(OSMesg, received_message) == 53);
    assert(osRecvMesg(rdram, queue, received_message, OS_MESG_NOBLOCK) == 0);
    assert(*TO_PTR(OSMesg, received_message) == 52);
    assert(osRecvMesg(rdram, queue, received_message, OS_MESG_NOBLOCK) == -1);

    start_thread(rdram, original_thread, ProbeReceive, 10);
    const auto probe = ultramodern::jp_get_message_checkpoint(rdram, original_thread);
    assert(probe && probe->frame_count == 2);
    const auto coverage = ultramodern::jp_guest_checkpoint_coverage();
    assert(coverage.threads == 2 && coverage.not_started == 0 && coverage.message_checkpoints == 1);
    assert(!ultramodern::jp_get_unstarted_guest_thread(original_thread));
    assert(probe->message.call == GOEMON_JP_MESSAGE_RECEIVE && probe_receives == 1);
    assert(*TO_PTR(int32_t, marker) == 1);
    saved_probe = *probe;
    // The driver is the only running guest. There are no native RDRAM
    // writers in this fixture, and the queue receiver is parked.
    std::memcpy(saved_ram, rdram, ram_size);
    wait_for_shutdown();
}

void exercise_restored_scheduler(uint8_t* rdram) {
    assert(*TO_PTR(int32_t, marker) == 1);
    auto blocked = GET_MEMBER(OSMesgQueue, queue, blocked_on_recv);
    assert(ultramodern::thread_queue_pop(rdram, blocked) == original_thread);
    start_thread(rdram, original_thread, RestoreProbe, 10);
    const auto waiting = ultramodern::jp_get_message_checkpoint(rdram, original_thread);
    assert(waiting && waiting->message.call == GOEMON_JP_MESSAGE_RECEIVE);
    assert(waiting->probe_revision == saved_probe.probe_revision && waiting->frame_count == 2);
    assert(osSendMesg(rdram, queue, 61, OS_MESG_NOBLOCK) == 0);
    assert(*TO_PTR(OSMesg, received_message) == 61);
    assert(*TO_PTR(int32_t, marker) == 1 && probe_receives == 1);

    start_thread(rdram, original_thread, InspectProbeSender, 10);
    start_probe(rdram, true);
    assert(*TO_PTR(OSMesg, received_message) == 62);
    assert(completed_probe.frame_count == 2 && completed_probe.message.call == GOEMON_JP_MESSAGE_RETURN);
    assert(!ultramodern::jp_get_message_checkpoint(rdram, driver_thread));
    restore_probe(rdram, completed_probe);
    assert(*TO_PTR(int32_t, marker) == 2 && probe_sends == 1);
    assert(TO_PTR(OSMesgQueue, queue)->validCount == 0);
    wait_for_shutdown();
}

} // namespace

extern "C" void osRecvMesg_recomp(uint8_t* rdram, recomp_context* ctx) {
    ++probe_receives;
    ctx->r2 = osRecvMesg(rdram, static_cast<int32_t>(ctx->r4),
                         static_cast<int32_t>(ctx->r5), static_cast<int32_t>(ctx->r6));
}

extern "C" void osSendMesg_recomp(uint8_t* rdram, recomp_context* ctx) {
    ++probe_sends;
    ctx->r2 = osSendMesg(rdram, static_cast<int32_t>(ctx->r4),
                         static_cast<int32_t>(ctx->r5), static_cast<int32_t>(ctx->r6));
}

void run_thread_function(uint8_t* rdram, std::uint64_t entry, std::uint64_t, std::uint64_t) {
    switch (entry) {
        case Driver:
            exercise_scheduler(rdram);
            break;
        case Receive:
            (void)osRecvMesg(rdram, queue, received_message, OS_MESG_BLOCK);
            break;
        case Restore: {
            std::int32_t result = -1;
            assert(ultramodern::jp_resume_message_call(rdram, waiting_call, result) && result == 0);
            break;
        }
        case InspectSender: {
            assert(osRecvMesg(rdram, queue, received_message, OS_MESG_BLOCK) == 0);
            const auto completed = ultramodern::jp_get_message_continuation(rdram, driver_thread);
            assert(completed && completed->call == MessageCall::Return && completed->argument == 0);
            completed_call = *completed;
            break;
        }
        case Jam:
            (void)osJamMesg(rdram, queue, 53, OS_MESG_BLOCK);
            break;
        case ProbeReceive:
            start_probe(rdram, false);
            break;
        case RestoreProbe: {
            goemon_jp_guest_checkpoint invalid = saved_probe;
            ++invalid.message.queue;
            recomp_context cpu{};
            assert(goemon_jp_guest_checkpoint_probe(rdram, &cpu, &invalid, nullptr, nullptr) ==
                GOEMON_JP_CHECKPOINT_INVALID);
            assert(cpu.r4 == 0);
            restore_probe(rdram, saved_probe);
            break;
        }
        case ProbeDriver:
            exercise_restored_scheduler(rdram);
            break;
        case InspectProbeSender: {
            assert(osRecvMesg(rdram, queue, received_message, OS_MESG_BLOCK) == 0);
            const auto checkpoint = ultramodern::jp_get_message_checkpoint(rdram, driver_thread);
            assert(checkpoint && checkpoint->message.call == GOEMON_JP_MESSAGE_RETURN);
            completed_probe = *checkpoint;
            break;
        }
    }
}

static void run_generation(uint8_t* rdram, Entry entry) {
    finished.store(false);
    exited.store(false);
    ultramodern::init_thread_cleanup();
    start_thread(rdram, driver_thread, entry, 1);
    const auto deadline = std::chrono::steady_clock::now() + 5s;
    while (!finished.load() && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(1ms);
    }
    assert(finished.load());
    ultramodern::request_guest_shutdown_for_future_reset();
    assert(ultramodern::wait_for_guest_joins_for_future_reset(2000));
    exited.store(true);
    ultramodern::join_thread_cleaner_thread();
}

int main() {
    alignas(OSThread) uint8_t original_ram[ram_size]{};
    run_generation(original_ram, Driver);
    assert(ultramodern::jp_rearm_guest_after_verified_exit());

    std::memset(original_ram, 0, ram_size);
    alignas(OSThread) uint8_t restored_ram[ram_size]{};
    std::memcpy(restored_ram, saved_ram, ram_size);
    recomp_context unbound_cpu{};
    assert(goemon_jp_guest_checkpoint_probe(restored_ram, &unbound_cpu, &saved_probe, nullptr, nullptr) ==
        GOEMON_JP_CHECKPOINT_INVALID);
    assert(unbound_cpu.r4 == 0 && std::memcmp(restored_ram, saved_ram, ram_size) == 0);
    run_generation(restored_ram, ProbeDriver);
}
