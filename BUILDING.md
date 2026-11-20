# Building Guide

This guide will help you build the project on your local machine. The process will require you to provide a decompressed ROM of the US version of the game.

These steps cover: decompressing the ROM, running the recompiler and finally building the project.

## 1. Clone the Goemon64Recomp Repository
This project makes use of submodules so you will need to clone the repository with the `--recurse-submodules` flag.

```bash
git clone --recurse-submodules
# if you forgot to clone with --recurse-submodules
# cd /path/to/cloned/repo && git submodule update --init --recursive
```

## 2. Install Dependencies

### Linux
For Linux the instructions for Ubuntu are provided, but you can find the equivalent packages for your preferred distro.

```bash
# For Ubuntu, simply run:
sudo apt-get install cmake ninja-build libsdl2-dev libgtk-3-dev lld llvm clang
```

### Windows
You will need to install [Visual Studio 2022](https://visualstudio.microsoft.com/downloads/).
In the setup process you'll need to select the following options and tools for installation:
- Desktop development with C++
- C++ Clang Compiler for Windows
- C++ CMake tools for Windows

The other tool necessary will be `make` which can be installe via [Chocolatey](https://chocolatey.org/):
```bash
choco install make
```

## 3. Decompressing the target ROM
You will need to decompress the NTSC-U Mystical Ninja Starring Goemon ROM (sha1: df8083a54296b8c151917c5333e1c85f014a2a66) before running the recompiler.

Follow the build instructions for the [Mystical Ninja Starring Goemon Decompilation Project](https://github.com/klorfmorf/mnsg) in order to generate a decompressed ROM.

Copy the decompressed ROM with the name `baserom.us.decompressed.z64` from the root of the decompilation project to the root of the Goemon64Recomp repository and rename it to `mnsg.z64`.

## 4. Generating the C code

Now that you have the required files, you must build [N64Recomp](https://github.com/Mr-Wiseguy/N64Recomp) and run it to generate the C code to be compiled. The building instructions can be found [here](https://github.com/Mr-Wiseguy/N64Recomp?tab=readme-ov-file#building). That will build the executables: `N64Recomp` and `RSPRecomp` which you should copy to the root of the Goemon64Recomp repository.

After that, go back to the repository root, and run the following commands:
```bash
./N64Recomp mnsg.toml
./RSPRecomp aspMain.toml
```

## 5. Building the Project

Finally, you can build the project! :rocket:

On Windows, you can open the repository folder with Visual Studio, and you'll be able to `[build / run / debug]` the project from there.

If you prefer the command line or you're on a Unix platform you can build the project using CMake:

```bash
cmake -S . -B build-cmake -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_C_COMPILER=clang -G Ninja -DCMAKE_BUILD_TYPE=Release # or Debug if you want to debug
cmake --build build-cmake --target Goemon64Recompiled -j$(nproc) --config Release # or Debug
```

## 6. Success

Voilà! You should now have a `Goemon64Recompiled` executable in the build directory! If you used Visual Studio this will be `out/build/x64-[Configuration]` and if you used the provided CMake commands then this will be `build-cmake`. You will need to run the executable out of the root folder of this project or copy the assets folder to the build folder to run it.

> [!IMPORTANT]  
> In the game itself, you should be using a standard ROM, not the decompressed one.

### USA and Japan on one launcher

On Linux, build with `GOEMON_BUILD_JAPAN=ON`, the existing integrated reset
runtime, and `GOEMON_UNIFIED_REGIONS=ON`. Open `Goemon64RecompiledJP` with
the shared assets. That executable contains both compiled game code sets.
Select USA or Japan and choose **Start Game** in the same window.

**Select ROM** and **Change ROM** apply to the highlighted edition. ROMs,
mods and saves use their edition's profile. Controls, keybinds, graphics and
audio use the shared launcher's settings profile (the existing Japanese
configuration folder). The USA standalone executable remains available for
builds using the older launcher setup.

F8 resets the running game. F6 pauses or resumes it when native pause is
enabled in the build.

The launcher checks stable and test GitHub releases on demand using the
system `curl` installation. Before each test build, bump `VERSION`
(for example, `0.2.26-test.1` to `0.2.26-test.2`). After building and
committing, publish the matching source to the standalone GitHub repository:

```bash
tools/japan_port/publish_test_build.sh /path/to/Goemon64RecompiledJP
```

The ROM picker accepts the original supported revisions. An N64 ROM rebuilt
from a decompilation can be distributed as a patch for emulator or hardware
play. Native play requires a recompilation built for that hack's ROM layout
and content. Recomp mods are available through the existing **Mods** screen.

For temporary patched ROM experiments on Ubuntu, launch with
`GOEMON_EXPERIMENTAL_ANY_ROM=1`. Choose the edition the patch targets, then
use **Select ROM** or **Change ROM** and **Start Game**. The picker accepts
N64 ROM images without requiring a stock hash. The original decompressor
and the installed compiled game code still run, so some inputs will fail.

The game window shows the imported filename. Experimental imports and saves
live under `experimental-roms` in the selected edition's configuration
folder. Save profiles are keyed by edition and original filename. Reimport
using the same filename to reuse that profile.
