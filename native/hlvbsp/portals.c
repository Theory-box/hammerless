/* Portals between the tree's leaves, the flood from entities that finds the inside, areas, and
 * which brush sides end up visible. */
#include "hlvbsp.h"

#define SIDESPACE 8
#define BASE_WINDING_EPSILON 0.001
#define SPLIT_WINDING_EPSILON 0.001

int c_areas;

static portal_t *AllocPortal(void) { return xalloc(sizeof(portal_t)); }

void FreePortal(portal_t *p) {
    if (p->winding) FreeWinding(p->winding);
    free(p);
}

/* The strongest visible content bit present. */
int VisibleContents(int contents) {
    for (int i = 1; i <= LAST_VISIBLE_CONTENTS; i <<= 1)
        if (contents & i) return i;
    return 0;
}

static int ClusterContents(node_t *node) {
    if (node->planenum == PLANENUM_LEAF) return node->contents;
    int c1 = ClusterContents(node->children[0]), c2 = ClusterContents(node->children[1]);
    int c = c1 | c2;
    if (!(c1 & CONTENTS_SOLID) || !(c2 & CONTENTS_SOLID)) c &= ~CONTENTS_SOLID;
    return c;
}

/* Can vis see through this portal? */
int Portal_VisFlood(portal_t *p) {
    if (!p->onnode) return 0;
    int c1 = ClusterContents(p->nodes[0]), c2 = ClusterContents(p->nodes[1]);
    if (!VisibleContents(c1 ^ c2)) return 1;
    if (c1 & (CONTENTS_TRANSLUCENT | CONTENTS_DETAIL)) c1 = 0;
    if (c2 & (CONTENTS_TRANSLUCENT | CONTENTS_DETAIL)) c2 = 0;
    if ((c1 | c2) & CONTENTS_SOLID) return 0;
    if (!(c1 ^ c2)) return 1;
    if (!VisibleContents(c1 ^ c2)) return 1;
    return 0;
}

static int Portal_EntityFlood(portal_t *p) {
    if (p->nodes[0]->planenum != PLANENUM_LEAF || p->nodes[1]->planenum != PLANENUM_LEAF)
        Error("Portal_EntityFlood: not a leaf");
    if ((p->nodes[0]->contents & CONTENTS_SOLID) || (p->nodes[1]->contents & CONTENTS_SOLID)) return 0;
    return 1;
}

static void AddPortalToNodes(portal_t *p, node_t *front, node_t *back) {
    if (p->nodes[0] || p->nodes[1]) Error("AddPortalToNode: allready included");
    p->nodes[0] = front;
    p->next[0] = front->portals;
    front->portals = p;
    p->nodes[1] = back;
    p->next[1] = back->portals;
    back->portals = p;
}

void RemovePortalFromNode(portal_t *portal, node_t *l) {
    portal_t **pp = &l->portals, *t;
    for (;;) {
        t = *pp;
        if (!t) Error("RemovePortalFromNode: portal not in leaf");
        if (t == portal) break;
        if (t->nodes[0] == l) pp = &t->next[0];
        else if (t->nodes[1] == l) pp = &t->next[1];
        else Error("RemovePortalFromNode: portal not bounding leaf");
    }
    if (portal->nodes[0] == l) {
        *pp = portal->next[0];
        portal->nodes[0] = NULL;
    } else if (portal->nodes[1] == l) {
        *pp = portal->next[1];
        portal->nodes[1] = NULL;
    }
}

/* Six portals from the tree's box (8 units larger) to the global outside node. */
void MakeHeadnodePortals(tree_t *tree) {
    vec3_t bounds[2];
    portal_t *portals[6];
    plane_t bplanes[6];
    node_t *node = tree->headnode;
    for (int i = 0; i < 3; i++) {
        bounds[0][i] = tree->mins[i] - SIDESPACE;
        bounds[1][i] = tree->maxs[i] + SIDESPACE;
    }
    tree->outside_node.planenum = PLANENUM_LEAF;
    tree->outside_node.brushlist = NULL;
    tree->outside_node.portals = NULL;
    tree->outside_node.contents = 0;
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 2; j++) {
            int n = j * 3 + i;
            portal_t *p = AllocPortal();
            portals[n] = p;
            plane_t *pl = &bplanes[n];
            memset(pl, 0, sizeof(*pl));
            if (j) {
                pl->normal[i] = -1;
                pl->dist = -bounds[j][i];
            } else {
                pl->normal[i] = 1;
                pl->dist = bounds[j][i];
            }
            p->plane = *pl;
            p->winding = BaseWindingForPlane(pl->normal, pl->dist);
            AddPortalToNodes(p, node, &tree->outside_node);
        }
    for (int i = 0; i < 6; i++)
        for (int j = 0; j < 6; j++) {
            if (j == i) continue;
            ChopWindingInPlace(&portals[i]->winding, bplanes[j].normal, bplanes[j].dist, ON_EPSILON);
        }
}

