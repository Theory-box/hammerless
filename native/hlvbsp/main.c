/* hlvbsp: the program. Same arguments as vbsp.exe, plus -materials <file> (the material table the
 * Python side wrote: the game's materials live in VPKs that we don't read here) and -surfaceprops <file>
 * (the game's surface property scripts, for the physics).
 *
 *   hlvbsp [-game <dir>] -materials <file> <map>     (reads <map>.vmf, writes <map>.bsp and <map>.prt)
 */
#include "hlvbsp.h"
#include "disp.h"

int entity_num;
int verbose;
static int block_xl = BLOCKS_MIN, block_xh = BLOCKS_MAX, block_yl = BLOCKS_MIN, block_yh = BLOCKS_MAX;
static node_t *block_nodes[BLOCKS_SPACE + 2][BLOCKS_SPACE + 2];
static int brush_start, brush_end;
static char source[1024];

/* A tree of the blocks: split the larger of the x / y ranges in the middle until single blocks. */
static node_t *BlockTree(int xl, int yl, int xh, int yh) {
    if (xl == xh && yl == yh) {
        node_t *node = block_nodes[xl + BLOCK_OFFSET][yl + BLOCK_OFFSET];
        if (!node) {
            node = AllocNode();
            node->planenum = PLANENUM_LEAF;
            node->contents = 0;
        }
        return node;
    }
    node_t *node = AllocNode();
    vec3_t normal = {0, 0, 0};
    int mid;
    if (xh - xl > yh - yl) {
        mid = xl + (xh - xl) / 2 + 1;
        normal[0] = 1;
        node->planenum = FindFloatPlane(normal, (float)(mid * BLOCKS_SIZE));
        node->children[0] = BlockTree(mid, yl, xh, yh);
        node->children[1] = BlockTree(xl, yl, mid - 1, yh);
    } else {
        mid = yl + (yh - yl) / 2 + 1;
        normal[1] = 1;
        node->planenum = FindFloatPlane(normal, (float)(mid * BLOCKS_SIZE));
        node->children[0] = BlockTree(xl, mid, xh, yh);
        node->children[1] = BlockTree(xl, yl, xh, mid - 1);
    }
    return node;
}

static void ProcessBlock(int blocknum) {
    int yblock = block_yl + blocknum / (block_xh - block_xl + 1);
    int xblock = block_xl + blocknum % (block_xh - block_xl + 1);
    vec3_t mins = {(float)(xblock * BLOCKS_SIZE), (float)(yblock * BLOCKS_SIZE), MIN_COORD_INTEGER};
    vec3_t maxs = {(float)((xblock + 1) * BLOCKS_SIZE), (float)((yblock + 1) * BLOCKS_SIZE), MAX_COORD_INTEGER};
    bspbrush_t *brushes = MakeBspBrushList(brush_start, brush_end, mins, maxs, NO_DETAIL);
    if (!brushes) {
        node_t *node = AllocNode();
        node->planenum = PLANENUM_LEAF;
        node->contents = CONTENTS_SOLID;
        block_nodes[xblock + BLOCK_OFFSET][yblock + BLOCK_OFFSET] = node;
        return;
    }
    FixupAreaportalWaterBrushes(brushes);
    brushes = ChopBrushes(brushes);
    tree_t *tree = BrushBSP(brushes, mins, maxs);
    block_nodes[xblock + BLOCK_OFFSET][yblock + BLOCK_OFFSET] = tree->headnode;
    free(tree);
}

