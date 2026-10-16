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