static winding_t *BaseWindingForNode(node_t *node) {
    winding_t *w = BaseWindingForPlane(mapplanes[node->planenum].normal, mapplanes[node->planenum].dist);
    for (node_t *n = node->parent; n && w;) {
        plane_t *plane = &mapplanes[n->planenum];
        if (n->children[0] == node) {
            ChopWindingInPlace(&w, plane->normal, plane->dist, BASE_WINDING_EPSILON);
        } else {
            vec3_t normal;
            VectorFromOrigin(plane->normal, normal);
            ChopWindingInPlace(&w, normal, -plane->dist, BASE_WINDING_EPSILON);
        }
        node = n;
        n = n->parent;
    }
    return w;
}

/* The node's plane, clipped by its parents and its existing portals: the portal between its children. */
void MakeNodePortal(node_t *node) {
    winding_t *w = BaseWindingForNode(node);
    int side = 0;
    for (portal_t *p = node->portals; p && w; p = p->next[side]) {
        vec3_t normal;
        float dist;
        if (p->nodes[0] == node) {
            side = 0;
            VectorCopy(p->plane.normal, normal);
            dist = p->plane.dist;
        } else if (p->nodes[1] == node) {
            side = 1;
            VectorFromOrigin(p->plane.normal, normal);
            dist = -p->plane.dist;
        } else Error("CutNodePortals_r: mislinked portal");
        ChopWindingInPlace(&w, normal, dist, 0.1);
    }
    if (!w) return;
    if (WindingIsTiny(w)) {
        FreeWinding(w);
        return;
    }
    portal_t *np = AllocPortal();
    np->plane = mapplanes[node->planenum];
    np->onnode = node;
    np->winding = w;
    AddPortalToNodes(np, node->children[0], node->children[1]);
}

/* Hand the node's portals down to its children, splitting those that cross its plane. */
void SplitNodePortals(node_t *node) {
    plane_t *plane = &mapplanes[node->planenum];
    node_t *f = node->children[0], *b = node->children[1];
    portal_t *p, *next_portal;
    int side = 0;
    for (p = node->portals; p; p = next_portal) {
        if (p->nodes[0] == node) side = 0;
        else if (p->nodes[1] == node) side = 1;
        else Error("CutNodePortals_r: mislinked portal");
        next_portal = p->next[side];
        node_t *other = p->nodes[!side];
        RemovePortalFromNode(p, p->nodes[0]);
        RemovePortalFromNode(p, p->nodes[1]);
        winding_t *fw, *bw;
        ClipWindingEpsilon(p->winding, plane->normal, plane->dist, SPLIT_WINDING_EPSILON, &fw, &bw);
        if (fw && WindingIsTiny(fw)) {
            FreeWinding(fw);
            fw = NULL;
        }
        if (bw && WindingIsTiny(bw)) {
            FreeWinding(bw);
            bw = NULL;
        }
        if (!fw && !bw) continue;       /* tiny on both sides */
        if (!fw) {
            FreeWinding(bw);
            if (side == 0) AddPortalToNodes(p, b, other);
            else AddPortalToNodes(p, other, b);
            continue;
        }
        if (!bw) {
            FreeWinding(fw);
            if (side == 0) AddPortalToNodes(p, f, other);
            else AddPortalToNodes(p, other, f);
            continue;
        }
        portal_t *np = AllocPortal();
        *np = *p;
        np->winding = bw;
        FreeWinding(p->winding);
        p->winding = fw;
        if (side == 0) {
            AddPortalToNodes(p, f, other);
            AddPortalToNodes(np, b, other);
        } else {
            AddPortalToNodes(p, other, f);
            AddPortalToNodes(np, other, b);
        }
    }
    node->portals = NULL;
}

