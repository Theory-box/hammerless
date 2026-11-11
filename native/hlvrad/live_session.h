/* -live <name>: a lighting session that stays loaded (part of direct.c: BuildIndirectGPU hands over to LiveSession once
 * its points are made). The map's geometry is fixed; light entities are edited by commands on stdin, and after each
 * the lightmaps are brought up to date in steps, each step's result put in shared memory for the owner to show:
 *
 *   1. the lights that changed (the old and new light lists compared) are gathered alone, without supersampling, the
 *      old ones' light taken off the samples' direct light and the new ones' added; the bounced light stays;
 *   2. bounce passes go on from the lightmaps as they are (the passes' rays are the same every time, so they settle
 *      where a bake's passes do, within the passes' tolerance);
 *   3. when no command waits, the direct light is made again as a bake makes it (every light, supersampled), which
 *      clears what step 1's shortcut left at shadow edges, and the passes settle again.
 *
 * The passes of step 2 come in two kinds. A change pass gathers only what the lightmaps changed by since the bounced
 * light was last gathered (new - then, a signed grid through gi.comp's layer channel) with a few rays, and adds that
 * to the bounced light: cheap, and its noise is a fraction of the change, not of the light. When change passes have
 * nothing left to add, a full pass (every ray, the lightmaps themselves, as a bake's) replaces the bounced light of
 * the points the change passes touched (the others' is a full pass's already); if that moves a face beyond tolerance
 * the change passes go on from there, else the lighting has settled. Change passes that add little after a full pass
 * (under two tolerances a face in all) need no full pass after them: their noise is a fraction of that.
 *
 * COLD does step 3 and then the bounce from nothing, as a bake: its lightmaps are a bake's to the byte.
 *
 * Commands (a line each):
 *   SET <entity> <key> <value>   an entity's key (the value: the rest of the line)
 *   ADD                          a new entity (answers HLLIVE_ENT <index>); SET its classname, origin, _light...
 *   DEL <entity>                 an entity no longer a light
 *   GO [serial]                  light the changes so far (the serial comes back with each result)
 *   COLD [serial]                the changes so far, lit as a bake from nothing (one result, when done)
 *   RAYS <n>                     bounce rays a sample from now on
 *   QUIT
 * Results: "HLLIVE_UPDATE gen serial state pass seconds" on stdout once the shared memory holds them. Shared memory:
 * 16 uint32 (magic HLLV, version, generation (odd while being written), faces, bytes a face, lightmap bytes, state,
 * serial, pass, rays, capacity, ...), the faces (dface_t each: their lightofs and styles), the lightmap bytes.
 * States: 0 rough direct light (step 1), 1 exact direct light, bounce settling, 2 settled, 3 a bake's own (COLD). */

int g_live;
const char *g_liveName;

typedef struct {
    gpugroup_t *groups;
    int (*where)[2];
    int n;
    int *k0;
    vec3_t *(*direct)[NUM_BUMP_VECTS + 1];
    float *out;
    double *faceChange;
    unsigned char *dirty;               /* per face: its samples changed, its luxels are to be made again */
    float *pend;                        /* per face: how much (in the passes' tolerances) since they were made */
    float *since;                       /* per face: how much change passes added since the last full pass */
    unsigned char *gtouch;              /* per group: change passes moved its bounced light since its full pass */
    int fullAll;                        /* the next full pass gathers every group (nothing to go by) */
    unsigned char *gridChanged;         /* per face: its luxels changed since the bounced light was gathered */
    int *pick;
    gpugroup_t *asked;
    unsigned char *stale;               /* per face: its direct light has step 1's shortcut in it */
    int nstale, allStale;
    unsigned char *ref;                 /* the lightmaps the bounced light was gathered from (NULL: none, a bake's start) */
    int refBytes;
    int *gridofs;                       /* per face: where its luxels are in the change grid (-1: not lit) */
    size_t gridluxels;
    float *grid, *lout;                 /* the change grid (rgb a luxel); a change pass's answers (48 floats a group) */
    int deltaRays, debug;
    double tmark;
    int *todo, ntodo;
    unsigned int *shared;
    size_t capacity;
    int serial, rays, pass;
    double t0;                          /* (when the last command was taken) */
} live_t;

static live_t *g_liveNow;
static void LiveSetRef(live_t *L, int have);
/* (HLLIVEDBG: each step's time) */
static void LiveMark(live_t *L, const char *what) {
    double t = Seconds();
    if (L->debug && what) Msg("  [live] %-24s %.3f s\n", what, t - L->tmark);
    L->tmark = t;
}
static void LiveTodoWork(int i, int thread) { (void)thread; FinalLightFace(g_liveNow->todo[i]); }

