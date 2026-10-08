/* The map's visibility (what each cluster can see) and leaves, as vrad reads them. */
#include "hlvrad.h"

dleaf_t *dleafs; int numleafs;
dnode_t *dnodes; int numnodes;
unsigned short *dleaffaces; int numleaffaces;
dbrush_t *dbrushes; int numbrushes;
dbrushside_t *dbrushsides; int numbrushsides;
unsigned short *dleafbrushes; int numleafbrushes;
int numclusters;
static const unsigned char *visdata;
static int visdatasize;

void MapVis(void) {
    dleafs = (dleaf_t *)lumps[LUMP_LEAFS].data; numleafs = lumps[LUMP_LEAFS].len / sizeof(dleaf_t);
    dnodes = (dnode_t *)lumps[LUMP_NODES].data; numnodes = lumps[LUMP_NODES].len / sizeof(dnode_t);
    dleaffaces = (unsigned short *)lumps[LUMP_LEAFFACES].data; numleaffaces = lumps[LUMP_LEAFFACES].len / 2;
    dbrushes = (dbrush_t *)lumps[LUMP_BRUSHES].data; numbrushes = lumps[LUMP_BRUSHES].len / sizeof(dbrush_t);
    dbrushsides = (dbrushside_t *)lumps[LUMP_BRUSHSIDES].data;
    numbrushsides = lumps[LUMP_BRUSHSIDES].len / sizeof(dbrushside_t);
    dleafbrushes = (unsigned short *)lumps[LUMP_LEAFBRUSHES].data; numleafbrushes = lumps[LUMP_LEAFBRUSHES].len / 2;
    visdata = lumps[LUMP_VISIBILITY].data;
    visdatasize = lumps[LUMP_VISIBILITY].len;
    numclusters = 0;
    if (visdatasize >= 4) memcpy(&numclusters, visdata, 4);
}

int VisRowBytes(void) { return numclusters / 8 + 1; }
int HaveVis(void) { return visdatasize > 0; }

static void DecompressVis(const unsigned char *in, unsigned char *out) {
    int row = (numclusters + 7) >> 3;
    unsigned char *start = out;
    do {
        if (*in) {
            *out++ = *in++;
            continue;
        }
        int c = in[1];
        if (!c) Error("DecompressVis: 0 repeat");
        in += 2;
        if ((out - start) + c > row) c = row - (int)(out - start);
        while (c--) *out++ = 0;
    } while (out - start < row);
}

/* The clusters a cluster can see (everything when there is no vis or the point is in a wall). */
void GetClusterPVS(int cluster, unsigned char *pvs) {
    if (!visdatasize || cluster < 0) {
        memset(pvs, 255, (numclusters + 7) / 8);
        return;
    }
    int ofs;
    memcpy(&ofs, visdata + 4 + 8 * cluster, 4);       /* bitofs[cluster][DVIS_PVS] */
    if (ofs == -1) Error("visofs == -1");
    DecompressVis(visdata + ofs, pvs);
}

/* (a cluster of -1 counts as seen: vrad would rather light a sample than leave it black) */
int PVSCheck(const unsigned char *pvs, int cluster) {
    if (cluster >= 0) return pvs[cluster >> 3] & (1 << (cluster & 7));
    return 1;
}

int LeafFlags(int leaf) { return ((unsigned short)dleafs[leaf].area_flags) >> 9; }
void SetLeafFlags(int leaf, int flags) {
    dleafs[leaf].area_flags = (short)((dleafs[leaf].area_flags & 0x1ff) | (flags << 9));
}