static void CalcNodeBounds(node_t *node) {
    ClearBounds(node->mins, node->maxs);
    int s;
    for (portal_t *p = node->portals; p; p = p->next[s]) {
        s = (p->nodes[1] == node);
        for (int i = 0; i < p->winding->numpoints; i++) AddPointToBounds(p->winding->p[i], node->mins, node->maxs);
    }
}

static void MakeTreePortals_r(node_t *node) {
    CalcNodeBounds(node);
    if (node->mins[0] >= node->maxs[0]) Warning("WARNING: node without a volume\n");
    for (int i = 0; i < 3; i++) {
        if (node->mins[i] < (MIN_COORD_INTEGER - SIDESPACE) || node->maxs[i] > (MAX_COORD_INTEGER + SIDESPACE)) {
            Warning("WARNING: BSP node with unbounded volume\n");
            break;
        }
    }
    if (node->planenum == PLANENUM_LEAF) return;
    MakeNodePortal(node);
    SplitNodePortals(node);
    MakeTreePortals_r(node->children[0]);
    MakeTreePortals_r(node->children[1]);
}

void MakeTreePortals(tree_t *tree) {
    MakeHeadnodePortals(tree);
    MakeTreePortals_r(tree->headnode);
}

/* ------------------------------------------------------------------ the entity flood */
static void FloodPortals_r(node_t *node, int dist) {
    node->occupied = dist;
    int s;
    for (portal_t *p = node->portals; p; p = p->next[s]) {
        s = (p->nodes[1] == node);
        if (p->nodes[!s]->occupied) continue;
        if (!Portal_EntityFlood(p)) continue;
        FloodPortals_r(p->nodes[!s], dist + 1);
    }
}

static int PlaceOccupant(node_t *headnode, vec3_t origin, entity_t *occupant) {
    node_t *node = headnode;
    while (node->planenum != PLANENUM_LEAF) {
        plane_t *plane = &mapplanes[node->planenum];
        vec_t d = DotProduct(origin, plane->normal) - plane->dist;
        node = d >= 0 ? node->children[0] : node->children[1];
    }
    if (node->contents == CONTENTS_SOLID) return 0;
    node->occupant = occupant;
    FloodPortals_r(node, 1);
    return 1;
}

/* Every leaf an entity can reach; false when nothing is inside or the outside was reached (a leak). */
int FloodEntities(tree_t *tree) {
    node_t *headnode = tree->headnode;
    int inside = 0;
    vec3_t origin;
    tree->outside_node.occupied = 0;
    for (int i = 1; i < num_entities; i++) {
        GetVectorForKey(&entities[i], "origin", origin);
        if (origin[0] == 0 && origin[1] == 0 && origin[2] == 0) continue;
        const char *cl = ValueForKey(&entities[i], "classname");
        origin[2] += 1;     /* things on the floor are fine */
        if (!strcmp(cl, "info_player_start")) {
            for (int x = -16; x <= 16; x += 16) {
                for (int y = -16; y <= 16; y += 16) {
                    origin[0] += x;
                    origin[1] += y;
                    if (PlaceOccupant(headnode, origin, &entities[i])) {
                        inside = 1;
                        goto gotit;
                    }
                    origin[0] -= x;
                    origin[1] -= y;
                }
            }
        gotit:;
        } else {
            if (PlaceOccupant(headnode, origin, &entities[i])) inside = 1;
        }
    }
    return inside && !tree->outside_node.occupied;
}

static void FillOutside_r(node_t *node) {
    if (node->planenum != PLANENUM_LEAF) {
        FillOutside_r(node->children[0]);
        FillOutside_r(node->children[1]);
        return;
    }
    if (!node->occupied && node->contents != CONTENTS_SOLID) node->contents = CONTENTS_SOLID;
}

void FillOutside(node_t *headnode) { FillOutside_r(headnode); }

/* ------------------------------------------------------------------ areas */
static int IsAreaportalNode(node_t *node) { return (node->contents & CONTENTS_AREAPORTAL) != 0; }

