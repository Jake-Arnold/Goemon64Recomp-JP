#!/usr/bin/env python3
"""Generate a guest call-chain resume probe from pinned N64Recomp C output."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re

FUNCTION = re.compile(r"^RECOMP_FUNC void (\w+)\(uint8_t\* rdram, recomp_context\* ctx\) \{\n")
LOCALS = "    uint64_t hi = 0, lo = 0, result = 0;\n    int c1cs = 0;"
CALL = re.compile(r"(?:\w+|LOOKUP_FUNC\((?:0x[\dA-F]+|ctx->r\d+)\))\(rdram, ctx\);")
EXTRA_LOCAL = re.compile(
    r"(?:(?:unsigned|signed|const|static)\s+)*"
    r"(?:gpr|fpr|u?int\w*|float|double|bool|size_t|char|long|short|struct)\s+"
)
PROBE_HEADER = '''#include "guest_checkpoint_probe.h"
#include "funcs.h"
#include <stddef.h>
#include <string.h>

_Static_assert(sizeof(gpr) == sizeof(uint64_t), "Unexpected GPR size");
_Static_assert(sizeof(fpr) == sizeof(uint64_t), "Unexpected FPR size");
_Static_assert(offsetof(recomp_context, r31) - offsetof(recomp_context, r0) == 31 * sizeof(gpr),
    "GPR layout changed");
_Static_assert(offsetof(recomp_context, f31) - offsetof(recomp_context, f0) == 31 * sizeof(fpr),
    "FPR layout changed");

static void capture_cpu(goemon_jp_guest_checkpoint* state, const recomp_context* ctx) {
    const unsigned char* bytes = (const unsigned char*)ctx;
    memcpy(state->registers, bytes + offsetof(recomp_context, r0), sizeof(state->registers));
    memcpy(state->float_registers, bytes + offsetof(recomp_context, f0), sizeof(state->float_registers));
    state->context_hi = ctx->hi;
    state->context_lo = ctx->lo;
    state->status_reg = ctx->status_reg;
    state->mips3_float_mode = ctx->mips3_float_mode;
    state->rounding_mode = get_cop1_cs();
}

static void restore_cpu(recomp_context* ctx, const goemon_jp_guest_checkpoint* state) {
    unsigned char* bytes = (unsigned char*)ctx;
    memcpy(bytes + offsetof(recomp_context, r0), state->registers, sizeof(state->registers));
    memcpy(bytes + offsetof(recomp_context, f0), state->float_registers, sizeof(state->float_registers));
    ctx->hi = state->context_hi;
    ctx->lo = state->context_lo;
    ctx->status_reg = state->status_reg;
    ctx->mips3_float_mode = state->mips3_float_mode;
    ctx->f_odd = state->mips3_float_mode ? &ctx->f1.u32l : &ctx->f0.u32h;
    set_cop1_cs(state->rounding_mode);
}

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC optimize ("rounding-math")
#endif

typedef struct {
    goemon_jp_guest_checkpoint state;
    goemon_jp_checkpoint_callback* callback;
    void* user;
} probe_execution;

typedef goemon_jp_checkpoint_result probe_function(
    uint8_t* rdram, recomp_context* ctx, probe_execution* execution,
    const goemon_jp_guest_frame* resume);
'''


def emit_function(name: str, body: list[str], functions: dict[str, int]) -> tuple[str, list[int]]:
    if "\n".join(body[:2]) != LOCALS:
        raise ValueError(f"{name}: expected the pinned N64Recomp local declarations")
    emitted = []
    callees = []
    for index, line in enumerate(body[2:], start=2):
        statement = line.strip()
        if statement == "TRACE_ENTRY()":
            continue
        if statement == "TRACE_RETURN()":
            if index + 1 == len(body) or body[index + 1].strip() != "return;":
                raise ValueError(f"{name}: expected an explicit return after the generated trace macro")
            continue
        if statement == "return;":
            emitted.append(line.replace("return;", "goto goemon_finished;"))
            continue
        if EXTRA_LOCAL.match(statement) or statement.startswith("#"):
            raise ValueError(f"{name}: unsupported additional locals or preprocessor statements")
        if not CALL.fullmatch(statement):
            emitted.append(line)
            continue

        callee = statement.split("(", 1)[0]
        callee_index = functions.get(callee, -1)
        callees.append(callee_index)
        site = len(callees)
        emitted.extend((
            f"    *frame = (goemon_jp_guest_frame){{.function_index = {functions[name]}, .call_site = {site},",
            "        .local_hi = hi, .local_lo = lo, .local_result = result, .local_c1cs = c1cs};",
        ))
        if callee_index >= 0:
            emitted.extend((
                "    {",
                f"        goemon_jp_checkpoint_result child = probe_{callee}(rdram, ctx, execution, NULL);",
                "        if (child != GOEMON_JP_CHECKPOINT_FINISHED) return child;",
                "    }",
            ))
        else:
            emitted.append(line)
        emitted.extend((
            "    if (execution->callback != NULL) {",
            "        capture_cpu(&execution->state, ctx);",
            "        if (execution->callback(&execution->state, execution->user))",
            "            return GOEMON_JP_CHECKPOINT_STOPPED;",
            "    }",
            f"goemon_resume_{site}: ;",
        ))
    if not callees:
        raise ValueError(f"{name}: expected at least one generated function call")
    dispatch = "\n".join(
        f"        case {site}: goto goemon_resume_{site};"
        for site in range(1, len(callees) + 1)
    )
    instructions = "\n".join(emitted)
    return f"""static goemon_jp_checkpoint_result probe_{name}(
    uint8_t* rdram, recomp_context* ctx, probe_execution* execution,
    const goemon_jp_guest_frame* resume) {{
    SET_FENV_ACCESS()
{LOCALS}
    if (execution->state.frame_count == GOEMON_JP_CHECKPOINT_MAX_FRAMES) {{
        return GOEMON_JP_CHECKPOINT_INVALID;
    }}
    goemon_jp_guest_frame* frame = &execution->state.frames[execution->state.frame_count++];
    if (resume != NULL) {{
        *frame = *resume;
        hi = resume->local_hi;
        lo = resume->local_lo;
        result = resume->local_result;
        c1cs = resume->local_c1cs;
        switch (resume->call_site) {{
{dispatch}
        }}
    }}
{instructions}
goemon_finished:
    --execution->state.frame_count;
    return GOEMON_JP_CHECKPOINT_FINISHED;
}}
""", callees


def generate(bodies: dict[str, list[str]]) -> str:
    functions = {name: index for index, name in enumerate(bodies)}
    revision_input = "goemon-jp-checkpoint-v2\n" + "\n".join(
        name + "\n" + "\n".join(body) for name, body in bodies.items()
    )
    revision = hashlib.sha256(revision_input.encode()).hexdigest()[:16]
    revision_constant = f"UINT64_C(0x{revision})"
    prototypes = "\n".join(f"static probe_function probe_{name};" for name in functions)
    definitions = []
    site_tables = []
    entries = []
    for name, body in bodies.items():
        definition, sites = emit_function(name, body, functions)
        definitions.append(definition)
        site_values = ", ".join(map(str, sites))
        site_tables.append(f"static const int sites_{name}[] = {{{site_values}}};")
        entries.append(f"    {{probe_{name}, sites_{name}, {len(sites)}}},")
    definition_text = "\n".join(definitions)
    table_text = "\n".join(site_tables)
    entry_text = "\n".join(entries)
    return f"""{PROBE_HEADER}
{prototypes}

