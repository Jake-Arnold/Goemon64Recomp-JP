#include "ovl_patches.hpp"
#ifdef GOEMON_UNIFIED_REGIONS
#include "goemon_usa_symbols.h"
#endif
#include "../../RecompiledPatches/patches_bin.h"
#include "../../RecompiledPatches/recomp_overlays.inl"

#include "librecomp/overlays.hpp"
#include "librecomp/game.hpp"

#ifdef GOEMON_UNIFIED_REGIONS
void goemon64::register_usa_patches() {
#else
void goemon64::register_patches() {
#endif
    recomp::overlays::register_patches(mm_patches_bin, sizeof(mm_patches_bin), section_table, ARRLEN(section_table));
    recomp::overlays::register_base_exports(export_table);
    recomp::overlays::register_base_events(event_names);
    recomp::overlays::register_manual_patch_symbols(manual_patch_symbols);
}