static bspbrush_t *AreaportalBrushForNode(node_t *node) {
    bspbrush_t *b = node->brushlist;
    while (b && !(b->original->contents & CONTENTS_AREAPORTAL)) b = b->next;
    return b;
}

static void FloodAreas_r(node_t *node, portal_t *through) {
    if (IsAreaportalNode(node)) {
        bspbrush_t *b = AreaportalBrushForNode(node);
        entity_t *e = &entities[b->original->entitynum];
        if (e->portalareas[0] == c_areas || e->portalareas[1] == c_areas) return;
        if (e->portalareas[1]) {
            Warning("WARNING: areaportal entity %i (brush %i) touches > 2 areas\n", b->original->entitynum, b->original->id);
            return;
        }
        if (e->portalareas[0]) {
            e->portalareas[1] = c_areas;
            e->portals_into_areas[1] = through;
        } else {
            e->portalareas[0] = c_areas;
            e->portals_into_areas[0] = through;
        }
        return;
    }
    if (node->area) return;
    node->area = c_areas;
    int s;
    for (portal_t *p = node->portals; p; p = p->next[s]) {
        s = (p->nodes[1] == node);
        if (!Portal_EntityFlood(p)) continue;
        FloodAreas_r(p->nodes[!s], p);
    }
}

static void FindAreas_r(node_t *node) {
    if (node->planenum != PLANENUM_LEAF) {
        FindAreas_r(node->children[0]);
        FindAreas_r(node->children[1]);
        return;
    }
    if (node->area || (node->contents & CONTENTS_SOLID) || !node->occupied || IsAreaportalNode(node)) return;
    c_areas++;
    FloodAreas_r(node, NULL);
}

const char *g_linpath;
static int tree_leaked;

static int Portal_AreaLeakFlood(portal_t *p) {
    if (!Portal_EntityFlood(p)) return 0;
    if ((p->nodes[0]->contents & CONTENTS_AREAPORTAL) || (p->nodes[1]->contents & CONTENTS_AREAPORTAL)) return 0;
    return 1;
}

static void FloodAreaLeak_r(node_t *node, int dist) {
    node->occupied = dist;
    int s;
    for (portal_t *p = node->portals; p; p = p->next[s]) {
        s = (p->nodes[1] == node);
        if (p->nodes[!s]->occupied) continue;
        if (!Portal_AreaLeakFlood(p)) continue;
        FloodAreaLeak_r(p->nodes[!s], dist + 1);
    }
}

static void ClearOccupied_r(node_t *node) {
    if (!node) return;
    node->occupied = 0;
    if (node->planenum != PLANENUM_LEAF) {
        ClearOccupied_r(node->children[0]);
        ClearOccupied_r(node->children[1]);
    }
}

/* An areaportal that doesn't separate two areas: a leak line from one side of it around to the other. */
static void AreaportalLeakFile(portal_t *start, portal_t *end, node_t *startnode) {
    if (tree_leaked || !g_linpath) return;
    tree_leaked = 1;
    FILE *f = fopen(g_linpath, "w");
    if (!f) Error("Couldn't open %s", g_linpath);
    vec3_t mid;
    WindingCenter(end->winding, mid);
    fprintf(f, "%f %f %f\n", mid[0], mid[1], mid[2]);
    for (int k = 0; k < 3; k++) mid[k] = 0.5f * (startnode->mins[k] + startnode->maxs[k]);
    fprintf(f, "%f %f %f\n", mid[0], mid[1], mid[2]);
    node_t *node = startnode;
    while (node->occupied >= 1) {
        portal_t *nextportal = NULL;
        node_t *nextnode = NULL;
        int s = 0, next = node->occupied;
        for (portal_t *p = node->portals; p; p = p->next[!s]) {
            s = (p->nodes[0] == node);
            if (p->nodes[s]->occupied && p->nodes[s]->occupied < next) {
                nextportal = p;
                nextnode = p->nodes[s];
                next = nextnode->occupied;
            }
        }
        if (!nextnode) break;
        node = nextnode;
        WindingCenter(nextportal->winding, mid);
        fprintf(f, "%f %f %f\n", mid[0], mid[1], mid[2]);
    }
    for (int k = 0; k < 3; k++) mid[k] = 0.5f * (node->mins[k] + node->maxs[k]);
    fprintf(f, "%f %f %f\n", mid[0], mid[1], mid[2]);
    WindingCenter(start->winding, mid);
    fprintf(f, "%f %f %f\n", mid[0], mid[1], mid[2]);
    fclose(f);
    Warning("Wrote %s\n", g_linpath);
    Msg("Areaportal leak ! File: %s ", g_linpath);
}

