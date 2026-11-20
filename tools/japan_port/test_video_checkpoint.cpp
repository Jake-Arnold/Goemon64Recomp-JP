#include <atomic>
#include <cassert>
#include <chrono>
#include <cstring>
#include <thread>

#include "jp_external_message_queue.hpp"
#include "jp_video_checkpoint.hpp"
#include "ultramodern/ultramodern.hpp"

extern std::atomic_bool exited;
moodycamel::LightweightSemaphore graphics_shutdown_ready;

namespace ultramodern {
bool jp_set_guest_paused(bool pause);
bool jp_drain_action_queue_after_pc_exit(bool all_guests_joined);
bool jp_rearm_events_after_verified_exit(std::uint64_t generation);
bool jp_flush_external_messages_after_join(std::uint64_t generation);
bool jp_rearm_external_messages_after_verified_exit(std::uint64_t generation);
}

namespace {

using namespace std::chrono_literals;
using namespace ultramodern::renderer;
std::atomic_bool game_started{true};
std::atomic_uint frame_count{0};
std::atomic_uint first_origin{0};
std::atomic_uint last_origin{0};
std::atomic_uint last_width{0};

class TestRenderer final : public RendererContext {
public:
    TestRenderer() {
        setup_result = SetupResult::Success;
        chosen_api = GraphicsApi{};
    }

    bool valid() override { return true; }
    bool update_config(const GraphicsConfig&, const GraphicsConfig&) override { return true; }
    void enable_instant_present() override {}
    void send_dl(const OSTask*) override { std::abort(); }
    void shutdown() override {}
    uint32_t get_display_framerate() const override { return 60; }
    float get_resolution_scale() const override { return 1.0f; }

    void update_screen() override {
        const auto& regs = *get_vi_regs();
        if (frame_count.load() == 0) {
            first_origin.store(regs.VI_ORIGIN_REG);
        }
        last_origin.store(regs.VI_ORIGIN_REG);
        last_width.store(regs.VI_WIDTH_REG);
        frame_count.fetch_add(1);
    }
};

void wait_for(auto predicate) {
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while (!predicate() && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(1ms);
    }
    assert(predicate());
}

goemon_jp::savestate::VideoCheckpoint capture_video() {
    assert(ultramodern::jp_set_guest_paused(true));
    std::optional<goemon_jp::savestate::VideoCheckpoint> saved;
    wait_for([&] {
        saved = ultramodern::jp_snapshot_video();
        return saved.has_value();
    });
    return *saved;
}

void stop_video() {
    exited.store(true);
    assert(ultramodern::jp_set_guest_paused(false));
    graphics_shutdown_ready.signal();
    ultramodern::join_event_threads();
    assert(ultramodern::jp_drain_action_queue_after_pc_exit(true));
}

} // namespace

namespace ultramodern {
bool is_game_started() { return game_started.load(); }
void measure_input_latency() {}

namespace extensions {
void on_displaylist_submitted(int32_t) {}
void on_displaylist_parsed(int32_t) {}
void on_displaylist_completed(int32_t) {}
}

namespace error_handling {
void message_box(const char*) { std::abort(); }
void quick_exit(const char*, int, const char*, int) { std::abort(); }
}

namespace rsp {
void init() {}
bool run_task(uint8_t*, const OSTask*) { std::abort(); }
}

namespace renderer {
const GraphicsConfig& get_graphics_config() {
    static const GraphicsConfig config{};
    return config;
}
std::unique_ptr<RendererContext> create_render_context(uint8_t*, WindowHandle, bool) {
    return std::make_unique<TestRenderer>();
}
std::string get_graphics_api_name(GraphicsApi) { return "test"; }
}
}

extern "C" uint32_t osVirtualToPhysical(int32_t address) {
    return static_cast<uint32_t>(address) & 0x1FFFFFFF;
}

