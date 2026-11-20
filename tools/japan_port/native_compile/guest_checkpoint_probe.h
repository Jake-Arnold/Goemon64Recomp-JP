#ifndef GOEMON_JP_GUEST_CHECKPOINT_PROBE_H
#define GOEMON_JP_GUEST_CHECKPOINT_PROBE_H

#include "recomp.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { GOEMON_JP_CHECKPOINT_MAX_FRAMES = 32 };

typedef struct {
    uint32_t function_index;
    uint32_t call_site;
    uint64_t local_hi, local_lo, local_result;
    int local_c1cs;
} goemon_jp_guest_frame;

// In-memory state for the instrumented call chain in one probe build.
typedef struct {
    uint64_t probe_revision;
    uint32_t frame_count;
    goemon_jp_guest_frame frames[GOEMON_JP_CHECKPOINT_MAX_FRAMES];
    gpr registers[32];
    uint64_t float_registers[32];
    uint64_t context_hi, context_lo;
    uint32_t status_reg;
    uint8_t mips3_float_mode;
    uint32_t rounding_mode;
} goemon_jp_guest_checkpoint;

typedef bool goemon_jp_checkpoint_callback(const goemon_jp_guest_checkpoint*, void*);

typedef enum {
    GOEMON_JP_CHECKPOINT_FINISHED,
    GOEMON_JP_CHECKPOINT_STOPPED,
    GOEMON_JP_CHECKPOINT_INVALID,
} goemon_jp_checkpoint_result;

// Pass NULL to start at function entry. The callback may copy the checkpoint
// and return true to stop after the call. The caller owns guest RAM.
goemon_jp_checkpoint_result goemon_jp_guest_checkpoint_probe(
    uint8_t* rdram, recomp_context* ctx, const goemon_jp_guest_checkpoint* resume,
    goemon_jp_checkpoint_callback* callback, void* user);

#ifdef __cplusplus
}
#endif

#endif
