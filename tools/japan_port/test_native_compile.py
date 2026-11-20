"""ROM-free CMake smoke tests for isolated Japanese static-library build."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent / 'native_compile'


@unittest.skipUnless(shutil.which('cmake') and shutil.which('cc') and shutil.which('c++'),
                     'Native CMake and C/C++ compilers are required')
class NativeCompileSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.generated = self.base / 'japan-generated'
        self.headers = self.base / 'sdk'
        self.trace = self.base / 'port'
        self.build = self.base / 'build'
        for path in (self.generated, self.headers, self.trace):
            path.mkdir()
        (self.headers / 'recomp.h').write_text(
            '#pragma once\n#include <stdint.h>\n'
            'typedef struct recomp_context { int value; } recomp_context;\n'
        )
        (self.trace / 'trace.h').write_text('#pragma once\n')
        (self.generated / 'funcs.h').write_text('#pragma once\n')
        (self.generated / 'lookup.cpp').write_text('int japan_lookup_smoke_test() { return 42; }\n')
        (self.generated / 'recomp_overlays.inl').write_text('// Synthetic only\n')
        for index in range(140):
            (self.generated / f'funcs_{index}.c').write_text(
                f'#include "recomp.h"\n'
                f'void jp_synthetic_{index}(uint8_t* ptr, recomp_context* ctx)'
                f' {{ (void)ptr; (void)ctx; }}\n'
            )

    def tearDown(self):
        self.temp.cleanup()

    def configure(self):
        return subprocess.run(
            ['cmake', '-S', str(HERE), '-B', str(self.build),
             '-DGOEMON_JAPAN_GENERATED=' + str(self.generated),
             '-DGOEMON_N64RECOMP_INCLUDE=' + str(self.headers),
             '-DGOEMON_PORT_INCLUDE=' + str(self.trace)],
            capture_output=True, text=True, timeout=30
        )

    def test_synthetic_archive_compiles_without_rom(self):
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run(
            ['cmake', '--build', str(self.build), '--target', 'GoemonRecompiledJapan',
             '--parallel', '4'],
            capture_output=True, text=True, timeout=90
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(list(self.build.glob('*GoemonRecompiledJapan.a')))

    def test_missing_generated_header_rejected(self):
        (self.generated / 'funcs.h').unlink()
        result = self.configure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Required native recompilation header', result.stdout + result.stderr)

    def test_incomplete_function_batch_rejected(self):
        for index in range(10):
            (self.generated / f'funcs_{index}.c').unlink()
        result = self.configure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Incomplete Japanese N64Recomp C output', result.stdout + result.stderr)


    def test_guest_checkpoint_restores_nested_frames_without_replaying_calls(self):
        sdk = Path(os.environ.get(
            'GOEMON_N64RECOMP_INCLUDE',
            HERE.parents[2] / 'lib/N64ModernRuntime/N64Recomp/include',
        ))
        if not (sdk / 'recomp.h').is_file():
            self.skipTest('Requires pinned N64Recomp recomp.h')
        (self.generated / 'funcs.h').write_text(
            'void synthetic_call(uint8_t* rdram, recomp_context* ctx);\n'
        )
        (self.generated / 'funcs_0.c').write_text("""RECOMP_FUNC void fixture(uint8_t* rdram, recomp_context* ctx) {
    uint64_t hi = 0, lo = 0, result = 0;
    int c1cs = 0;
    TRACE_ENTRY()
    hi = 17;
    lo = 3;
    result = 11;
    c1cs = 5;
    nested(rdram, ctx);
    ctx->r4 = hi + lo + result + c1cs;
    TRACE_RETURN()
    return;
;}
RECOMP_FUNC void nested(uint8_t* rdram, recomp_context* ctx) {
    uint64_t hi = 0, lo = 0, result = 0;
    int c1cs = 0;
    TRACE_ENTRY()
    hi = 23;
    synthetic_call(rdram, ctx);
    if (ctx->r2 == 1) {
        LOOKUP_FUNC(ctx->r3)(rdram, ctx);
    }
    ctx->r5 = hi;
    TRACE_RETURN()
    return;
;}
""")
        probe = self.base / 'probe.c'
        subprocess.run(
            [sys.executable, str(HERE / 'generate_checkpoint_probe.py'),
             '--generated', str(self.generated), '--function', 'fixture',
             '--callee', 'nested', '--output', str(probe)],
            check=True, timeout=10,
        )
        harness = self.base / 'harness.c'
        harness.write_text("""#include "guest_checkpoint_probe.h"

static unsigned lookup_count;

void synthetic_call(uint8_t* rdram, recomp_context* ctx) {
    ++rdram[0];
    ctx->r2 = 1;
    ctx->r3 = 0x100;
    ctx->mips3_float_mode = 1;
    ctx->f_odd = &ctx->f1.u32l;
}

recomp_func_t* get_function(int32_t addr) {
    assert(addr == 0x100);
    ++lookup_count;
    return synthetic_call;
}

static bool capture(const goemon_jp_guest_checkpoint* state, void* user) {
    *(goemon_jp_guest_checkpoint*)user = *state;
    return true;
}

int main(void) {
    uint8_t rdram[1] = {0};
    recomp_context initial = {0};
    recomp_context resumed = {0};
    goemon_jp_guest_checkpoint saved;
    assert(goemon_jp_guest_checkpoint_probe(rdram, &initial, NULL, capture, &saved)
        == GOEMON_JP_CHECKPOINT_STOPPED);
    assert(rdram[0] == 1 && lookup_count == 0 && saved.frame_count == 2);
    assert(saved.frames[0].call_site == 1 && saved.frames[1].call_site == 1);
    goemon_jp_guest_checkpoint invalid = saved;
    invalid.frames[1].function_index = 0;
    assert(goemon_jp_guest_checkpoint_probe(rdram, &resumed, &invalid, NULL, NULL)
        == GOEMON_JP_CHECKPOINT_INVALID);
    assert(resumed.r2 == 0 && rdram[0] == 1);
    assert(goemon_jp_guest_checkpoint_probe(rdram, &resumed, &saved, capture, &saved)
        == GOEMON_JP_CHECKPOINT_STOPPED);
    assert(rdram[0] == 2 && lookup_count == 1 && saved.frame_count == 2);
    assert(saved.frames[1].call_site == 2);
    assert(goemon_jp_guest_checkpoint_probe(rdram, &resumed, &saved, NULL, NULL)
        == GOEMON_JP_CHECKPOINT_FINISHED);
    assert(rdram[0] == 2 && lookup_count == 1);
    assert(resumed.r4 == 36 && resumed.r5 == 23 && resumed.f_odd == &resumed.f1.u32l);
}
""")
        exe = self.base / 'checkpoint-test'
        subprocess.run(
            ['cc', '-std=gnu17', '-O2', '-I', str(sdk), '-I', str(HERE),
             '-I', str(self.generated), str(probe), str(harness), '-lm', '-o', str(exe)],
            check=True, timeout=30,
        )
        subprocess.run([str(exe)], check=True, timeout=5)


if __name__ == '__main__':
    unittest.main()
