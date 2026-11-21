#!/usr/bin/env python3
from analyze_file22_evidence import parse, summarize
PREFIX = "[Goemon USA native state trace] "
BASE = ("source_rom=0x0069E080 guest_destination=0x801CB460 "
        "source_length=47264 reference_ok=1 guest_window_ok=1 "
        "exact_bytes=1 mismatches=0")

def main():
    events = parse("\n".join([
        PREFIX + "type=file22_image_attestation stage=before_relocation file_id=22 " + BASE,
        PREFIX + "type=file22_image_attestation stage=after_relocation file_id=22 " + BASE +
            " pre_relocation_match=1 post_relocation_match=1",
        PREFIX + "type=file22_translated_function_entry "
            "original_rom_function=0x0069E080 guest_function_entry=0x801CB460 "
            "translated_entry_reached=1 file22_slot_active=1 "
            "pre_relocation_match=1 post_relocation_match=1 usa_sys_valid=1",
    ]))
    assert summarize(events)["file22_bytes_confirmed"]
    assert summarize(events)["file22_translated_function_reached"]
    assert summarize(events)["guest_pc_register_sampled"] is False
    assert summarize(events)["scene_entry_confirmed"] is False
    assert len(summarize(events)["limitations"]) >= 3
    assert summarize(events[:2])["file22_bytes_confirmed"]
    assert not summarize(events[:2])["file22_translated_function_reached"]
    assert not summarize(events[1:])["file22_bytes_confirmed"]
    bad = events.copy()
    bad[1] = dict(bad[1], exact_bytes="0")
    assert not summarize(bad)["file22_bytes_confirmed"]
    bad = events.copy()
    bad[2] = dict(bad[2], file22_slot_active="0")
    assert not summarize(bad)["file22_translated_function_reached"]
    assert not summarize([])["file22_bytes_confirmed"]
    print("file22 trace evidence gate tests: PASS")

if __name__ == "__main__":
    main()
