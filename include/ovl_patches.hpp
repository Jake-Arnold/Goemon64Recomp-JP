#ifndef __OVL_PATCHES_HPP__
#define __OVL_PATCHES_HPP__

namespace goemon64 {
    void register_overlays();
    void register_patches();
#ifdef GOEMON_UNIFIED_REGIONS
    void register_usa_overlays();
    void register_usa_patches();
#endif
}

#endif
