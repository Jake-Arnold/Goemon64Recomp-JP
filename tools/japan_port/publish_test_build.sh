#!/usr/bin/env bash
# Publish the source for a versioned, locally built Linux test build.
set -euo pipefail
if [[ $# != 1 ]]; then
    printf 'Usage: %s /path/to/Goemon64RecompiledJP\n' "$0" >&2
    exit 2
fi
root=$(git rev-parse --show-toplevel)
repo=Jake-Arnold/Goemon64Recomp-JP
origin=$(git -C "$root" remote get-url --push origin)
case "$origin" in
    "https://github.com/$repo"|"https://github.com/$repo.git"|"git@github.com:$repo.git") ;;
    *) echo "Refusing to publish to an unexpected origin: $origin" >&2; exit 1 ;;
esac
if ! git -C "$root" diff --quiet HEAD; then
    echo "Commit the source changes before publishing." >&2
    exit 1
fi
version=$(<"$root/VERSION")
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-test\.[1-9][0-9]*$ ]]; then
    echo "Set VERSION to a new X.Y.Z-test.N version." >&2
    exit 1
fi
commit=$(git -C "$root" rev-parse HEAD)
short_commit=$(git -C "$root" rev-parse --short=10 HEAD)
build_id=$("$1" --version)
if [[ "$build_id" != *"v$version ($short_commit)"* ]]; then
    echo "The executable must match VERSION and the clean source commit. Rebuild first." >&2
    exit 1
fi
tag="v$version"
if gh release view "$tag" -R "$repo" >/dev/null 2>&1; then
    echo "That test version already exists. Bump VERSION first." >&2
    exit 1
fi
git -C "$root" -c credential.helper= -c 'credential.helper=!gh auth git-credential' \
    push origin HEAD:refs/heads/main
gh release create "$tag" --repo "$repo" --target "$commit" --prerelease \
    --title "$tag" --notes "Source snapshot for the locally built Ubuntu test build. Build instructions are in BUILDING.md."
