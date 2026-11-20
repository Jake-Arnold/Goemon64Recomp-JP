#ifndef GOEMON_JP_GUEST_CHECKPOINT_STATE_H
#define GOEMON_JP_GUEST_CHECKPOINT_STATE_H

#include <stdint.h>

enum { GOEMON_JP_CHECKPOINT_MAX_FRAMES = 32 };

typedef struct {
    uint32_t function_index;
    uint32_t call_site;
    uint64_t local_hi, local_lo, local_result;
    int local_c1cs;
} goemon_jp_guest_frame;

enum {
    GOEMON_JP_MESSAGE_NONE,
    GOEMON_JP_MESSAGE_SEND,
    GOEMON_JP_MESSAGE_JAM,
    GOEMON_JP_MESSAGE_RECEIVE,
    GOEMON_JP_MESSAGE_RETURN,
};

typedef struct {
    uint8_t call;
    int32_t queue;
    int32_t argument;
} goemon_jp_saved_message_call;

// In-memory state for the instrumented call chain in one probe build.
typedef struct {
    uint64_t probe_revision;
    uint32_t frame_count;
    goemon_jp_guest_frame frames[GOEMON_JP_CHECKPOINT_MAX_FRAMES];
    uint64_t registers[32];
    uint64_t float_registers[32];
    uint64_t context_hi, context_lo;
    uint32_t status_reg;
    uint8_t mips3_float_mode;
    uint32_t rounding_mode;
    goemon_jp_saved_message_call message;
} goemon_jp_guest_checkpoint;

#endif