{definition_text}
{table_text}

static const struct {{
    probe_function* call;
    const int* callees;
    uint32_t site_count;
}} functions[] = {{
{entry_text}
}};

static bool valid_checkpoint(const goemon_jp_guest_checkpoint* state) {{
    if (state->probe_revision != {revision_constant} ||
        state->frame_count == 0 || state->frame_count > GOEMON_JP_CHECKPOINT_MAX_FRAMES ||
        state->frames[0].function_index != 0 ||
        state->mips3_float_mode > 1 || state->rounding_mode > 3) {{
        return false;
    }}
    for (uint32_t depth = 0; depth < state->frame_count; ++depth) {{
        const goemon_jp_guest_frame* frame = &state->frames[depth];
        const uint32_t index = frame->function_index;
        const uint32_t site = frame->call_site;
        if (index >= sizeof(functions) / sizeof(functions[0]) ||
            site == 0 || site > functions[index].site_count) {{
            return false;
        }}
        if (depth + 1 < state->frame_count) {{
            const int callee = functions[index].callees[site - 1];
            if (callee < 0 || (uint32_t)callee != state->frames[depth + 1].function_index) {{
                return false;
            }}
        }}
    }}
    return true;
}}

goemon_jp_checkpoint_result goemon_jp_guest_checkpoint_probe(
    uint8_t* rdram, recomp_context* ctx, const goemon_jp_guest_checkpoint* resume,
    goemon_jp_checkpoint_callback* callback, void* user) {{
    probe_execution execution = {{
        .state = {{.probe_revision = {revision_constant}}},
        .callback = callback,
        .user = user,
    }};
    if (resume == NULL) {{
        return functions[0].call(rdram, ctx, &execution, NULL);
    }}
    if (!valid_checkpoint(resume)) {{
        return GOEMON_JP_CHECKPOINT_INVALID;
    }}
    execution.state = *resume;
    restore_cpu(ctx, &execution.state);
    // Finish the leaf first, then return to suspended callers with the
    // same register context. A callback may overwrite the original input.
    while (execution.state.frame_count != 0) {{
        goemon_jp_guest_frame frame = execution.state.frames[--execution.state.frame_count];
        goemon_jp_checkpoint_result result =
            functions[frame.function_index].call(rdram, ctx, &execution, &frame);
        if (result != GOEMON_JP_CHECKPOINT_FINISHED) {{
            return result;
        }}
    }}
    return GOEMON_JP_CHECKPOINT_FINISHED;
}}
"""


def find_body(directory: Path, name: str) -> list[str]:
    body = None
    declaration = f"RECOMP_FUNC void {name}("
    for source in sorted(directory.glob("funcs_*.c")):
        with source.open(encoding="utf-8") as lines:
            for line in lines:
                if not line.startswith(declaration):
                    continue
                if body is not None:
                    raise ValueError(f"Found more than one definition of {name}")
                if not FUNCTION.fullmatch(line):
                    raise ValueError(f"Unexpected generated signature for {name}")
                body = []
                for line in lines:
                    if line.rstrip() == ";}":
                        break
                    body.append(line.rstrip("\n"))
                else:
                    raise ValueError(f"Missing generated function terminator for {name}")
    if body is None:
        raise ValueError(f"Generated function {name} was not found")
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", required=True, type=Path)
    parser.add_argument("--function", required=True)
    parser.add_argument("--callee", action="append", default=[])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        names = dict.fromkeys([args.function, *args.callee])
        result = generate({name: find_body(args.generated, name) for name in names})
    except ValueError as error:
        parser.error(str(error))
    args.output.write_text(result, encoding="utf-8")


if __name__ == "__main__":
    main()
