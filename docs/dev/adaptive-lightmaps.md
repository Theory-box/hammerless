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

## Navigation-based viewing regions

The earlier geometry-top heuristic preserved unreachable exterior sides. Current
viewing regions use `navpredict.predict` on the current exported VMF, including
manual nav regions, climb settings and prop collision. No existing .nav is read
or overwritten; no game is launched. Automatic extra rooftop seeds are disabled.
Missing native nav support or an empty prediction leaves original scales intact.

Each nav area becomes a continuous viewing box covering all its corner heights,
24–144 Hammer units above the floor. `Nav Edge Leeway` adds horizontal padding
(default 16, adjustable 0–128). Ladder transit envelopes are included too.
This covers crouching, standing and jumping near the nav; it does not prove every
physics trick or scripted viewpoint is covered. Increase leeway for unusual
routes or disable adaptive scaling when navigation does not represent play space.

Touching coplanar face bounds share a resolution requirement; disconnected islands
and opposite normals remain separate. Existing coarser overrides remain intact.
Near faces now undergo the same occlusion checks as distant ones: proximity by
itself is not evidence of visibility. An opaque stationary convex brush can
reject a viewing region only if it blocks every segment from the entire region
to the entire receiver bounds. Vectorized halfspace interval tests implement the
same proof as the scalar reference. Unknown/transparent materials, doors and
props never act as permanent occluders. `%keywords` is harmless browser metadata
and is allowed; actual compile flags and unsupported conditional materials stay
conservative.

Distance uses a lower bound between boxes. Default spacing doubles at 1024,
2048, etc. units. Back-facing/proven occluded surfaces use the coarsest spacing.
The bounded occlusion budget fails open, retaining possible visibility. Moving
platforms, teleports, unexpanded instances and displacements currently leave the
map unchanged. No additional geometry splitting or texture-UV changes occur.

On the user's already optimized exported map, the new pass found 88 additional
sides to coarsen (73 unseen). Total analysis took about ten seconds, including
fresh nav prediction (~1.5 seconds). This is export overhead, not a bake; actual
bake-time or BSP savings have not been measured. Sixteen focused checks cover
nav padding, missing-nav fallback, close occlusion, scalar/vector parity, material
handling, and the existing resolution safety rules.

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
explicit playable volumes to supplement navigation coverage. Thin gaps are conservatively
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
