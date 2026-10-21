# Non-rendering volumes and baked light

Hammerless VRAD classifies permanently non-rendering entity models and tool surfaces before face
sample generation. They receive no samples, styles, lightmap offsets or radiosity patches. The test
is per face/model; shared materials on ordinary visible brushes are not modified. Sky patches are
preserved. World brush collision/shadow data is unchanged, so toolsblocklight remains a shadow caster.
This changes Hammerless VRAD behavior; an explicitly selected external Valve VRAD is not modified.

The same classification excludes old lightmapped logical volumes from the preview and Cycles receiver
list. Native classification is mirrored in core/lightmap.py; keep both lists synchronized. Newly
hidden faces in pre-existing BSPs need a full rebake to remove their stored light data. The lighting
receiver revision invalidates old Hammerless bakes without rebuilding unchanged geometry.

Validation: CPU and GPU compile fixtures give zero lit fog/trigger faces while an ordinary func_brush
using the same visible material retains six lit faces. Geometry/collision lumps remain identical.
An unchanged toolsblocklight reduces the sampled floor illumination from 0.29085 to 0.10131, proving
its shadow survives. The hotel rebake reduces lit fog-volume faces from 416 to zero; all those faces
have lightofs=-1 and unused styles. The preview previously drew 1,084 fog-volume triangles, now zero.

Run `python tests/compile/nonrendering_lighting.py <scratch-directory>` with L4D2 installed.
