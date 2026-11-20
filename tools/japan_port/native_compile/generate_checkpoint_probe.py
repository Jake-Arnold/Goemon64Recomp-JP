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
MESSAGE_CALLS = {
    "osSendMesg_recomp": "GOEMON_JP_MESSAGE_SEND",
    "osJamMesg_recomp": "GOEMON_JP_MESSAGE_JAM",
    "osRecvMesg_recomp": "GOEMON_JP_MESSAGE_RECEIVE",
}
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


def emit_function(name: str, body: list[str], functions: dict[str, int],
                  message_continuations: bool) -> tuple[str, list[tuple[int, str]]]:
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
        message = MESSAGE_CALLS.get(callee) if message_continuations and callee_index < 0 else None
        callees.append((callee_index, message or "GOEMON_JP_MESSAGE_NONE"))
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
        elif message:
            emitted.extend((
                "    capture_cpu(&execution->state, ctx);",
                f"    goemon_jp_probe_message_call(rdram, ctx, &execution->state, {callee});",
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


def generate(bodies: dict[str, list[str]], *, message_continuations: bool = False,
             entry_points: dict[str, str] | None = None) -> str:
    functions = {name: index for index, name in enumerate(bodies)}
    if entry_points is None:
        entry_points = {"goemon_jp_guest_checkpoint_probe": next(iter(bodies))}
    revision_input = f"goemon-jp-checkpoint-v4:{message_continuations}\n" + "\n".join(
        name + "\n" + "\n".join(body) for name, body in bodies.items()
    )
    revision = hashlib.sha256(revision_input.encode()).hexdigest()[:16]
    revision_constant = f"UINT64_C(0x{revision})"
    prototypes = "\n".join(f"static probe_function probe_{name};" for name in functions)
    definitions = []
    site_tables = []
    entries = []
    for name, body in bodies.items():
        definition, sites = emit_function(name, body, functions, message_continuations)
        definitions.append(definition)
        site_values = ", ".join(f"{{{child}, {message}}}" for child, message in sites)
        site_tables.append(f"static const probe_site sites_{name}[] = {{{site_values}}};")
        entries.append(f"    {{probe_{name}, sites_{name}, {len(sites)}}},")
    definition_text = "\n".join(definitions)
    table_text = "\n".join(site_tables)
    entry_text = "\n".join(entries)
    validate_message = """
    if (state->message.call != GOEMON_JP_MESSAGE_NONE) {
        const goemon_jp_guest_frame* leaf = &state->frames[state->frame_count - 1];
        const uint8_t expected = functions[leaf->function_index].sites[leaf->call_site - 1].message;
        if (expected == GOEMON_JP_MESSAGE_NONE) {
            return false;
        }
        if (state->message.call == GOEMON_JP_MESSAGE_RETURN) {
            return state->message.queue == 0 &&
                (state->message.argument == 0 || state->message.argument == -1);
        }
        return state->message.call == expected && state->message.queue != 0 &&
            state->message.queue == (int32_t)state->registers[4] &&
            state->message.argument == (int32_t)state->registers[5] &&
            (int32_t)state->registers[6] == 1; // OS_MESG_BLOCK
    }""" if message_continuations else """
    if (state->message.call != GOEMON_JP_MESSAGE_NONE) {
        return false;
    }"""
    resume_message = """
    if (execution.state.message.call != GOEMON_JP_MESSAGE_NONE &&
        !goemon_jp_probe_resume_message_call(rdram, &execution.state)) {
        return GOEMON_JP_CHECKPOINT_INVALID;
    }""" if message_continuations else ""
    exports = "\n".join(f"""
goemon_jp_checkpoint_result {symbol}(
    uint8_t* rdram, recomp_context* ctx, const goemon_jp_guest_checkpoint* resume,
    goemon_jp_checkpoint_callback* callback, void* user) {{
    return run_checkpoint_probe({functions[root]}, rdram, ctx, resume, callback, user);
}}
""" for symbol, root in entry_points.items())
    return f"""{PROBE_HEADER}
{prototypes}

{definition_text}
typedef struct {{
    int child;
    uint8_t message;
}} probe_site;

{table_text}

static const struct {{
    probe_function* call;
    const probe_site* sites;
    uint32_t site_count;
}} functions[] = {{
{entry_text}
}};

static bool valid_checkpoint(uint32_t root, const goemon_jp_guest_checkpoint* state) {{
    if (state->probe_revision != {revision_constant} ||
        state->frame_count == 0 || state->frame_count > GOEMON_JP_CHECKPOINT_MAX_FRAMES ||
        state->frames[0].function_index != root ||
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
            const int callee = functions[index].sites[site - 1].child;
            if (callee < 0 || (uint32_t)callee != state->frames[depth + 1].function_index) {{
                return false;
            }}
        }}
    }}{validate_message}
    return true;
}}

static goemon_jp_checkpoint_result run_checkpoint_probe(
    uint32_t root, uint8_t* rdram, recomp_context* ctx,
    const goemon_jp_guest_checkpoint* resume, goemon_jp_checkpoint_callback* callback, void* user) {{
    probe_execution execution = {{
        .state = {{.probe_revision = {revision_constant}}},
        .callback = callback,
        .user = user,
    }};
    if (resume == NULL) {{
        return functions[root].call(rdram, ctx, &execution, NULL);
    }}
    if (!valid_checkpoint(root, resume)) {{
        return GOEMON_JP_CHECKPOINT_INVALID;
    }}
    execution.state = *resume;{resume_message}
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
{exports}"""


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


def instrument_roots(source: Path, bodies: dict[str, list[str]], entries: dict[str, str], probe: str) -> str:
    source_text = source.read_text(encoding="utf-8")
    declarations = ""
    for symbol, name in entries.items():
        original_definition = (
            f"RECOMP_FUNC void {name}(uint8_t* rdram, recomp_context* ctx) {{\n"
            + "\n".join(bodies[name]) + "\n;}"
        )
        if source_text.count(original_definition) != 1:
            raise ValueError(f"{source.name} does not contain the selected root function {name}")
        declarations += f"""goemon_jp_checkpoint_result {symbol}(
    uint8_t*, recomp_context*, const goemon_jp_guest_checkpoint*, goemon_jp_checkpoint_callback*, void*);
"""
        wrapper = f"""RECOMP_FUNC void {name}(uint8_t* rdram, recomp_context* ctx) {{
    const goemon_jp_checkpoint_result status = {symbol}(rdram, ctx, NULL, NULL, NULL);
    if (status != GOEMON_JP_CHECKPOINT_FINISHED) abort();
}}
"""
        source_text = source_text.replace(original_definition, wrapper, 1)
    return '#include "guest_checkpoint_probe.h"\n' + declarations + source_text + "\n" + probe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", required=True, type=Path)
    roots = parser.add_mutually_exclusive_group(required=True)
    roots.add_argument("--function")
    roots.add_argument("--live-root", action="append")
    parser.add_argument("--callee", action="append", default=[])
    parser.add_argument("--message-continuations", action="store_true")
    parser.add_argument("--instrument-root-source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.live_root and not (args.instrument_root_source and args.message_continuations):
            raise ValueError("Live roots require an instrumented source unit and message continuations")
        entries = ({f"goemon_jp_guest_checkpoint_probe_{root}": root for root in args.live_root}
                   if args.live_root else {"goemon_jp_guest_checkpoint_probe": args.function})
        names = dict.fromkeys([*entries.values(), *args.callee])
        bodies = {name: find_body(args.generated, name) for name in names}
        result = generate(bodies, message_continuations=args.message_continuations, entry_points=entries)
        if args.instrument_root_source:
            result = instrument_roots(args.instrument_root_source, bodies, entries, result)
    except ValueError as error:
        parser.error(str(error))
    args.output.write_text(result, encoding="utf-8")


if __name__ == "__main__":
    main()
