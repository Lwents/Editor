#!/usr/bin/env bash
# Build the bundled GPL-2.0 frei0r browser runtime from its exact included source.
# Requires Emscripten 4.0.20 (emcmake/emcc), CMake >= 3.20 and Ninja on PATH.
set -euo pipefail
editor_root="$(cd "$(dirname "$0")/.." && pwd)"
editor_build="$(mktemp -d)"
trap 'rm -rf "$editor_build"' EXIT
tar -xzf "$editor_root/fe/apps/web/public/vendor/frei0r/source.tar.gz" -C "$editor_build"
emcmake cmake -S "$editor_build" -B "$editor_build/build" -G Ninja \
 -DCMAKE_BUILD_TYPE=Release -DFREI0R_BUILD_BROWSER_DEMO_RUNTIME=ON \
 -DBUILD_TESTING=OFF -DWITHOUT_GAVL=ON -DWITHOUT_OPENCV=ON -DWITHOUT_CAIRO=ON
cmake --build "$editor_build/build" --target frei0r-demo-browser-runtime
cp "$editor_build/build/examples/browser-demo/runtime/frei0r-demo-runtime.mjs" "$editor_root/fe/apps/web/public/vendor/frei0r/"
cp "$editor_build/build/examples/browser-demo/runtime/frei0r-demo-runtime.wasm" "$editor_root/fe/apps/web/public/vendor/frei0r/"