static void ReportAreaportalLeak(node_t *headnode, node_t *node) {
    portal_t *p, *start = NULL;
    int s = 0;
    for (p = node->portals; p; p = p->next[s]) {
        s = (p->nodes[1] == node);
        if (!Portal_EntityFlood(p)) continue;
        if (p->nodes[!s]->contents & CONTENTS_AREAPORTAL) continue;
        start = p;
        break;
    }
    if (!start) return;
    s = start->nodes[0] == node;
    ClearOccupied_r(headnode);
    FloodAreaLeak_r(start->nodes[s], 2);
    portal_t *best = NULL;
    int bestdist = 0;
    for (p = node->portals; p; p = p->next[s]) {
        if (p == start) continue;       /* (vbsp steps on with the previous portal's side here) */
        s = (p->nodes[1] == node);
        if (p->nodes[!s]->occupied > bestdist) {
            best = p;
            bestdist = p->nodes[!s]->occupied;
        }
    }
    if (best) {
        s = (best->nodes[0] == node);
        AreaportalLeakFile(start, best, best->nodes[s]);
    }
}

static node_t *area_headnode;

static void SetAreaPortalAreas_r(node_t *node) {
    if (node->planenum != PLANENUM_LEAF) {
        SetAreaPortalAreas_r(node->children[0]);
        SetAreaPortalAreas_r(node->children[1]);
        return;
    }
    if (IsAreaportalNode(node)) {
        if (node->area) return;
        bspbrush_t *b = AreaportalBrushForNode(node);
        entity_t *e = &entities[b->original->entitynum];
        node->area = e->portalareas[0];
        if (!e->portalareas[1]) {
            ReportAreaportalLeak(area_headnode, node);
            Warning("\nBrush %i: areaportal brush doesn't touch two areas\n", b->original->id);
        }
    }
}

void FloodAreas(tree_t *tree) {
    Msg("Processing areas...");
    FindAreas_r(tree->headnode);
    area_headnode = tree->headnode;
    SetAreaPortalAreas_r(tree->headnode);
    Msg("done (0)\n");
}

static void SetNodeAreaIndices_R(node_t *node) {
    if (node->planenum == PLANENUM_LEAF) return;
    SetNodeAreaIndices_R(node->children[0]);
    SetNodeAreaIndices_R(node->children[1]);
    node->area = node->children[0]->area == node->children[1]->area ? node->children[0]->area : -1;
}

/* ------------------------------------------------------------------ clip portal outlines */
/* The game clips what it draws through an open areaportal to the outline of the portals between the
 * two areas on the areaportal's plane: their points, reduced to a 2D convex hull (gift wrapping, as
 * vbsp does it, in the plane's own axes). */
typedef struct { float x, y; } v2_t;

static float dist2(v2_t a, v2_t b) { return (a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y); }

static int FindUniquePoints(const v2_t *pts, int n, int *map, int maxmap, float tol) {
    float tol2 = tol * tol;
    int unique = 0;
    for (int i = 0; i < n; i++) {
        int j;
        for (j = 0; j < unique; j++)
            if (dist2(pts[i], pts[map[j]]) < tol2) break;
        if (j == unique) {
            if (unique >= maxmap) Error("FindUniquePoints: overflowed unique point list (size %d).", maxmap);
            map[unique++] = i;
        }
    }
    return unique;
}

static float AngleOffset(float base, float test) {
    while (test > base) test = (float)(test - 2 * M_PI);      /* (in double, then stored) */
    return fmodf(base - test, (float)(2 * M_PI));
}