/* the luxels of the faces whose samples changed, and of the faces that blend those samples into theirs (neighbours
 * over smoothed edges: a face's luxels blend its own samples and its neighbours', displacements too). least: only
 * the faces whose samples changed that much since their luxels were made (change passes: a face a pass moves by a
 * fraction of what a lightmap byte can hold waits, and is made with the rest by the next call with least 0) */
#define LIVE_DUE(L, f, least) ((L)->dirty[f] && ((least) <= 0 || (L)->pend[f] >= (least)))
static void LiveFinalLeast(live_t *L, float least) {
    int any = 0;
    for (int f = 0; f < numfaces && !any; f++) any = LIVE_DUE(L, f, least);
    if (!any) return;
    L->ntodo = 0;
    for (int f = 0; f < numfaces; f++) {
        int need = LIVE_DUE(L, f, least);
        if (!need) {
            int nn;
            const int *nb = FaceNeighbours(f, &nn);
            for (int j = 0; j < nn && !need; j++) need = LIVE_DUE(L, nb[j], least);
        }
        if (need) L->todo[L->ntodo++] = f;
    }
    g_liveNow = L;
    RunThreadsOn(L->ntodo, LiveTodoWork);
    for (int f = 0; f < numfaces; f++)
        if (LIVE_DUE(L, f, least)) L->dirty[f] = 0, L->pend[f] = 0;
}
static void LiveFinal(live_t *L) { LiveFinalLeast(L, 0); }

static void LivePublish(live_t *L, int state) {
    unsigned int *h = L->shared;
    size_t facebytes = sizeof(dface_t) * (size_t)numfaces;
    if (64 + facebytes + (size_t)lightdatasize > L->capacity) Error("HLLIVE: the lightmaps outgrew the shared memory (restart the session)");
    h[2] |= 1u;                         /* (odd: being written) */
    LiveBarrier();
    h[3] = (unsigned int)numfaces, h[4] = (unsigned int)sizeof(dface_t), h[5] = (unsigned int)lightdatasize;
    h[6] = (unsigned int)state, h[7] = (unsigned int)L->serial, h[8] = (unsigned int)L->pass, h[9] = (unsigned int)L->rays;
    memcpy((unsigned char *)h + 64, g_pFaces, facebytes);
    memcpy((unsigned char *)h + 64 + facebytes, dlightdata, (size_t)lightdatasize);
    LiveBarrier();
    h[2]++;
    Msg("HLLIVE_UPDATE %u %d %d %d %.3f\n", h[2], L->serial, state, L->pass, Seconds() - L->t0);
}

/* the lights in a list, as an array (the list's order) */
static directlight_t **LiveLights(int *count) {
    int n = 0;
    for (directlight_t *dl = activelights; dl; dl = dl->next) n++;
    directlight_t **a = xalloc(sizeof(directlight_t *) * (n + 1));
    n = 0;
    for (directlight_t *dl = activelights; dl; dl = dl->next) a[n++] = dl;
    *count = n;
    return a;
}

static float LiveSunExtent(const directlight_t *dl, float global) { return dl->has_sun_extent ? dl->sun_extent : global; }

/* whether two lights light the same (everything gather.comp gets of them) */
static int LiveSameLight(const directlight_t *a, float extA, const directlight_t *b, float extB) {
    if (memcmp(&a->light, &b->light, sizeof(dworldlight_t))) return 0;
    if (a->m_flStartFadeDistance != b->m_flStartFadeDistance || a->m_flEndFadeDistance != b->m_flEndFadeDistance ||
        a->m_flCapDist != b->m_flCapDist || a->directional != b->directional) return 0;
    if (a->light.type == emit_skylight && LiveSunExtent(a, extA) != LiveSunExtent(b, extB)) return 0;
    if (!a->pvs != !b->pvs) return 0;
    return !a->pvs || !memcmp(a->pvs, b->pvs, VisRowBytes());
}

/* step 1: the changed lights alone at every point (the old ones as style 253, the new as 252: a slot each in one
 * gather), new - old onto the samples' direct light and their light as it is now */
