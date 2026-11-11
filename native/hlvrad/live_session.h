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
    unsigned char *stale;               /* per face: its direct light has step 1's shortcut in it */
    int nstale, allStale;
    int *todo, ntodo;
    unsigned int *shared;
    size_t capacity;
    int serial, rays, pass;
    double t0;                          /* (when the last command was taken) */
} live_t;

static live_t *g_liveNow;
static void LiveTodoWork(int i, int thread) { (void)thread; FinalLightFace(g_liveNow->todo[i]); }

/* the luxels of the faces whose samples changed, and of the faces that blend those samples into theirs (neighbours
 * over smoothed edges; a displacement gathers samples by distance: any change makes them all again) */
static void LiveFinal(live_t *L) {
    int any = 0;
    for (int f = 0; f < numfaces && !any; f++) any = L->dirty[f];
    if (!any) return;
    L->ntodo = 0;
    for (int f = 0; f < numfaces; f++) {
        int need = L->dirty[f] || g_pFaces[f].dispinfo != -1;
        if (!need) {
            int nn;
            const int *nb = FaceNeighbours(f, &nn);
            for (int j = 0; j < nn && !need; j++) need = L->dirty[nb[j]];
        }
        if (need) L->todo[L->ntodo++] = f;
    }
    g_liveNow = L;
    RunThreadsOn(L->ntodo, LiveTodoWork);
    memset(L->dirty, 0, numfaces + 1);
}

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
    GPU_Lights();
    int so = GPU_StyleSlot(LIVE_OLD), sn = GPU_StyleSlot(LIVE_NEW), R = GPU_GroupFloats();
    enum { PIECE = 32768 };
    float *ans = xalloc(sizeof(float) * (size_t)R * PIECE);
    for (int at = 0; at < L->n; at += PIECE) {
        int k = L->n - at < PIECE ? L->n - at : PIECE;
        GPU_Gather(L->groups + at, k, -1, ans);
        for (int g = 0; g < k; g++) {
            int facenum = L->where[at + g][0], first = L->where[at + g][1], style = L->k0[facenum];
            facelight_t *fl = &facelight[facenum];
            const float *ro = so >= 0 ? ans + (size_t)R * g + 64 * so : NULL, *rn = sn >= 0 ? ans + (size_t)R * g + 64 * sn : NULL;
            const gpugroup_t *q = &L->groups[at + g];
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
    free(ans), free(tmp);
    activelights = saved;
    GPU_Lights();
}

/* step 3: the direct light as a bake makes it; the bounced light kept (warm) or dropped (a bake's start). Some: only
 * the faces step 1 changed (the others' direct light is a bake's already); when one of them comes out with other
 * styles than it had, every face is made again after all */
static void LiveExactDirect(live_t *L, int keepBounce, int some) {
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
    }
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
}

/* a bounce pass (as BuildIndirectGPU's, without layers): the faces still unsettled */
static int LivePass(live_t *L, int rays, int all) {
    memset(L->faceChange, 0, sizeof(double) * (numfaces + 1));
    GPU_Lightmaps();
    GPU_GIGather(L->groups, L->n, rays, 1, L->out, NULL);
    for (int g = 0; g < L->n; g++) {
        int facenum = L->where[g][0], first = L->where[g][1], k = L->k0[facenum];
        facelight_t *fl = &facelight[facenum];
        for (int i = 0; i < L->groups[g].lanes; i++)
            for (int b = 0; b < L->groups[g].normalCount; b++)
                for (int c = 0; c < 3; c++) {
                    float base = L->direct[facenum][b][first + i][c];
                    float next = L->out[64 * (size_t)g + 16 * i + 3 * b + c];
                    float was = fl->light[k][b][first + i][c];
                    double change = GIChange(was - base, next);
                    if (change > L->faceChange[facenum]) L->faceChange[facenum] = change;
                    if (base + next != was) L->dirty[facenum] = 1;
                    fl->light[k][b][first + i][c] = base + next;
                }
    }
    if (all) {
        RunThreadsOn(numfaces, FinalWork);
        memset(L->dirty, 0, numfaces + 1);
    } else LiveFinal(L);
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
    L.rays = g_giRays, L.t0 = Seconds();
    /* (room for the lightmaps to grow: a light with a style of its own can add pages) */
    L.capacity = 64 + sizeof(dface_t) * (size_t)numfaces + 2 * (size_t)lightdatasize + (1 << 20);
    L.shared = (unsigned int *)LiveMap(g_liveName, L.capacity);
    if (!L.shared) Error("-live: can't make the shared memory \"%s\"", g_liveName);
    memset(L.shared, 0, 64);
    L.shared[0] = 0x564C4C48u, L.shared[1] = 1, L.shared[10] = (unsigned int)L.capacity;
    LiveStartInput();
    Msg("HLLIVE_READY %s %u %d %d\n", g_liveName, (unsigned int)L.capacity, numfaces, num_entities);
    LivePublish(&L, 1);                 /* (the direct light, exact: the bounce follows) */
    int rough = 0, settled = 0, sinceEdit = 0, changed = 0;
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
                if (rays != L.rays) L.rays = rays, settled = 0;
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
                    rough = 0, settled = 2, changed = 0;
                    L.rays = g_giRays;
                    LivePublish(&L, 3);
                    continue;
                }
                if (changed) rough |= LiveApply(&L);
                changed = 0, settled = 0, sinceEdit = 0;
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
        int unsettled = LivePass(&L, L.rays, 0);
        sinceEdit++;
        settled = unsettled ? 0 : settled + 1;
        LivePublish(&L, rough ? 0 : settled >= 2 ? 2 : 1);
    }
    Msg("HLLIVE_DONE\n");
}
