"""Widen the two JP camera scissors only when the host requests Expand."""

SITES = (
    ("0x80017900: sw          $t4, 0x4($v0)", "r2", "r12"),
    ("0x80017E54: sw          $t6, 0x4($v1)", "r3", "r14"),
)


def inject(source: str) -> str:
    marker = "goemon_jp_widen_camera_scissor"
    if marker in source:
        raise ValueError("Japanese widescreen already applied")
    for comment, base, value in SITES:
        anchor = f"    // {comment}\n    MEM_W(0X4, ctx->{base}) = ctx->{value};\n"
        if source.count(anchor) != 1:
            raise ValueError(f"Japanese camera scissor anchor missing: {comment}")
        source = source.replace(
            anchor,
            anchor + f"    {marker}(&MEM_W(0X0, ctx->{base}), &MEM_W(0X4, ctx->{base}));\n",
            1,
        )
    return f'#include <stdint.h>\nextern void {marker}(int32_t*, int32_t*);\n' + source