#define LIVE_OLD 253
#define LIVE_NEW 252
static void LiveDelta(live_t *L, directlight_t **oldl, int nold, float oldExt, directlight_t **newl, int nnew) {
    directlight_t *saved = activelights, *tmp = xalloc(sizeof(directlight_t) * (nold + nnew + 1));
    activelights = NULL;
    for (int i = nold + nnew - 1; i >= 0; i--) {
        tmp[i] = i < nold ? *oldl[i] : *newl[i - nold];
        tmp[i].sun_extent = LiveSunExtent(&tmp[i], i < nold ? oldExt : g_SunAngularExtent), tmp[i].has_sun_extent = 1;
        tmp[i].light.style = i < nold ? LIVE_OLD : LIVE_NEW;
        tmp[i].layer = 0;
        tmp[i].next = activelights;
        activelights = &tmp[i];
    }
    LiveMark(L, NULL);
    GPU_Lights();
    int so = GPU_StyleSlot(LIVE_OLD), sn = GPU_StyleSlot(LIVE_NEW), R = GPU_GroupFloats();
    /* the groups with a point in a cluster one of the lights can reach (the others would answer nothing) */
    int rowbytes = VisRowBytes(), everywhere = 0;
    unsigned char *reach = xalloc(rowbytes + 1);
    for (int i = 0; i < nold + nnew; i++) {
        if (!tmp[i].pvs) everywhere = 1;
        else for (int b = 0; b < rowbytes; b++) reach[b] |= tmp[i].pvs[b];
    }
    int *pick = xalloc(sizeof(int) * (L->n + 1)), npick = 0;
    for (int g = 0; g < L->n; g++) {
        int in = everywhere;
        for (int i = 0; i < L->groups[g].lanes && !in; i++) {
            int c = L->groups[g].cluster[i];
            in = c < 0 || (c < 8 * rowbytes && (reach[c >> 3] & (1 << (c & 7))));
        }
        if (in) pick[npick++] = g;
    }
    free(reach);
    enum { PIECE = 32768 };
    float *ans = xalloc(sizeof(float) * (size_t)R * PIECE);
    gpugroup_t *asked = xalloc(sizeof(gpugroup_t) * PIECE);
    for (int at = 0; at < npick; at += PIECE) {
        int k = npick - at < PIECE ? npick - at : PIECE;
        for (int g = 0; g < k; g++) asked[g] = L->groups[pick[at + g]];
        GPU_Gather(asked, k, -1, ans);
        for (int g = 0; g < k; g++) {
            int gi = pick[at + g];
            int facenum = L->where[gi][0], first = L->where[gi][1], style = L->k0[facenum];
            facelight_t *fl = &facelight[facenum];
            const float *ro = so >= 0 ? ans + (size_t)R * g + 64 * so : NULL, *rn = sn >= 0 ? ans + (size_t)R * g + 64 * sn : NULL;
            const gpugroup_t *q = &L->groups[gi];
            for (int i = 0; i < q->lanes && !L->stale[facenum]; i++)      /* (a changed light reaches the face) */
                if ((ro && ro[16 * i + 12] != 0) || (rn && rn[16 * i + 12] != 0)) L->stale[facenum] = 1, L->nstale++;
            for (int i = 0; i < q->lanes; i++) {
                if (g_bNoBake && NoBakePoint(fl->sample[first + i].pos)) continue;     /* (not lit: the ambient colour) */
                for (int b = 0; b < q->normalCount; b++)
                    for (int c = 0; c < 3; c++) {
                        float d = (rn ? rn[16 * i + 3 * b + c] : 0.0f) - (ro ? ro[16 * i + 3 * b + c] : 0.0f);
                        if (d == 0) continue;
                        float was = L->direct[facenum][b][first + i][c], now = was + d;
                        if (now < 0) now = 0;       /* (the old light was supersampled, this isn't: never below none) */
                        L->direct[facenum][b][first + i][c] = now;
                        fl->light[style][b][first + i][c] += now - was;
                        L->dirty[facenum] = 1;
                    }
            }
        }
    }
    free(ans), free(tmp), free(asked), free(pick);
    activelights = saved;
    GPU_Lights();
    if (L->debug) Msg("  [info] changed lights asked %d of %d groups\n", npick, L->n);
    LiveMark(L, "changed lights: gather");
}

/* step 3: the direct light as a bake makes it; the bounced light kept (warm) or dropped (a bake's start). Some: only
 * the faces step 1 changed (the others' direct light is a bake's already); when one of them comes out with other
 * styles than it had, every face is made again after all */
