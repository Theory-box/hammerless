/* hlvvis -gpu (hlvvis_gpu.c): 0 from any call = do it on the CPU */
#pragma once
#include <stdint.h>

typedef struct { float plane[4]; uint32_t first, count, pad0, pad1; } vgpu_portal_t;   /* (std430 Portal) */

int vgpu_init(void);
const char *vgpu_name(void);
/* the rough pass's "in front" bits: row i, word w (bit j & 31); store() gets each row as it comes back */
int vgpu_front(int npd, const float *pts, int npts, const vgpu_portal_t *portals, float eps_up, int w32,
               void (*store)(int row, const uint32_t *words, void *ctx), void *ctx);
/* (self-test) the GPU's doubles against the CPU's: mismatches, -1 if it couldn't run */
int vgpu_fp64_selftest(int n);

/* -jacobi's expansion steps (hlvvis_shaders/expand.comp): the portals once, then a round's items */
#define VGPU_MAXW 64       /* points per output winding */
#define VGPU_SNAPCAP 16    /* points per snapshot window (the -jacobi hull cap) */
typedef struct { float plane[4], origin[4]; uint32_t first, count, pad0, pad1; } vgpu_eportal_t;   /* origin[3] = radius */
typedef struct { uint32_t base, snap, q, pad; } vgpu_item_t;
int vgpu_expand_setup(int npd, const float *pts, int npts, const vgpu_eportal_t *portals, float eps_up, float len_lo,
                      int robust, int maxchop);
/* items: (base, snapshot index, q); snap_pts: per snapshot S then W, VGPU_SNAPCAP points each (vec4); snap_n: per snapshot
   S count, W count. Out per item: src and pass windings (VGPU_MAXW vec4 each) and (status, src n, pass n, 0) */
int vgpu_expand(int nitems, const vgpu_item_t *items, int nsnap, const float *snap_pts, const uint32_t *snap_n,
                float *out_src, float *out_pass, uint32_t *out_n);

/* (self-test) merges recorded from a CPU run, redone on the GPU (hlvvis_shaders/mergetest.comp + merge_lib.glsl) */
typedef struct { int32_t flags[4]; float bn[4], pn[4]; int32_t counts[4]; float S[64], W[64], src[256], pas[256]; } vgpu_mrec_t;
typedef struct { int32_t flags[4]; int32_t counts[4]; float S[64], W[64]; } vgpu_mres_t;
int vgpu_merge_test(int n, const vgpu_mrec_t *recs, vgpu_mres_t *out, const double *cap_dir, int cap);
