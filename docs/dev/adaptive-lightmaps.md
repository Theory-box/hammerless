# Adaptive lightmaps: first visibility implementation

Experimental, **off by default**. Enable Lighting > Adaptive Lightmaps. The user's
existing face/material scale remains the finest scale. Coarsest Scale defaults to
64. Disable Also Reduce Distant Surfaces to test unseen surfaces alone.

This is an export-time receiver-resolution optimizer, not a high-resolution bake
followed by compression. It changes `lightmapscale` on static world/detail VMF
sides before VBSP, so subdivision, VRAD allocations and the game's final layout
agree. No custom shader, geometry splitting pass, or change to texture UVs.

## Existing work that is already skipped

- `native/hlvbsp/csg.c:ChopBrushes` carves overlapping solid world brushes.
  `faces.c:MakeFaces_r/FaceFromPortal` builds exposed boundaries; solid/solid
  internal boundaries do not become ordinary receiving faces.
- `detail.c:MergeDetailTree` chops detail brushes, finds visible sides (including
  clipping shared planes), and filters faces through the world tree. Separate
  brush entity models are not globally unioned: overlap involving those is not
  proof that a face is permanently invisible (doors can move).
- `native/hlvrad/layout.c` already excludes sky/unlit/tool/non-rendering entity
  receivers. Non-rendering faces get no lightmap allocation or bounce patches.
- Ordinary textured exterior faces inside the automatic sky shell can survive
  those steps and receive full-resolution maps. That's the target here.
- GPU GI work, supersampling, bump-lightmap layers and switchable-light styles
  are not disabled. They contribute to actual appearance. Live Light Mixing also
  requests extra saved lighting work; it is not a free optimization to remove it.

## How the first version decides

`core/lightvisibility.py` analyzes the exported VMF, never the editable Blender
scene or a stale BSP/nav cache. It builds continuous viewing boxes above all
upward brush surfaces, including unreachable ledges, plus prop collision/model
bounds, ladders and spawn landmarks. Eye space extends 24–144 Hammer units above
support surfaces and 32 sideways. Sky shell faces are not walkable supports.
This deliberately includes more than the nav mesh. It cannot optimize as
aggressively as a later reachable-space analysis, but doesn't require generating
another nav mesh or miss a position merely because sparse rays skipped it.

Touching coplanar face bounds are grouped. Disconnected islands and opposite
normals stay separate. A group's finest required spacing protects its other
members; existing explicit coarser scales remain intact. Bounding boxes may join
extra faces around holes, conservatively keeping more detail. No nonlinear UVs.

A surface stays detailed if any possible nearby viewing region faces it. Farther
regions can be rejected only when an opaque stationary convex brush provably
blocks **every segment between the entire viewing box and receiver box**. This
uses halfspace bounds: find a shared segment parameter where the entire bundle
is inside the blocker. A centre ray being blocked is insufficient. Alpha-tested,
translucent, unknown and conditional/proxy materials are not used as occluders;
receivers with unknown/non-opaque materials keep their original scales when
material content is available. Doors/props never serve as permanent occluders.

Distance is the lower bound between boxes. Default spacing doubles at 1024,
2048, etc. units; it never exceeds the configured coarsest spacing unless an
existing face was already coarser. Entirely back-facing or proven occluded groups
use that coarsest spacing. A bounded occlusion budget fails open (possible sight),
not closed (hidden). Missing support data, moving platforms, teleports, unexpanded
instances and displacement viewing regions retain the map's original scales.

## Lighting preservation and limits

Nothing becomes nodraw and no geometry/collision/shadow material is removed.
`_hl_transport_lightmap_scale` on worldspawn records the original finest eligible
scale. Native VRAD uses it as a floor on radiosity patch density, so coarsening
receivers doesn't automatically make the transport mesh equally coarse. This
can preserve substantial bounce cost; it is not a promise of identical bounce
values, since face subdivision and receiving samples still change. Fine material
overrides can make the global transport floor conservative/expensive.

The analysis does not know arbitrary runtime scripts or every physics-prop jump
trick. Disable the option for unusual moving-world gameplay. Future work can use
explicit playable volumes and verified nav coverage. Thin gaps are conservatively
left visible; a surface hidden jointly by several blockers may remain detailed.

Summary sample savings are geometric estimates over **eligible sides before BSP
clipping**, not measured file-size or bake-time savings. Internal sides may later
be removed by CSG. Do not present these estimates as compiled savings.

This does not fix malformed texture/lightmap axes or guarantee that every scale-4
map fits Source limits. The separate bad-surface-extents issue still needs its
own diagnosis. Automatic Valve fallback stays disabled.

## Validation

- Headless unit checks cover underside vs upper surface, lower walkways, sky
  shell exclusion, whole-region occlusion, thin-gap uncertainty, behind-target
  blockers, connected groups, scale bounds, unsupported maps, opacity, and the
  disabled-by-default export path.
- Native lightmapper rebuilt; CLI starts. No game or user map bake performed.
- Read-only analysis of the current exported `my_map.vmf` took about two seconds;
  80 of 1,241 eligible sides were coarsened, including 69 classified unseen.
  Estimated eligible-side receiver sample reduction was 12%, before BSP CSG.
- User should restart Blender, enable the option, start with unseen-only and
  their usual working scale, then Build & Play. Turning it off restores the
  exported original scales on the next build. No .blend settings are changed by
  installation; no releases are updated until this experimental branch is tested.

## Resolution overlay

Lighting > Adaptive Lightmaps > Show Baked Resolution loads the last compiled
BSP and selects the new Resolution display mode. Also available in the baked
lighting display modes, independently of whether adaptive scaling is enabled.
Blue/cyan/green/orange/red/purple show progressively coarser spacing; an
antialiased grid shows actual lightmap cells. The legend and measured spacing
range appear in the baked-lighting panel. X-Ray is supported; props are omitted
because their baked lighting is per vertex, not this brush lightmap grid.

Flat-face spacing comes from the inverse compiled lightmap axes restricted to
the face plane. It must not be estimated from float32 atlas UV derivatives on
tiny triangles: those produce false extreme density values. Displacements use
their local triangle mapping. Un-atlased luxel coordinates avoid atlas precision
loss in the grid. Degenerate mappings are grey. Existing compile-following,
scene-stale warnings, clear and unit-scale handling apply to the overlay.

Colours show final spacing, not the optimizer's reason or a before/after diff.
This works on existing bakes without rebuilding. It adds no Blender objects or
materials and changes no bake setting. Five numerical checks and reading the
user's existing BSP passed; viewport appearance remains for user testing.