static void LiveExactDirect(live_t *L, int keepBounce, int some) {
    LiveMark(L, NULL);
    vec3_t *(*bounce)[NUM_BUMP_VECTS + 1] = xalloc(sizeof(*bounce) * (numfaces + 1));
    int *counts = xalloc(sizeof(int) * (numfaces + 1));
    if (L->allStale || !keepBounce) some = 0;
    int *list = NULL, nlist = 0;
    dface_t *was = NULL;
    if (some) {
        list = xalloc(sizeof(int) * (L->nstale + 1)), was = xalloc(sizeof(dface_t) * (L->nstale + 1));
        for (int f = 0; f < numfaces; f++)
            if (L->stale[f]) was[nlist] = g_pFaces[f], list[nlist++] = f;
    }
    for (int f = 0; f < numfaces; f++) {
        facelight_t *fl = &facelight[f];
        counts[f] = fl->numsamples;
        if (L->k0[f] < 0 || !keepBounce || (some && !L->stale[f])) continue;
        for (int b = 0; b < NUM_BUMP_VECTS + 1; b++) {
            if (!L->direct[f][b]) continue;
            bounce[f][b] = xalloc(sizeof(vec3_t) * (fl->numsamples + 1));
            for (int i = 0; i < fl->numsamples; i++)
                for (int c = 0; c < 3; c++) bounce[f][b][i][c] = fl->light[L->k0[f]][b][i][c] - L->direct[f][b][i][c];
        }
    }
    if (some) {
        BuildFacelightsGPUFaces(list, nlist);
        for (int i = 0; i < nlist; i++) {
            dface_t *face = &g_pFaces[list[i]];
            if (memcmp(face->styles, was[i].styles, 4)) some = 0;
            face->lightofs = was[i].lightofs;
        }
        if (!some) {                    /* (other styles: the lightmaps are laid out anew, from every face) */
            for (int f = 0; f < numfaces; f++) {
                if (L->k0[f] < 0 || L->stale[f]) continue;
                for (int b = 0; b < NUM_BUMP_VECTS + 1; b++) {
                    if (!L->direct[f][b]) continue;
                    bounce[f][b] = xalloc(sizeof(vec3_t) * (counts[f] + 1));
                    for (int i = 0; i < counts[f]; i++)
                        for (int c = 0; c < 3; c++) bounce[f][b][i][c] = facelight[f].light[L->k0[f]][b][i][c] - L->direct[f][b][i][c];
                }
            }
        }
    }
    if (!some) {
        BuildFacelightsGPU();
        free(dlightdata);
        dlightdata = NULL;
        PrecompLightmapOffsets();
        free(L->gridofs), free(L->grid);        /* (the lightmaps laid out anew: the change grid with them) */
        L->gridofs = NULL, L->grid = NULL;
        if (!keepBounce) LiveSetRef(L, 0);      /* (no bounced light: gathered from nothing) */
        else if (L->refBytes != lightdatasize) L->refBytes = -1;    /* (what it was gathered from: lost) */
    }
    LiveMark(L, "exact direct: gather");
    for (int f = 0; f < numfaces; f++) {
        facelight_t *fl = &facelight[f];
        const dface_t *face = &g_pFaces[f];
        if (fl->numsamples != counts[f]) Error("HLLIVE: a face's samples changed");
        if (L->k0[f] < 0 || (some && !L->stale[f])) continue;
        if (face->lightofs < 0 || face->styles[L->k0[f]] != 0) Error("HLLIVE: a face's light styles moved");
        for (int b = 0; b < NUM_BUMP_VECTS + 1; b++) {
            if (!L->direct[f][b]) continue;
            memcpy(L->direct[f][b], fl->light[L->k0[f]][b], sizeof(vec3_t) * fl->numsamples);
            if (bounce[f][b]) {
                for (int i = 0; i < fl->numsamples; i++)
                    for (int c = 0; c < 3; c++) fl->light[L->k0[f]][b][i][c] += bounce[f][b][i][c];
                free(bounce[f][b]);
            }
        }
    }
    free(bounce), free(counts), free(was);
    if (some) {
        for (int i = 0; i < nlist; i++) L->dirty[list[i]] = 1;
        LiveFinal(L);
    } else {
        PrepareFinalLight();
        RunThreadsOn(numfaces, FinalWork);
        memset(L->dirty, 0, numfaces + 1);
    }
    free(list);
    memset(L->stale, 0, numfaces + 1);
    L->nstale = L->allStale = 0;
    LiveMark(L, "exact direct: luxels");
}