static void ProcessWorldModel(void) {
    entity_t *e = &entities[entity_num];
    brush_start = e->firstbrush;
    brush_end = brush_start + e->numbrushes;
    int leaked = 0;
    if (block_xh * BLOCKS_SIZE > map_maxs[0]) block_xh = (int)floor(map_maxs[0] / BLOCKS_SIZE);
    if ((block_xl + 1) * BLOCKS_SIZE < map_mins[0]) block_xl = (int)floor(map_mins[0] / BLOCKS_SIZE);
    if (block_yh * BLOCKS_SIZE > map_maxs[1]) block_yh = (int)floor(map_maxs[1] / BLOCKS_SIZE);
    if ((block_yl + 1) * BLOCKS_SIZE < map_mins[1]) block_yl = (int)floor(map_mins[1] / BLOCKS_SIZE);
    if (block_xl < BLOCKS_MIN) block_xl = BLOCKS_MIN;
    if (block_yl < BLOCKS_MIN) block_yl = BLOCKS_MIN;
    if (block_xh > BLOCKS_MAX) block_xh = BLOCKS_MAX;
    if (block_yh > BLOCKS_MAX) block_yh = BLOCKS_MAX;
    tree_t *tree = NULL;
    for (int optimize = 0; optimize <= 1; optimize++) {
        /* pass 2 builds again, splitting only with sides pass 1 found visible */
        int nblocks = (block_xh - block_xl + 1) * (block_yh - block_yl + 1);
        Msg("ProcessBlock_Thread: ");
        for (int b = 0; b < nblocks; b++) ProcessBlock(b);
        Msg("0...1...2...3...4...5...6...7...8...9...10 (0)\n");
        tree = AllocTree();
        tree->headnode = BlockTree(block_xl - 1, block_yl - 1, block_xh + 1, block_yh + 1);
        tree->mins[0] = (float)(block_xl * BLOCKS_SIZE);
        tree->mins[1] = (float)(block_yl * BLOCKS_SIZE);
        tree->mins[2] = map_mins[2] - 8;
        tree->maxs[0] = (float)((block_xh + 1) * BLOCKS_SIZE);
        tree->maxs[1] = (float)((block_yh + 1) * BLOCKS_SIZE);
        tree->maxs[2] = map_maxs[2] + 8;
        MakeTreePortals(tree);
        if (FloodEntities(tree)) FillOutside(tree->headnode);
        else {
            Warning("**** leaked ****\n");
            leaked = 1;
            char lin[1100];
            sprintf(lin, "%s.lin", source);
            LeakFile(tree, lin);
        }
        MarkVisibleSides(tree, brush_start, brush_end, NO_DETAIL);
        if (leaked) break;
        if (!optimize) FreeTree(tree);
    }
    FloodAreas(tree);
    RemoveAreaPortalBrushes_R(tree->headnode);
    MakeFaces(tree->headnode);
    {
        extern void AssignOccluderAreas(tree_t *tree);
        extern void Compute3DSkyboxAreas(node_t *headnode);
        AssignOccluderAreas(tree);
        Compute3DSkyboxAreas(tree->headnode);
    }
    face_t *leaffaces = MergeDetailTree(tree, brush_start, brush_end);
    Msg("FixTjuncs...\n");
    leaffaces = FixTjuncs(tree->headnode, leaffaces);
    Msg("PruneNodes...\n");
    PruneNodes(tree->headnode);
    Msg("WriteBSP...\n");
    WriteBSP(tree->headnode, leaffaces);
    Msg("done (0)\n");
    if (!leaked) {
        char prt[1100];
        sprintf(prt, "%s.prt", source);
        WritePortalFile(tree, prt);
    } else {
        char prt[1100];
        sprintf(prt, "%s.prt", source);
        remove(prt);
    }
}

static void ProcessSubModel(void) {
    entity_t *e = &entities[entity_num];
    int start = e->firstbrush, end = start + e->numbrushes;
    vec3_t mins = {MIN_COORD_INTEGER, MIN_COORD_INTEGER, MIN_COORD_INTEGER};
    vec3_t maxs = {MAX_COORD_INTEGER, MAX_COORD_INTEGER, MAX_COORD_INTEGER};
    bspbrush_t *list = MakeBspBrushList(start, end, mins, maxs, FULL_DETAIL);
    list = ChopBrushes(list);
    tree_t *tree = BrushBSP(list, mins, maxs);
    if (tree->headnode->planenum == PLANENUM_LEAF)
        Error("bmodel %d has no head node (class '%s', targetname '%s')", entity_num,
              ValueForKey(e, "classname"), ValueForKey(e, "targetname"));
    MakeTreePortals(tree);
    MarkVisibleSides(tree, start, end, FULL_DETAIL);
    MakeFaces(tree->headnode);
    FixTjuncs(tree->headnode, NULL);
    WriteBSP(tree->headnode, NULL);
}

