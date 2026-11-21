#ifndef PATCH_API_FUNCS_H
#define PATCH_API_FUNCS_H

#include "patch_helpers.h"

DECLARE_FUNC(void, patch_api_load_overlays, u32 rom_addr, void *vram_addr, u32 size);
// Host trace callback after the original-USA patched file-loader postprocessing.
DECLARE_FUNC(void, recomp_file_loader_postprocess, u32 file_id, u32 rom_addr, void *vram_addr, u32 source_length);
DECLARE_FUNC(void, patch_api_unload_overlays, void *vram_addr, u32 size);
DECLARE_FUNC(float, patch_api_get_aspect_ratio, float original);
DECLARE_FUNC(void, osMapTLB_recomp, s32 index, u32 pm, void *vaddr, u32 evenpaddr, u32 oddpaddr, s32 asid);

#endif // PATCH_API_FUNCS_H