/* a bounce pass (as BuildIndirectGPU's, without layers): the faces still unsettled */
static int LivePass(live_t *L, int rays, int all) {
    LiveMark(L, NULL);
    memset(L->faceChange, 0, sizeof(double) * (numfaces + 1));
    LiveSetRef(L, 1);
    GPU_Lightmaps();
    /* the groups to gather: all (a bake's pass, or nothing to go by), or the ones change passes touched, each told its
     * place in the job a bake gathers it in */
    int some = !all && !L->fullAll, count = L->n;
    if (some) {
        int job = GPU_GIJobGroups(rays);
        count = 0;
        for (int g = 0; g < L->n; g++)
            if (L->gtouch[g]) {
                L->asked[count] = L->groups[g];
                L->asked[count].pad[0] = g % job + 1;
                L->pick[count++] = g;
            }
    }
    if (count) GPU_GIGather(some ? L->asked : L->groups, count, rays, 1, L->out, NULL);
    if (L->debug) Msg("  [info] full pass gathered %d of %d groups\n", count, L->n);
    LiveMark(L, "full: gather");
    for (int at = 0; at < count; at++) {
        int g = some ? L->pick[at] : at;
        int facenum = L->where[g][0], first = L->where[g][1], k = L->k0[facenum];
        facelight_t *fl = &facelight[facenum];
        for (int i = 0; i < L->groups[g].lanes; i++)
            for (int b = 0; b < L->groups[g].normalCount; b++)
                for (int c = 0; c < 3; c++) {
                    float base = L->direct[facenum][b][first + i][c];
                    float next = L->out[64 * (size_t)at + 16 * i + 3 * b + c];
                    float was = fl->light[k][b][first + i][c];
                    double change = GIChange(was - base, next);
                    if (change > L->faceChange[facenum]) L->faceChange[facenum] = change;
                    if (base + next != was) L->dirty[facenum] = 1;
                    fl->light[k][b][first + i][c] = base + next;
                }
    }
    memset(L->gtouch, 0, (size_t)L->n + 1);
    memset(L->since, 0, sizeof(float) * (numfaces + 1));
    L->fullAll = 0;
    LiveMark(L, "full: add");
    if (all) {
        RunThreadsOn(numfaces, FinalWork);
        memset(L->dirty, 0, numfaces + 1);
    } else {                            /* (faces moved by a fraction of a lightmap byte wait, as after change passes) */
        for (int f = 0; f < numfaces; f++) L->pend[f] += (float)L->faceChange[f];
        LiveFinalLeast(L, 0.25f);
    }
    LiveMark(L, "full: luxels");
    int unsettled = 0;
    for (int f = 0; f < numfaces; f++) unsettled += L->faceChange[f] > 1.0;
    L->pass++;
    return unsettled;
}

/* the lightmaps the bounced light now answers to: as they are (bytes NULL: none, nothing gathered yet) */
static void LiveSetRef(live_t *L, int have) {
    if (L->refBytes != lightdatasize || !L->ref) {
        free(L->ref);
        L->ref = xalloc((size_t)lightdatasize + 4);
        L->refBytes = lightdatasize;
    }
    if (have) memcpy(L->ref, dlightdata, (size_t)lightdatasize);
    else memset(L->ref, 0, (size_t)lightdatasize);       /* (RGBE zeros: no light) */
}

static void LiveGrid(live_t *L) {
    free(L->gridofs), free(L->grid);
    L->gridofs = xalloc(sizeof(int) * (numfaces + 1));
    L->gridluxels = 0;
    for (int f = 0; f < numfaces; f++) {
        const dface_t *face = &g_pFaces[f];
        L->gridofs[f] = -1;
        if (face->lightofs < 0 || face->styles[0] != 0 || (texinfo[face->texinfo].flags & TEX_SPECIAL)) continue;
        L->gridofs[f] = (int)L->gridluxels;
        L->gridluxels += (size_t)(face->m_LightmapTextureSizeInLuxels[0] + 1) * (face->m_LightmapTextureSizeInLuxels[1] + 1);
    }
    L->grid = xalloc(sizeof(float) * 3 * (L->gridluxels + 1));
}

static live_t *g_liveGrid;
static void LiveGridWork(int f, int thread) {
    (void)thread;
    live_t *L = g_liveGrid;
    if (L->gridofs[f] < 0) return;
    const dface_t *face = &g_pFaces[f];
    int n = (face->m_LightmapTextureSizeInLuxels[0] + 1) * (face->m_LightmapTextureSizeInLuxels[1] + 1);
    const unsigned char *now = dlightdata + face->lightofs, *then = L->ref + face->lightofs;
    float *g = L->grid + 3 * (size_t)L->gridofs[f];
    if (!memcmp(now, then, 4 * (size_t)n)) {
        memset(g, 0, sizeof(float) * 3 * (size_t)n);
        L->gridChanged[f] = 0;
        return;
    }
    L->gridChanged[f] = 1;
    for (int j = 0; j < n; j++, now += 4, then += 4, g += 3) {
        float a = ldexpf(1.0f, (signed char)now[3]), b = ldexpf(1.0f, (signed char)then[3]);
        for (int c = 0; c < 3; c++) g[c] = now[c] * a - then[c] * b;
    }
}