static int Convex2D(const v2_t *pts, int n, int *indices, int maxindices) {
    int map[512];
    if (n == 0) return 0;
    n = FindUniquePoints(pts, n, map, 512, 0.1f);
    int best = 0;
    for (int i = 1; i < n; i++)
        if (pts[map[i]].x < pts[map[best]].x || (pts[map[i]].x == pts[map[best]].x && pts[map[i]].y < pts[map[best]].y))
            best = i;
    indices[0] = map[best];
    int count = 1;
    v2_t edge = {0, 1};
    for (;;) {
        const v2_t *start = &pts[indices[count - 1]];
        float edgeangle = atan2f(edge.y, edge.x);
        int minidx = -1;
        float minangle = 5000;
        for (int i = 0; i < n; i++) {
            v2_t to = {pts[map[i]].x - start->x, pts[map[i]].y - start->y};
            float d2 = to.x * to.x + to.y * to.y;
            if (d2 <= 0.1f) continue;
            float angle = AngleOffset(edgeangle, atan2f(to.y, to.x));
            if (fabsf(angle - minangle) < 0.00001f) {
                float d2test = dist2(*start, pts[minidx]);
                if (minidx != indices[0] && d2 > d2test) {
                    minangle = angle;
                    minidx = map[i];
                }
            } else if (angle < minangle) {
                minangle = angle;
                minidx = map[i];
            }
        }
        if (minidx == -1 || minidx == indices[0] || count >= maxindices) break;
        indices[count++] = minidx;
        edge.x = pts[indices[count - 1]].x - pts[indices[count - 2]].x;
        edge.y = pts[indices[count - 1]].y - pts[indices[count - 2]].y;
    }
    return count;
}

typedef struct { portal_t **p; int n, cap; } portallist_t;

static void FindPortalsLeadingToArea_R(node_t *node, int src, int dst, plane_t *plane, portallist_t *out) {
    if (node->planenum != PLANENUM_LEAF) {
        FindPortalsLeadingToArea_R(node->children[0], src, dst, plane, out);
        FindPortalsLeadingToArea_R(node->children[1], src, dst, plane, out);
        return;
    }
    int s;
    for (portal_t *p = node->portals; p; p = p->next[!s]) {
        s = (p->nodes[0] == node);
        if (!p->nodes[0]->occupied || !p->nodes[1]->occupied) continue;
        if ((p->nodes[1]->area == dst && p->nodes[0]->area == src) || (p->nodes[0]->area == dst && p->nodes[1]->area == src)) {
            plane_t *mp = &mapplanes[p->onnode->planenum];
            float dot = fabsf(DotProduct(mp->normal, plane->normal));
            if (fabsf(1 - dot) < 0.01f) {
                vec3_t a, b, d;
                VectorScale(plane->normal, plane->dist, a);
                VectorScale(mp->normal, mp->dist, b);
                VectorSubtract(a, b, d);
                if (DotProduct(d, d) < 0.01f) {
                    if (out->n == out->cap) {
                        out->cap = out->cap ? out->cap * 2 : 16;
                        out->p = realloc(out->p, sizeof(portal_t *) * out->cap);
                    }
                    out->p[out->n++] = p;
                }
            }
        }
    }
}

static void VectorAngles(const vec3_t forward, vec3_t angles) {
    float yaw, pitch;
    if (forward[1] == 0 && forward[0] == 0) {
        yaw = 0;
        pitch = forward[2] > 0 ? 270 : 90;
    } else {
        yaw = (float)((double)(atan2f(forward[1], forward[0]) * 180.0f) / M_PI);
        if (yaw < 0) yaw += 360;
        float tmp = sqrtf(forward[0] * forward[0] + forward[1] * forward[1]);
        pitch = (float)((double)(atan2f(-forward[2], tmp) * 180.0f) / M_PI);
        if (pitch < 0) pitch += 360;
    }
    angles[0] = pitch;
    angles[1] = yaw;
    angles[2] = 0;
}

static void SinCos(float r, float *s, float *c) {
    *s = (float)sin(r);
    *c = (float)cos(r);
}