int main(int argc, char **argv) {
    const char *matfile = NULL;
    int i;
    setvbuf(stdout, NULL, _IONBF, 0);
    Msg("Hammerless hlvbsp\n");
    for (i = 1; i < argc; i++) {
        if (!_stricmp(argv[i], "-game") && i + 1 < argc) {
            extern const char *g_gamedir;
            g_gamedir = argv[++i];
        } else if (!_stricmp(argv[i], "-cubemaps") && i + 1 < argc) {
            extern const char *g_cubemap_file;
            g_cubemap_file = argv[++i];
        } else if (!_stricmp(argv[i], "-detail") && i + 1 < argc) {
            extern const char *g_detail_file;
            g_detail_file = argv[++i];
        } else if (!_stricmp(argv[i], "-props") && i + 1 < argc) {
            extern const char *g_props_file;
            g_props_file = argv[++i];
        } else if (!_stricmp(argv[i], "-surfaceprops") && i + 1 < argc) {
            extern const char *g_surfaceprops_file;
            g_surfaceprops_file = argv[++i];
        }
        else if (!_stricmp(argv[i], "-materials") && i + 1 < argc) matfile = argv[++i];
        else if (!_stricmp(argv[i], "-v") || !_stricmp(argv[i], "-verbose")) verbose = 1;
        else if (!_stricmp(argv[i], "-threads") && i + 1 < argc) i++;
        else if (argv[i][0] == '-') {
            /* other vbsp options: their defaults are what we do */
        } else break;
    }
    if (i != argc - 1) Error("usage: hlvbsp [-game <dir>] -materials <file> <map>");
    strncpy(source, argv[i], sizeof(source) - 1);
    size_t n = strlen(source);
    if (n > 4 && !_stricmp(source + n - 4, ".vmf")) source[n - 4] = 0;
    if (!matfile) Error("hlvbsp needs -materials <file> (written by Hammerless)");
    LoadMaterials(matfile);
    char path[1100];
    {
        const char *b = source, *s;
        for (s = source; *s; s++)
            if (*s == '/' || *s == 92) b = s + 1;          /* (92: backslash) */
        g_mapbase = copystring(b);
    }
    sprintf(path, "%s.vmf", source);
    LoadMapFile(path);
    {
        extern void Cubemap_FixupBrushSidesMaterials(void), Cubemap_AttachDefaultCubemapToSpecularSides(void),
            Cubemap_AddUnreferencedCubemaps(void);
        Cubemap_FixupBrushSidesMaterials();
        Cubemap_AttachDefaultCubemapToSpecularSides();
        Cubemap_AddUnreferencedCubemaps();
    }
    SetModelNumbers();
    SetLightStyles();
    BeginBSPFile();
    {
        extern void MarkNoDynamicShadowSides(void);
        MarkNoDynamicShadowSides();
    }
    EmitInitialDispInfos();
    {
        extern void EmitOccluderBrushes(void);
        EmitOccluderBrushes();      /* (their brushes are taken out of the models below) */
    }
    {
        extern const char *g_linpath;
        static char linpath[1100];
        sprintf(linpath, "%s.lin", source);
        g_linpath = linpath;
    }
    for (entity_num = 0; entity_num < num_entities; ++entity_num) {
        if (!entities[entity_num].numbrushes) continue;
        BeginModel();
        if (entity_num == 0) ProcessWorldModel();
        else ProcessSubModel();
        EndModel();
    }
    sprintf(path, "%s.bsp", source);
    EndBSPFile(path);
    return 0;
}