/* a change pass: what the lightmaps changed by since the bounced light was gathered, gathered with a few rays and
 * added to the bounced light. Returns the faces it moved beyond tolerance */
static int LiveChangePass(live_t *L) {
    LiveMark(L, NULL);
    if (!L->gridofs) LiveGrid(L);
    if (!L->lout) L->lout = xalloc(sizeof(float) * 48 * (size_t)(L->n + 1));
    if (!L->gridChanged) L->gridChanged = xalloc(numfaces + 1);
    g_liveGrid = L;
    RunThreadsOn(numfaces, LiveGridWork);
    memcpy(L->ref, dlightdata, (size_t)lightdatasize);
    /* the groups that can see a changed face: those in clusters the changed faces' clusters see (the map's vis; a
     * point in no cluster, or a map without vis: every group) */
    int rowbytes = VisRowBytes(), count = 0, everywhere = !HaveVis() || rowbytes <= 0;
    unsigned char *from = xalloc(rowbytes + 1), *sees = xalloc(rowbytes + 1), *row = xalloc(rowbytes + 1);
    for (int g = 0; g < L->n && !everywhere; g++) {
        if (!L->gridChanged[L->where[g][0]]) continue;
        for (int i = 0; i < L->groups[g].lanes; i++) {
            int c = L->groups[g].cluster[i];
            if (c < 0 || c >= 8 * rowbytes) everywhere = 1;
            else from[c >> 3] |= (unsigned char)(1 << (c & 7));
        }
    }
    for (int c = 0; c < 8 * rowbytes && !everywhere; c++)
        if (from[c >> 3] & (1 << (c & 7))) {
            GetClusterPVS(c, row);
            for (int b = 0; b < rowbytes; b++) sees[b] |= row[b];
        }
    for (int g = 0; g < L->n; g++) {
        int in = everywhere;
        for (int i = 0; i < L->groups[g].lanes && !in; i++) {
            int c = L->groups[g].cluster[i];
            in = c < 0 || c >= 8 * rowbytes || (sees[c >> 3] & (1 << (c & 7)));
        }
        if (in) L->pick[count++] = g;
    }
    free(from), free(sees), free(row);
    /* (most of them, an open map: every group as it lies, no copies made) */
    int culled = count < L->n - L->n / 4;
    if (culled)
        for (int at = 0; at < count; at++) L->asked[at] = L->groups[L->pick[at]];
    else count = L->n;
    LiveMark(L, "change: grid");
    GPU_GILayers(L->grid, L->gridluxels, L->gridofs, numfaces, 1);
    if (count) GPU_GIGather(culled ? L->asked : L->groups, count, L->deltaRays, 1, NULL, L->lout);
    if (L->debug) Msg("  [info] change pass gathered %d of %d groups\n", count, L->n);
    LiveMark(L, "change: gather");
    memset(L->faceChange, 0, sizeof(double) * (numfaces + 1));
    for (int at = 0; at < count; at++) {
        int g = culled ? L->pick[at] : at;
        int facenum = L->where[g][0], first = L->where[g][1], k = L->k0[facenum];
        facelight_t *fl = &facelight[facenum];
        const float *r = L->lout + 48 * (size_t)at;
        int any = 0;
        for (int j = 0; j < 48 && !any; j++) any = r[j] != 0;
        if (!any) continue;
        double most = 0;
        for (int i = 0; i < L->groups[g].lanes; i++)
            for (int b = 0; b < L->groups[g].normalCount; b++)
                for (int c = 0; c < 3; c++) {
                    float d = r[12 * i + 3 * b + c];
                    if (d == 0) continue;
                    float was = fl->light[k][b][first + i][c], base = L->direct[facenum][b][first + i][c];
                    float now = was + d;
                    if (now < base) now = base;         /* (bounced light never below none) */
                    double change = GIChange(was - base, now - base);
                    if (change > most) most = change;
                    fl->light[k][b][first + i][c] = now;
                    L->dirty[facenum] = 1;
                }
        if (most > L->faceChange[facenum]) L->faceChange[facenum] = most;
        if (most > 0.02) L->gtouch[g] = 1;      /* (less: its full pass's light still, to a fraction of a tolerance) */
    }
    LiveMark(L, "change: add");
    for (int f = 0; f < numfaces; f++) L->pend[f] += (float)L->faceChange[f], L->since[f] += (float)L->faceChange[f];
    LiveFinalLeast(L, 0.25f);
    LiveMark(L, "change: luxels");
    int unsettled = 0;
    for (int f = 0; f < numfaces; f++) unsettled += L->faceChange[f] > 1.0;
    L->pass++;
    return unsettled;
}