static void AngleVectors(const vec3_t angles, vec3_t f, vec3_t r, vec3_t u) {
    float sr, sp, sy, cr, cp, cy;
    const float d2r = (float)((float)M_PI / 180.f);
    SinCos(angles[1] * d2r, &sy, &cy);
    SinCos(angles[0] * d2r, &sp, &cp);
    SinCos(angles[2] * d2r, &sr, &cr);
    f[0] = cp * cy; f[1] = cp * sy; f[2] = -sp;
    r[0] = (-1 * sr * sp * cy + -1 * cr * -sy);
    r[1] = (-1 * sr * sp * sy + -1 * cr * cy);
    r[2] = -1 * sr * cp;
    u[0] = (cr * sp * cy + -sr * -sy);
    u[1] = (cr * sp * sy + -sr * cy);
    u[2] = cr * cp;
}

static void EmitClipPortalGeometry(node_t *headnode, portal_t *portal, int src, dareaportal_t *dp) {
    portallist_t list = {0};
    FindPortalsLeadingToArea_R(headnode, src, dp->otherarea, &portal->plane, &list);
    int npts = 0;
    for (int i = 0; i < list.n; i++) npts += list.p[i]->winding->numpoints;
    vec3_t *points = xalloc(sizeof(vec3_t) * (npts + 1));
    v2_t *pts2 = xalloc(sizeof(v2_t) * (npts + 1));
    int k = 0;
    for (int i = 0; i < list.n; i++)
        for (int j = 0; j < list.p[i]->winding->numpoints; j++, k++) VectorCopy(list.p[i]->winding->p[j], points[k]);
    vec3_t angles, f, r, u;
    VectorAngles(portal->plane.normal, angles);
    AngleVectors(angles, f, r, u);
    /* the matrix with forward, "left" (vbsp passes right) and up as its columns, times each point */
    for (int i = 0; i < npts; i++) {
        float *p = points[i];
        pts2[i].x = f[1] * p[0] + r[1] * p[1] + u[1] * p[2] + 0.0f;
        pts2[i].y = f[2] * p[0] + r[2] * p[1] + u[2] * p[2] + 0.0f;
    }
    int indices[512];
    int n = Convex2D(pts2, npts, indices, 512);
    dp->firstclip = (unsigned short)numclipportalverts;
    dp->numclip = (unsigned short)n;
    if (n >= 32) Warning("Warning: area portal has %d verts. Could be a vbsp bug.\n", n);
    for (int i = 0; i < n; i++) {
        VectorCopy(points[indices[i]], clipportalverts[numclipportalverts]);
        numclipportalverts++;
    }
    free(points);
    free(pts2);
    free(list.p);
}

void EmitAreaPortals(node_t *headnode) {
    if (c_areas > 256) Error("Map is split into too many unique areas (max = 256)\nProbably too many areaportals");
    numareas = c_areas + 1;
    numareaportals = 1;       /* 0 means an error */
    numclipportalverts = 0;
    for (int src = 1; src <= c_areas; src++) {
        dareas[src].firstareaportal = numareaportals;
        for (int j = 0; j < num_entities; j++) {
            entity_t *e = &entities[j];
            if (!e->areaportalnum) continue;
            if (e->portalareas[0] != src && e->portalareas[1] != src) continue;
            int iside = (e->portalareas[0] == src);
            portal_t *lead = e->portals_into_areas[0];
            if (lead && lead->nodes[0]->area == lead->nodes[1]->area) lead = e->portals_into_areas[1];
            if (!lead) continue;
            dareaportal_t *dp = &dareaportals[numareaportals++];
            memset(dp, 0, sizeof(*dp));
            dp->key = (unsigned short)e->areaportalnum;
            dp->otherarea = (unsigned short)e->portalareas[iside];
            dp->planenum = lead->onnode->planenum;
            if (lead->nodes[0]->area == dp->otherarea) dp->planenum = (dp->planenum & ~1) | (~dp->planenum & 1);
            EmitClipPortalGeometry(headnode, lead, src, dp);
        }
        dareas[src].numareaportals = numareaportals - dareas[src].firstareaportal;
    }
    SetNodeAreaIndices_R(headnode);
}

/* ------------------------------------------------------------------ visible sides */
static vec_t DistFromPlane(winding_t *w, plane_t *plane, float maxdist) {
    float total = 0.0f;
    for (int i = 0; i < w->numpoints; ++i) {
        total += fabsf(DotProduct(plane->normal, w->p[i]) - plane->dist);
        if (total > maxdist) return total;
    }
    return total;
}

