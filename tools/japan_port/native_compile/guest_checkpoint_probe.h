#ifndef GOEMON_JP_GUEST_CHECKPOINT_PROBE_H
#define GOEMON_JP_GUEST_CHECKPOINT_PROBE_H

#include "recomp.h"
#include "jp_guest_checkpoint_state.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

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

// Used by probes generated with --message-continuations.
void goemon_jp_probe_message_call(uint8_t* rdram, recomp_context* ctx,
    const goemon_jp_guest_checkpoint* checkpoint, recomp_func_t* call);
bool goemon_jp_probe_resume_message_call(uint8_t* rdram, goemon_jp_guest_checkpoint* checkpoint);

#ifdef __cplusplus
}
#endif

#endif