void test_video_checkpoint() {
    using goemon_jp::savestate::VideoModeSource;
    constexpr int32_t mode_address = static_cast<int32_t>(0x80000100u);
    constexpr int32_t queue_address = static_cast<int32_t>(0x80000200u);
    constexpr int32_t framebuffer = static_cast<int32_t>(0x80000400u);
    constexpr int32_t next_buffer = static_cast<int32_t>(0x80000500u);
    alignas(OSViMode) uint8_t original[0x1000]{};
    alignas(OSViMode) uint8_t restored[0x1000]{};

    assert(ultramodern::jp_flush_external_messages_after_join(2));
    assert(ultramodern::jp_rearm_external_messages_after_verified_exit(2));
    exited.store(false);
    ultramodern::init_events(original, nullptr);
    assert(!ultramodern::jp_snapshot_video());
    OSViMode mode{};
    mode.comRegs.width = 320;
    mode.fldRegs[0].origin = 0x10;
    mode.fldRegs[1].origin = 0x10;
    std::memcpy(original + 0x100, &mode, sizeof(mode));
    osViSetMode(original, mode_address);
    osViSwapBuffer(original, framebuffer);
    osViSetEvent(original, queue_address, 57, 3);
    osSetEventMesg(original, OS_EVENT_SP, queue_address, 58);
    osSetEventMesg(original, OS_EVENT_AI, queue_address, 59);
    wait_for([] { return last_origin.load() == 0x410 && last_width.load() == 320; });

    (void)capture_video();
    osViSwapBuffer(original, next_buffer);
    const auto checkpoint = *ultramodern::jp_snapshot_video();
    assert(checkpoint.buffers[checkpoint.current_buffer].framebuffer == framebuffer);
    assert(checkpoint.buffers[checkpoint.current_buffer ^ 1].framebuffer == next_buffer);
    assert(checkpoint.buffers[checkpoint.current_buffer].mode_source == VideoModeSource::Guest);
    assert(checkpoint.buffers[checkpoint.current_buffer].mode_address == mode_address);
    assert(checkpoint.presented_registers.VI_ORIGIN_REG == 0x410);
    assert(checkpoint.sp.queue == queue_address && checkpoint.sp.message == 58);
    assert(checkpoint.ai.queue == queue_address && checkpoint.ai.message == 59);
    assert(!ultramodern::jp_prepare_video_restore(checkpoint, 1, restored));
    std::memcpy(restored, original, sizeof(restored));
    stop_video();
    assert(ultramodern::jp_rearm_events_after_verified_exit(1));
    assert(ultramodern::jp_flush_external_messages_after_join(3));
    assert(ultramodern::jp_rearm_external_messages_after_verified_exit(3));
    auto invalid = checkpoint;
    invalid.buffers[0].mode_address = static_cast<int32_t>(0x80001000u);
    assert(!ultramodern::jp_prepare_video_restore(invalid, 2, restored));
    assert(ultramodern::jp_prepare_video_restore(checkpoint, 2, restored));
    assert(!ultramodern::jp_prepare_video_restore(checkpoint, 2, restored));

    // Make stale pointers observable, and verify the mode takes effect
    // from restored guest memory once the new VI worker starts advancing.
    std::memset(original, 0, sizeof(original));
    mode.comRegs.width = 336;
    std::memcpy(restored + 0x100, &mode, sizeof(mode));
    game_started.store(false);
    frame_count.store(0);
    exited.store(false);
    ultramodern::init_events(restored, nullptr);
    wait_for([] { return frame_count.load() == 1; });
    assert(first_origin.load() == checkpoint.presented_registers.VI_ORIGIN_REG);
    assert(osViGetCurrentFramebuffer() == framebuffer && osViGetNextFramebuffer() == next_buffer);
    auto cold = capture_video();
    assert(cold.remaining_retraces == checkpoint.remaining_retraces);
    assert(cold.total_retraces == checkpoint.total_retraces);
    assert(ultramodern::jp_set_guest_paused(false));
    game_started.store(true);
    wait_for([] { return last_width.load() == 336 && last_origin.load() == 0x510; });
    wait_for([] {
        const auto pending = ultramodern::jp_snapshot_external_messages();
        if (!pending) return false;
        bool vi = false;
        bool ai = false;
        for (const auto& message : *pending) {
            if (message.mq == queue_address && message.mesg == 57) vi = true;
            if (message.mq == queue_address && message.mesg == 59) ai = true;
        }
        return vi && ai;
    });
    (void)capture_video();
    stop_video();
}