/* The brush side that textures a portal: one on the portal's plane, or the nearest one. */
static void FindPortalSide(portal_t *p) {
    int viscontents = VisibleContents(p->nodes[0]->contents ^ p->nodes[1]->contents);
    if (!viscontents) return;
    int planenum = p->onnode->planenum;
    side_t *bestside = NULL;
    float bestdist = 1000000;
    for (int j = 0; j < 2; j++) {
        node_t *n = p->nodes[j];
        for (bspbrush_t *bb = n->brushlist; bb; bb = bb->next) {
            mapbrush_t *brush = bb->original;
            if (!(brush->contents & viscontents)) continue;
            for (int i = 0; i < brush->numsides; i++) {
                side_t *side = &brush->original_sides[i];
                if (side->bevel || side->texinfo == TEXINFO_NODE) continue;
                if ((side->planenum & ~1) == planenum) {
                    bestside = side;
                    bestdist = 0.0f;
                    goto gotit;
                }
                plane_t *p2 = &mapplanes[side->planenum & ~1];
                float dist = DistFromPlane(p->winding, p2, bestdist);
                if (dist < bestdist) {
                    bestside = side;
                    bestdist = dist;
                }
            }
        }
    }
gotit:
    if ((bestdist / p->winding->numpoints) > 2) {
        static int warned = 0;
        if (warned++ < 8) {
            vec3_t c;
            WindingCenter(p->winding, c);
            Warning("\nFindPortalSide: Couldn't find a good match for which brush to assign to a portal near (%.1f %.1f %.1f)\n", c[0], c[1], c[2]);
        }
    }
    p->sidefound = 1;
    p->side = bestside;
}

static void MarkVisibleSides_r(node_t *node) {
    if (node->planenum != PLANENUM_LEAF) {
        MarkVisibleSides_r(node->children[0]);
        MarkVisibleSides_r(node->children[1]);
        return;
    }
    if (!node->contents) return;
    int s;
    for (portal_t *p = node->portals; p; p = p->next[!s]) {
        s = (p->nodes[0] == node);
        if (!p->onnode) continue;
        if (!p->sidefound) FindPortalSide(p);
        if (p->side) p->side->visible = 1;
    }
}

void MarkVisibleSides(tree_t *tree, int start, int end, int detail_screen) {
    for (int i = start; i < end; i++) {
        mapbrush_t *mb = &mapbrushes[i];
        if (detail_screen != FULL_DETAIL) {
            int only = detail_screen == ONLY_DETAIL, detail = (mb->contents & CONTENTS_DETAIL) != 0;
            if (only ^ detail) continue;
        }
        for (int j = 0; j < mb->numsides; j++) mb->original_sides[j].visible = 0;
    }
    MarkVisibleSides_r(tree->headnode);
}

/* The leak trace (.lin): from the outside, through the portals, to the entity that was reached,
 * stepping each time to the neighbour the flood reached one step earlier. */
void LeakFile(tree_t *tree, const char *path) {
    if (!tree->outside_node.occupied) return;
    FILE *f = fopen(path, "w");
    if (!f) Error("Couldn't open %s", path);
    node_t *node = &tree->outside_node;
    vec3_t mid;
    while (node->occupied > 1) {
        portal_t *nextportal = NULL;
        node_t *nextnode = NULL;
        int s = 0, next = node->occupied;
        for (portal_t *p = node->portals; p; p = p->next[!s]) {
            s = (p->nodes[0] == node);
            if (p->nodes[s]->occupied && p->nodes[s]->occupied < next) {
                nextportal = p;
                nextnode = p->nodes[s];
                next = nextnode->occupied;
            }
        }
        if (!nextnode) break;
        node = nextnode;
        WindingCenter(nextportal->winding, mid);
        fprintf(f, "%f %f %f\n", mid[0], mid[1], mid[2]);
    }
    vec3_t origin = {0, 0, 0};
    if (node->occupant) GetVectorForKey(node->occupant, "origin", origin);
    fprintf(f, "%f %f %f\n", origin[0], origin[1], origin[2]);
    fclose(f);
    Msg("Entity %s (%.2f %.2f %.2f) leaked!\n", node->occupant ? ValueForKey(node->occupant, "classname") : "?",
        origin[0], origin[1], origin[2]);
}