static void LiveFreeLights(directlight_t **a, int n, const unsigned char *keep) {
    for (int i = 0; i < n; i++)
        if (!keep || !keep[i]) free(a[i]->pvs), free(a[i]);
}

/* the entities' lights made again; what changed lit (step 1, or all of the direct light when a changed light has a
 * style of its own). Returns whether the direct light is step 1's (rough at shadow edges) */
static int LiveApply(live_t *L) {
    int nold, nnew;
    directlight_t **oldl = LiveLights(&nold);
    float oldExt = g_SunAngularExtent;
    CreateDirectLights();
    directlight_t **newl = LiveLights(&nnew);
    unsigned char *oldSame = xalloc(nold + 1), *newSame = xalloc(nnew + 1);
    for (int i = 0; i < nnew; i++) {            /* (mostly the same lights in the same places) */
        int j = i < nold && !oldSame[i] && LiveSameLight(oldl[i], oldExt, newl[i], g_SunAngularExtent) ? i : -1;
        for (int k = 0; j < 0 && k < nold; k++)
            if (!oldSame[k] && LiveSameLight(oldl[k], oldExt, newl[i], g_SunAngularExtent)) j = k;
        if (j >= 0) oldSame[j] = newSame[i] = 1;
    }
    int no = 0, nn = 0, styled = 0;
    for (int i = 0; i < nold; i++)
        if (!oldSame[i]) styled |= oldl[i]->light.style != 0, oldl[no++] = oldl[i];
        else free(oldl[i]->pvs), free(oldl[i]);
    for (int i = 0; i < nnew; i++)
        if (!newSame[i]) styled |= newl[i]->light.style != 0, newl[nn++] = newl[i];
    free(oldSame), free(newSame);
    int rough = 0;
    if (no || nn) {
        if (styled) {
            GPU_Lights();
            LiveExactDirect(L, 1, 0);
        } else {
            LiveDelta(L, oldl, no, oldExt, newl, nn);
            LiveFinal(L);
            LiveMark(L, "changed lights: luxels");
            rough = 1;
        }
    }
    Msg("HLLIVE_LIGHTS %d changed (%d before, %d now)%s\n", no > nn ? no : nn, nold, nnew, styled ? ", styled: all direct light made again" : "");
    LiveFreeLights(oldl, no, NULL);
    free(oldl), free(newl);
    return rough;
}

