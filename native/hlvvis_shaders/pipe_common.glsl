// pipe_common.glsl: what every kernel of hlvvis -jacobi's GPU pipeline shares (pipe_*.comp, driven by hlvvis_gpu.c).
// Many base portals' flows run at once, one per slot; each round of every live slot is a few dispatches:
//   pipe_compact  dirty portals -> the slot's next frontier (in portal order, as the CPU's sort), liveness
//   pipe_scan1    frontier offsets of all slots (pairs = (slot, frontier portal))
//   pipe_snap     per pair: the expansion count / open fully rule, the snapshot, the number of next portals
//   pipe_scan2    item offsets (items = (pair, next portal))
//   pipe_expand   per item: the expansion step (expand_lib.glsl), the result linked to its (slot, portal) list
//   pipe_args     dispatch size of the merge
//   pipe_merge    per (slot, portal) reached: its windows merged in item order (merge_core.glsl) = the CPU's order
// and pipe_refill starts new base portals in free slots (their first openings come from the CPU). The CPU's
// -jacobi is the reference: the results are the same to the bit.
#extension GL_EXT_shader_explicit_arithmetic_types_float64 : require
#extension GL_EXT_spirv_intrinsics : require
spirv_execution_mode(4462, 32);
spirv_execution_mode(4462, 64);
spirv_execution_mode(4461, 32);
spirv_execution_mode(4461, 64);

#define MAXW 64
#define SNAP 16                 // points per stored window (the hull cap's maximum)
#define EMPTY 0xffffffffu

layout(push_constant) uniform PC {
    uint nslots; uint fmax; uint pw32; uint npd;
    uint round; uint item_start; float eps_up; float len_lo;
    uint robust; uint maxchop; uint hullcap; uint nrefill;
    uint candcap; uint ptcap; uint saturate; uint maxarr;
} pc;

struct Portal { vec4 plane; vec4 origin; uint first; uint count; uint ccw; uint leaf; };   // origin.w = radius
struct Slot { uint base; uint gen; uint floodn; uint seen; uint nfront; uint live; uint slotoff; uint off; };   // off: its entries
// per entry (a slot's entries: off + rank in its base's flood): sth = (gen, grows, opened, counts: S | W << 8 |
// snapshot S << 16 | snapshot W << 24); the windows S, W and the snapshot PS, PW the round expands from
struct Win16 { vec4 p[SNAP]; };

layout(std430, binding = 0) readonly buffer Points { vec4 pts[]; };
layout(std430, binding = 1) readonly buffer Portals { Portal portals[]; };
layout(std430, binding = 2) readonly buffer Leaves { uvec2 leaves[]; };        // (first, count) into lflist
layout(std430, binding = 3) readonly buffer LfList { uint lflist[]; };
// binding 4: the hull cap's directions (merge_core.glsl)
layout(std430, binding = 5) coherent buffer Slots { Slot slots[]; };
layout(std430, binding = 6) buffer Flood { uint flood[]; };                    // per slot: pw32 words
layout(std430, binding = 7) coherent buffer Prefix { uint prefix[]; };         // per slot: set bits before each word
layout(std430, binding = 8) coherent buffer Vis { uint vis[]; };
layout(std430, binding = 9) coherent buffer Dirty { uint dirty[]; };
layout(std430, binding = 10) buffer StateH { ivec4 sth[]; };                     // per entry
layout(std430, binding = 11) buffer PerRank { uvec4 pr[]; };                    // per entry: (list head, arrivals, round stamp, portal)
layout(std430, binding = 12) buffer Front { uint front[]; };                    // per entry (a slot's frontier from its off)
layout(std430, binding = 13) buffer Pairs { uvec4 pairs[]; };                   // (items, item offset, slot, rank)
layout(std430, binding = 14) buffer Touched { uvec2 touched[]; };               // (entry, slot)
layout(std430, binding = 15) buffer Cands { uvec4 cands[]; };                   // (next, item, point offset, src n | pass n << 16)
layout(std430, binding = 16) buffer CPts { vec4 cpts[]; };
layout(std430, binding = 17) coherent buffer Counters { uint cnt[]; };
layout(std430, binding = 18) readonly buffer Refill { uint refill[]; };
layout(std430, binding = 19) buffer StateS { Win16 stS[]; };
layout(std430, binding = 20) buffer StateW { Win16 stW[]; };
layout(std430, binding = 21) buffer StatePS { Win16 stPS[]; };
layout(std430, binding = 22) buffer StatePW { Win16 stPW[]; };

#define C_N 0          // pairs this round
#define C_T 1          // items this round
#define C_CAND 2       // candidates used (this pass)
#define C_PTS 3        // candidate points used (this pass)
#define C_FAIL 4       // first item that found no room (this pass), EMPTY if none
#define C_TOUCH 5      // (slot, portal) lists started (this pass)
#define C_ARGS1 8      // dispatch sizes: snap
#define C_ARGS3 12     //                 expand
#define C_ARGS4 16     //                 merge

// (kernels of 64 invocations, dispatched as set_args sizes them)
uint gid() { return (gl_WorkGroupID.y * gl_NumWorkGroups.x + gl_WorkGroupID.x) * 64u + gl_LocalInvocationID.x; }

// groups for n invocations of 64, as (x, y) for an indirect dispatch (x at most 32768)
void set_args(uint at, uint n) {
    uint g = (n + 63u) / 64u;
    cnt[at] = g < 32768u ? g : 32768u;
    cnt[at + 1u] = g < 32768u ? 1u : (g + 32767u) / 32768u;
    cnt[at + 2u] = 1u;
}

bool in_flood(uint s, uint q) { return ((flood[s * pc.pw32 + (q >> 5)] >> (q & 31u)) & 1u) != 0u; }
uint rank_of(uint s, uint q) {
    uint w = s * pc.pw32 + (q >> 5);
    return prefix[w] + uint(bitCount(flood[w] & ((1u << (q & 31u)) - 1u)));
}

void load_win(uint p, out vec3 w[MAXW], out int n) {
    n = int(portals[p].count);
    for (int i = 0; i < n; i++) w[i] = pts[portals[p].first + uint(i)].xyz;
}
