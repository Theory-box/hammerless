# Isolated hardware preview worker

Experimental Vulkan 1.2 ray-query backend for the L4D2 viewport preview. This is a standalone process, not a Blender DLL. The Python owner must close or terminate it when its render engine is released.

Build using `build.py --glslang PATH_TO_GLSLANG --zig-path PATH_TO_PIP_TARGET`. The pip target must contain `ziglang/zig.exe` (tested compiler: Zig 0.13.0). The build compiles `preview.comp`, embeds SPIR-V, and writes `hammerless/core/hlpreview.exe`. No game or Blender process is started by the build.

## Protocol

All integers and floats are little endian. Each request starts with four uint32 values: magic `0x48504c31`, operation, count, reserved zero.

- 1: replace scene. `count*9` float32 triangle coordinates, followed by `count*8` float32 metadata: reflectivity RGB, receives-bounce flag, geometric world normal XYZ, reserved. World normals must retain the preview instance-normal convention, including mirrored transforms.
- 2: replace lights. `count*20` float32 values using the existing preview light rows.
- 3: shade receivers. First `<4I4f>` parameters: rays, depth, seed, flags, unit scale, sky RGB. Then `count*8` float32 values: already-offset position XYZ, coverage, normal XYZ, reserved. Flag bit 0 enables sampled sky; bit 1 sampled area lights. Zero means full direct evaluation. The response includes `count*4` float32 irradiance/255 plus coverage alpha. Uncovered inputs produce zero.
- 9: quit, with count zero. EOF also exits.

Responses begin with magic, status zero, count, reserved zero. Scene/light/quit responses have no data. Errors go to stderr and exit nonzero; stdout is binary only. A truncated response is failure, never a usable partial image.

The worker retains acceleration structures until scene replacement. Light changes do not rebuild them. Dispatches are subdivided by estimated light/ray/depth work. Input counts, finite values, parameter ranges, device storage limits, and finite outputs are checked. Process isolation and bounded dispatch do not substitute for owner-side cancellation/timeouts.

## Parity scope

Direct and bounded diffuse bounce equations follow the existing preview shader. Sky directions use exactly its float32 Halton sequence. Hardware intersections use a 1e-7 minimum ray distance; the software triangle test uses strict positive distance. Boundary cases therefore require image comparisons, not a claim of bit-identical results. Opaque triangles only; no new alpha-material support is implied. This backend does not implement an irradiance approximation grid or export baked lightmaps.

## Diagnostic dispatch controls

Default estimated dispatch budget is 128 million light-loop units, capped at 4096 receivers per dispatch. A single receiver exceeding two million units is rejected. These are safety/occupancy bounds, not a guaranteed GPU time limit. `HL_PREVIEW_DISPATCH_BUDGET` can reduce the dispatch budget to values from 2,000,000 through 128,000,000 for comparisons; it cannot increase the production cap. `HL_PREVIEW_PROFILE=1` writes count, chunk size, dispatch time, longest dispatch, and total request time to stderr. Timing includes synchronous GPU completion; total also includes pipe IO and host work.
