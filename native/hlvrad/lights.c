/* Lights (vrad's CreateDirectLights and friends). So far: the sky's leaves.
 *
 * With a light_environment, vrad works out again which leaves see the sky (vbsp's guess also marks
 * solid leaves): a leaf holding a sky face sees it; another non-solid leaf sees it when its cluster can
 * see one of those (3D sky wins over 2D). The sky light reaches the clusters of the sky leaves. */
#include "hlvrad.h"

#define LEAF_FLAGS_SKY 0x01
#define LEAF_FLAGS_RADIAL 0x02
#define LEAF_FLAGS_SKY2D 0x04
#define CONTENTS_SOLID 0x1

unsigned char *g_SkyPVS;          /* clusters the sky light (and sky ambient) can reach */

static void MergeSkyVis(int cluster) {
    int n = VisRowBytes();
    unsigned char *pvs = xalloc(n + 1);
    GetClusterPVS(cluster, pvs);
    if (!g_SkyPVS) g_SkyPVS = xalloc(n + 1);
    for (int i = 0; i < n; i++) g_SkyPVS[i] |= pvs[i];
    free(pvs);
}

static void BuildVisForLightEnvironment(void) {
    for (int leaf = 0; leaf < numleafs; leaf++) {
        int flags = LeafFlags(leaf) & ~(LEAF_FLAGS_SKY | LEAF_FLAGS_SKY2D);
        for (int k = 0; k < dleafs[leaf].numleaffaces; k++) {
            int face = dleaffaces[dleafs[leaf].firstleafface + k];
            int tf = texinfo[g_pFaces[face].texinfo].flags;
            if (tf & SURF_SKY) {
                flags |= tf & SURF_SKY2D ? LEAF_FLAGS_SKY2D : LEAF_FLAGS_SKY;
                MergeSkyVis(dleafs[leaf].cluster);
                break;
            }
        }
        SetLeafFlags(leaf, flags);
    }
    /* leaves that see a sky leaf (set afterwards, so the sky doesn't spread from leaf to leaf) */
    int bytes = (numleafs >> 3) + 1;
    unsigned char *sky3d = xalloc(bytes), *sky2d = xalloc(bytes), *pvs = xalloc(VisRowBytes() + 1);
    for (int leaf = 0; leaf < numleafs; leaf++) {
        if (LeafFlags(leaf) & LEAF_FLAGS_SKY) continue;
        if (dleafs[leaf].contents & CONTENTS_SOLID) continue;
        GetClusterPVS(dleafs[leaf].cluster, pvs);
        for (int other = 0; other < numleafs; other++) {
            if (other == leaf) continue;
            int of = LeafFlags(other);
            if (!(of & (LEAF_FLAGS_SKY | LEAF_FLAGS_SKY2D))) continue;
            if (!PVSCheck(pvs, dleafs[other].cluster)) continue;
            if (of & LEAF_FLAGS_SKY2D) sky2d[leaf >> 3] |= 1 << (leaf & 7);
            if (of & LEAF_FLAGS_SKY) {
                sky3d[leaf >> 3] |= 1 << (leaf & 7);
                break;
            }
        }
    }
    for (int leaf = 0; leaf < numleafs; leaf++) {
        int flags = LeafFlags(leaf);
        if (flags & LEAF_FLAGS_SKY) continue;
        if (dleafs[leaf].contents & CONTENTS_SOLID) continue;
        if (sky2d[leaf >> 3] & (1 << (leaf & 7))) flags |= LEAF_FLAGS_SKY2D;
        if (sky3d[leaf >> 3] & (1 << (leaf & 7))) flags = (flags | LEAF_FLAGS_SKY) & ~LEAF_FLAGS_SKY2D;
        else if (flags & LEAF_FLAGS_RADIAL) {
            /* TODO: vrad traces rays to find the sky from leaves radial vis cut short */
        }
        SetLeafFlags(leaf, flags);
    }
    free(sky3d);
    free(sky2d);
    free(pvs);
}

void CreateDirectLights(void) {
    for (int i = 0; i < num_entities; i++) {
        if (!strcmp(ValueForKey(&entities[i], "classname"), "light_environment")) {
            BuildVisForLightEnvironment();
            break;                       /* (only the first light_environment is the sky) */
        }
    }
}
