#!/usr/bin/env python3
"""Summarize live original-USA file22 image and translated-entry metadata.

Do not mistake a translated entry for a sampled guest PC, guest frame, or
an original-ROM matching-C promotion. Input must be a live session log.
"""
import argparse
import json
from pathlib import Path
import re

PREFIX = "[Goemon USA native state trace] "
KINDS = {"file22_image_attestation", "file22_translated_function_entry",
         "file_loader_postprocess_returned"}

def parse(data):
    records = []
    for line in data.splitlines():
        if PREFIX not in line:
            continue
        fields = dict(re.findall(r"(?<!\S)([a-z][a-z0-9_]*)=([^\s]+)",
                                 line.split(PREFIX, 1)[1]))
        if fields.get("type") in KINDS:
            records.append(fields)
    return records

def valid_attestation(record, stage):
    return (record.get("type") == "file22_image_attestation" and
            record.get("stage") == stage and record.get("file_id") == "22" and
            record.get("source_rom") == "0x0069E080" and
            record.get("guest_destination") == "0x801CB460" and
            record.get("source_length") == "47264" and
            record.get("reference_ok") == "1" and
            record.get("guest_window_ok") == "1" and
            record.get("exact_bytes") == "1" and
            record.get("mismatches") == "0")

def summarize(records):
    pre = [r for r in records if r.get("type") == "file22_image_attestation"
           and r.get("stage") == "before_relocation"]
    post = [r for r in records if r.get("type") == "file22_image_attestation"
            and r.get("stage") == "after_relocation"]
    entries = [r for r in records if r.get("type") == "file22_translated_function_entry"]
    good_pre = [r for r in pre if valid_attestation(r, "before_relocation")]
    good_post = [r for r in post if valid_attestation(r, "after_relocation")
                 and r.get("pre_relocation_match") == "1"
                 and r.get("post_relocation_match") == "1"]
    good_entries = [
        r for r in entries
        if r.get("translated_entry_reached") == "1"
        and r.get("file22_slot_active") == "1"
        and r.get("post_relocation_match") == "1"
        and r.get("pre_relocation_match") == "1"
        and r.get("usa_sys_valid") == "1"
        and 0x69E080 <= int(r.get("original_rom_function", "0"), 16) < 0x69F400
        and int(r.get("guest_function_entry", "0"), 16)
            == 0x801CB460 + int(r.get("original_rom_function", "0"), 16) - 0x69E080
    ]
    limitations = [
        "No independent sampled MIPS guest PC register (translated entry only)",
        "No independently confirmed scene entry or completed guest frame",
        "File22 static zero-delta comparison does not verify general relocations",
    ]
    if not good_pre:
        limitations.append("No exact pre-relocation ROM-backed image comparison")
    if not good_post:
        limitations.append("No exact post-relocation ROM-backed image comparison")
    if not good_entries:
        limitations.append("No verified-active translated file22 function entry")
    return {
        "evidence": "independent_guest_image_and_translated_entry_not_guest_pc_sample",
        "pre_relocation_events": len(pre),
        "pre_relocation_exact": len(good_pre),
        "post_relocation_events": len(post),
        "post_relocation_exact_both_stages": len(good_post),
        "translated_entry_events": len(entries),
        "verified_active_translated_entry_events": len(good_entries),
        "verified_active_function_addresses":
            sorted({r["guest_function_entry"] for r in good_entries}),
        "file22_bytes_confirmed": bool(good_pre and good_post),
        "file22_translated_function_reached": bool(good_pre and good_post and good_entries),
        "guest_pc_register_sampled": False,
        "scene_entry_confirmed": False,
        "limitations": limitations,
    }

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("log", type=Path, help="Live USA session log, not synthetic fixture output")
    p.add_argument("--json", type=Path)
    args = p.parse_args()
    report = json.dumps(summarize(parse(args.log.read_text(
        encoding="utf-8", errors="replace"))), indent=2)
    print(report)
    if args.json:
        args.json.write_text(report + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