static void LiveSession(gpugroup_t *groups, int (*where)[2], int n, int *k0, vec3_t *(*direct)[NUM_BUMP_VECTS + 1], float *out,
                        double *faceChange) {
    live_t L;
    memset(&L, 0, sizeof(L));
    L.groups = groups, L.where = where, L.n = n, L.k0 = k0, L.direct = direct, L.out = out, L.faceChange = faceChange;
    L.dirty = xalloc(numfaces + 1), L.stale = xalloc(numfaces + 1), L.todo = xalloc(sizeof(int) * (numfaces + 1));
    L.pend = xalloc(sizeof(float) * (numfaces + 1)), L.since = xalloc(sizeof(float) * (numfaces + 1));
    L.gtouch = xalloc((size_t)n + 1), L.pick = xalloc(sizeof(int) * (n + 1)), L.asked = xalloc(sizeof(gpugroup_t) * (n + 1));
    L.fullAll = 1;
    L.rays = g_giRays, L.t0 = Seconds();
    L.debug = getenv("HLLIVEDBG") != NULL;
    L.deltaRays = getenv("HLLIVEDELTA") ? atoi(getenv("HLLIVEDELTA")) : 128;     /* (0: full passes only) */
    if (L.deltaRays > 8192) L.deltaRays = 8192;
    LiveSetRef(&L, 0);                  /* (no bounced light yet: gathered from nothing) */
    GPU_Lightmaps();
    /* (room for the lightmaps to grow: a light with a style of its own can add pages) */
    L.capacity = 64 + sizeof(dface_t) * (size_t)numfaces + 2 * (size_t)lightdatasize + (1 << 20);
    L.shared = (unsigned int *)LiveMap(g_liveName, L.capacity);
    if (!L.shared) Error("-live: can't make the shared memory \"%s\"", g_liveName);
    memset(L.shared, 0, 64);
    L.shared[0] = 0x564C4C48u, L.shared[1] = 1, L.shared[10] = (unsigned int)L.capacity;
    LiveStartInput();
    Msg("HLLIVE_READY %s %u %d %d\n", g_liveName, (unsigned int)L.capacity, numfaces, num_entities);
    LivePublish(&L, 1);                 /* (the direct light, exact: the bounce follows) */
    int rough = 0, settled = 0, changed = 0;
    /* (change passes next; how many since a full one. From nothing, the start: the first bounce is most of the
     * bounced light, so a full pass makes it; the later bounces are changes) */
    int byChange = 0, nchange = 0, nfull = 0;
    char line[4096];
    for (;;) {
        int r = LiveGetLine(line, sizeof(line), settled >= 2 && !rough);
        if (r < 0) break;
        if (r > 0) {
            char *arg = line;
            while (*arg && *arg != ' ') arg++;
            if (*arg) *arg++ = 0;
            if (!strcmp(line, "QUIT")) break;
            else if (!strcmp(line, "SET")) {
                char *key = strchr(arg, ' ');
                int e = atoi(arg);
                if (!key || e < 0 || e >= num_entities) Error("HLLIVE: SET <entity> <key> <value>");
                *key++ = 0;
                char *value = strchr(key, ' ');
                if (value) *value++ = 0;
                SetKeyValue(&entities[e], key, value ? value : "");
                changed = 1;
            } else if (!strcmp(line, "ADD")) {
                Msg("HLLIVE_ENT %d\n", AddEntity());
            } else if (!strcmp(line, "DEL")) {
                int e = atoi(arg);
                if (e < 0 || e >= num_entities) Error("HLLIVE: DEL <entity>");
                SetKeyValue(&entities[e], "classname", "hl_removed");
                changed = 1;
            } else if (!strcmp(line, "RAYS")) {
                int rays = atoi(arg);
                if (rays < 1 || rays > 8192) Error("HLLIVE: RAYS 1..8192");
                if (rays != L.rays) L.rays = rays, settled = 0, nfull = 0, L.fullAll = 1, byChange = 0;
            } else if (!strcmp(line, "GO") || !strcmp(line, "COLD")) {
                L.t0 = Seconds();
                if (*arg) L.serial = atoi(arg);
                L.pass = 0;
                if (!strcmp(line, "COLD")) {            /* (as a bake: its direct light, then its passes from nothing) */
                    if (changed) {
                        int no;
                        directlight_t **oldl = LiveLights(&no);
                        CreateDirectLights();
                        LiveFreeLights(oldl, no, NULL);
                        free(oldl);
                        GPU_Lights();
                    }
                    LiveExactDirect(&L, 0, 0);
                    int stable = 0;
                    for (int pass = 0; pass < g_giPasses && stable < 3; pass++) {
                        int unsettled = LivePass(&L, g_giRays, 1);
                        stable = pass > 0 && !unsettled ? stable + 1 : 0;
                    }
                    rough = 0, settled = 2, changed = 0, byChange = 0;
                    L.rays = g_giRays;
                    LivePublish(&L, 3);
                    continue;
                }
                if (changed) rough |= LiveApply(&L);
                changed = 0, settled = 0, nchange = 0, nfull = 0;
                byChange = L.deltaRays > 0;
                LivePublish(&L, rough ? 0 : 1);
            } else Error("HLLIVE: unknown command \"%s\"", line);
            continue;
        }
        /* no command waits: the next step of the work */
        if (rough) {
            LiveExactDirect(&L, 1, 1);
            rough = 0, settled = 0;
            LivePublish(&L, 1);
            continue;
        }
        if (L.refBytes != lightdatasize) byChange = 0, L.fullAll = 1;      /* (laid out anew: a full pass first) */
        if (byChange) {                 /* (until nothing is left to add: then a full pass, or none) */
            int unsettled = LiveChangePass(&L);
            if (!unsettled || ++nchange >= 64) {
                byChange = 0;
                float most = 0;
                for (int f = 0; f < numfaces; f++)
                    if (L.since[f] > most) most = L.since[f];
                if (nfull > 0 && most < 2.0f) {         /* (little added since the full pass: its noise is nothing) */
                    settled = 2;
                    LiveFinal(&L);
                    LivePublish(&L, 2);
                    continue;
                }
            }
            LivePublish(&L, 1);
            continue;
        }
        if (!L.deltaRays) L.fullAll = 1;        /* (no change passes to go by) */
        int unsettled = LivePass(&L, L.rays, 0);
        nfull++;
        if (!L.deltaRays) settled = unsettled ? 0 : settled + 1;
        else if (!unsettled || nfull >= 8) settled = 2;         /* (the change passes had settled it: this agrees) */
        else byChange = 1, nchange = 0;
        if (settled >= 2) LiveFinal(&L);                        /* (the faces that waited) */
        LivePublish(&L, settled >= 2 ? 2 : 1);
    }
    Msg("HLLIVE_DONE\n");
}
