# opencut-wasm

Shared video editor logic compiled to WebAssembly. Used by the [OpenCut](https://github.com/opencut/opencut) web app.

## Install

```bash
npm install opencut-wasm
```

## Usage

```ts
import { formatTimecode, mediaTimeFromSeconds } from "opencut-wasm";

const ticks = mediaTimeFromSeconds(1.5);
const label = formatTimecode({ ticks });
```

All exports are documented in the [TypeScript definitions](./opencut_wasm.d.ts).

## Source

Functions are implemented in Rust under [`rust/crates/`](../crates/). This package is the compiled WebAssembly output — do not edit it directly.

## Local development

This integration uses the local compiled package in `rust/wasm/pkg`. The web app and root package reference it with a file dependency. The compiled files are included so running the web editor does not require Rust.

To rebuild after editing Rust (requires Rust, the wasm32 target, and wasm-pack):

```bash
# From the repo root
bun run build:wasm

```

While you work, rebuild on changes from the repo root:

```bash
bun dev:wasm
```
