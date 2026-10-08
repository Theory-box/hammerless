/* Brush pieces during the build, splitting them by planes, and building the BSP tree from them. */
#include "hlvbsp.h"

#define PLANESIDE_EPSILON 0.001
vec_t microvolume = 1.0;

bspbrush_t *AllocBrush(int numsides) {
    size_t size = sizeof(bspbrush_t) + (numsides > 6 ? numsides - 6 : 0) * sizeof(side_t);
    return xalloc(size);
}

void FreeBrush(bspbrush_t *b) {
    for (int i = 0; i < b->numsides; i++)
        if (b->sides[i].winding) FreeWinding(b->sides[i].winding);
    free(b);
}

void FreeBrushList(bspbrush_t *b) {
    bspbrush_t *next;
    for (; b; b = next) {
        next = b->next;
        FreeBrush(b);
    }
}

bspbrush_t *CopyBrush(const bspbrush_t *b) {
    bspbrush_t *n = AllocBrush(b->numsides);
    memcpy(n, b, sizeof(bspbrush_t) - sizeof(b->sides) + sizeof(side_t) * b->numsides);
    for (int i = 0; i < b->numsides; i++)
        if (b->sides[i].winding) n->sides[i].winding = CopyWinding(b->sides[i].winding);
    return n;
}

int CountBrushList(bspbrush_t *b) {
    int c = 0;
    for (; b; b = b->next) c++;
    return c;
}

void BoundBrush(bspbrush_t *b) {
    ClearBounds(b->mins, b->maxs);
    for (int i = 0; i < b->numsides; i++) {
        winding_t *w = b->sides[i].winding;
        if (!w) continue;
        for (int j = 0; j < w->numpoints; j++) AddPointToBounds(w->p[j], b->mins, b->maxs);
    }
}

/* A point inside the brush, found by pushing the origin behind each plane in turn (a few rounds). */
static void PointInsideBrush(bspbrush_t *b, vec3_t inside) {
    VectorClear(inside);
    int ok = 0;
    for (int k = 0; k < 4 && !ok; k++) {
        ok = 1;
        for (int i = 0; i < b->numsides; i++) {
            plane_t *plane = &mapplanes[b->sides[i].planenum];
            float d = DotProduct(plane->normal, inside) - plane->dist;
            if (d < 0) {
                ok = 0;
                vec3_t t;
                VectorScale(plane->normal, d, t);
                VectorSubtract(inside, t, inside);
            }
        }
    }
}

/* Side windings computed relative to a point inside the brush (precision), then moved back. */
void CreateBrushWindings(bspbrush_t *b) {
    vec3_t inside, offset, back;
    PointInsideBrush(b, inside);
    VectorNegate(inside, offset);
    VectorCopy(inside, back);
    for (int i = 0; i < b->numsides; i++) {
        side_t *side = &b->sides[i];
        plane_t *plane = &mapplanes[side->planenum];
        winding_t *w = BaseWindingForPlane(plane->normal, plane->dist + DotProduct(plane->normal, offset));
        for (int j = 0; j < b->numsides && w; j++) {
            if (i == j || b->sides[j].bevel) continue;
            plane = &mapplanes[b->sides[j].planenum ^ 1];
            ChopWindingInPlace(&w, plane->normal, plane->dist + DotProduct(plane->normal, offset), 0);
        }
        if (w) TranslateWinding(w, back);
        side->winding = w;
    }
    BoundBrush(b);
}

bspbrush_t *BrushFromBounds(const vec3_t mins, const vec3_t maxs) {
    bspbrush_t *b = AllocBrush(6);
    b->numsides = 6;
    vec3_t normal;
    for (int i = 0; i < 3; i++) {
        VectorClear(normal);
        normal[i] = 1;
        b->sides[i].planenum = FindFloatPlane(normal, maxs[i]);
        VectorClear(normal);
        normal[i] = -1;
        b->sides[3 + i].planenum = FindFloatPlane(normal, -mins[i]);
    }
    CreateBrushWindings(b);
    return b;
}

/* (vbsp's x87 code: each height in double, y, x, z, rounded to float; the sum kept as a float;
   the result is sum * 0.33333334f, unrounded - measured) */
double BrushVolume(bspbrush_t *b) {
    if (!b) return 0;
    int i;
    winding_t *w = NULL;
    for (i = 0; i < b->numsides; i++) {
        w = b->sides[i].winding;
        if (w) break;
    }
    if (!w) return 0;
    vec3_t corner;
    VectorCopy(w->p[0], corner);
    float volume = 0;
    for (; i < b->numsides; i++) {
        w = b->sides[i].winding;
        if (!w) continue;
        plane_t *plane = &mapplanes[b->sides[i].planenum];
        float d = (float)-((((double)plane->normal[1] * corner[1] + (double)plane->normal[0] * corner[0]) +
                            (double)plane->normal[2] * corner[2]) - plane->dist);
        float area = WindingArea(w);
        volume = (float)((double)area * d + volume);
    }
    return (double)volume * (double)(1.0f / 3.0f);
}

node_t *AllocNode(void) {
    node_t *n = xalloc(sizeof(*n));
    n->diskid = -1;
    return n;
}

tree_t *AllocTree(void) {
    tree_t *t = xalloc(sizeof(*t));
    ClearBounds(t->mins, t->maxs);
    return t;
}

int BoxOnPlaneSide(const vec3_t mins, const vec3_t maxs, const plane_t *plane) {
    int side = 0;
    if (plane->type < 3) {
        if (maxs[plane->type] > plane->dist + PLANESIDE_EPSILON) side |= PSIDE_FRONT;
        if (mins[plane->type] < plane->dist - PLANESIDE_EPSILON) side |= PSIDE_BACK;
        return side;
    }
    vec3_t corners[2];
    for (int i = 0; i < 3; i++) {
        if (plane->normal[i] < 0) {
            corners[0][i] = mins[i];
            corners[1][i] = maxs[i];
        } else {
            corners[1][i] = mins[i];
            corners[0][i] = maxs[i];
        }
    }
    vec_t d1 = DotProduct(plane->normal, corners[0]) - plane->dist;
    vec_t d2 = DotProduct(plane->normal, corners[1]) - plane->dist;
    if (d1 >= PLANESIDE_EPSILON) side = PSIDE_FRONT;
    if (d2 < PLANESIDE_EPSILON) side |= PSIDE_BACK;
    return side;
}

/* Which side of the plane the brush is on, and how many of its visible faces it would cut. */
static int TestBrushToPlanenum(bspbrush_t *b, int planenum, int *numsplits, int *hintsplit, int *epsilonbrush) {
    *numsplits = 0;
    *hintsplit = 0;
    for (int i = 0; i < b->numsides; i++) {
        int num = b->sides[i].planenum;
        if (num >= 0x10000) Error("bad planenum");
        if (num == planenum) return PSIDE_BACK | PSIDE_FACING;
        if (num == (planenum ^ 1)) return PSIDE_FRONT | PSIDE_FACING;
    }
    plane_t *plane = &mapplanes[planenum];
    int s = BoxOnPlaneSide(b->mins, b->maxs, plane);
    if (s != PSIDE_BOTH) return s;
    vec_t d_front = 0, d_back = 0;
    for (int i = 0; i < b->numsides; i++) {
        if (b->sides[i].texinfo == TEXINFO_NODE) continue;
        if (!b->sides[i].visible) continue;
        winding_t *w = b->sides[i].winding;
        if (!w) continue;
        int front = 0, back = 0;
        for (int j = 0; j < w->numpoints; j++) {
            vec_t d = DotProduct(w->p[j], plane->normal) - plane->dist;
            if (d > d_front) d_front = d;
            if (d < d_back) d_back = d;
            if (d > 0.1) front = 1;
            if (d < -0.1) back = 1;
        }
        if (front && back && !(b->sides[i].surf & SURF_SKIP)) {
            (*numsplits)++;
            if (b->sides[i].surf & SURF_HINT) *hintsplit = 1;
        }
    }
    if ((d_front > 0.0 && d_front < 1.0) || (d_back < 0.0 && d_back > -1.0)) (*epsilonbrush)++;
    return s;
}

static void CheckPlaneAgainstParents(int pnum, node_t *node) {
    for (node_t *p = node->parent; p; p = p->parent)
        if (p->planenum == pnum) Error("Tried parent");
}

static int CheckPlaneAgainstVolume(int pnum, node_t *node) {
    bspbrush_t *front, *back;
    SplitBrush(node->volume, pnum, &front, &back);
    int good = front && back;
    if (front) FreeBrush(front);
    if (back) FreeBrush(back);
    return good;
}

/* The splitting plane: scored from sides of the brushes (visible ones first, then the rest). */
static side_t *SelectSplitSide(bspbrush_t *brushes, node_t *node) {
    side_t *bestside = NULL;
    int bestvalue = -99999;
    int hintsplit = 0;
    for (int pass = 0; pass < 2; pass++) {
        for (bspbrush_t *brush = brushes; brush; brush = brush->next) {
            for (int i = 0; i < brush->numsides; i++) {
                side_t *side = brush->sides + i;
                if (side->bevel || !side->winding || side->texinfo == TEXINFO_NODE || side->tested) continue;
                if (side->surf & SURF_SKIP) continue;
                if ((side->visible != 0) ^ (pass < 1)) continue;
                int pnum = side->planenum & ~1;
                CheckPlaneAgainstParents(pnum, node);
                if (!CheckPlaneAgainstVolume(pnum, node)) continue;
                int front = 0, back = 0, facing = 0, splits = 0, epsilonbrush = 0, bsplits;
                for (bspbrush_t *test = brushes; test; test = test->next) {
                    int s = TestBrushToPlanenum(test, pnum, &bsplits, &hintsplit, &epsilonbrush);
                    splits += bsplits;
                    if (bsplits && (s & PSIDE_FACING)) Error("PSIDE_FACING with splits");
                    test->testside = s;
                    if (s & PSIDE_FACING) {
                        facing++;
                        for (int j = 0; j < test->numsides; j++)
                            if ((test->sides[j].planenum & ~1) == pnum) test->sides[j].tested = 1;
                    }
                    if (s & PSIDE_FRONT) front++;
                    if (s & PSIDE_BACK) back++;
                }
                int value = 5 * facing - 5 * splits - abs(front - back);
                if (mapplanes[pnum].type < 3) value += 5;      /* axial is better */
                value -= epsilonbrush * 1000;
                if (side->surf & SURF_TRANS) value -= 500;
                if (hintsplit && !(side->surf & SURF_HINT)) value = -9999999;
                if (side->contents & (CONTENTS_WATER | CONTENTS_SLIME)) value = 9999999;
                if (value > bestvalue) {
                    bestvalue = value;
                    bestside = side;
                    for (bspbrush_t *test = brushes; test; test = test->next) test->side = test->testside;
                }
            }
        }
        if (bestside) break;
    }
    for (bspbrush_t *brush = brushes; brush; brush = brush->next)
        for (int i = 0; i < brush->numsides; i++) brush->sides[i].tested = 0;
    return bestside;
}

static int BrushMostlyOnSide(bspbrush_t *b, plane_t *plane) {
    vec_t max = 0;
    int side = PSIDE_FRONT;
    for (int i = 0; i < b->numsides; i++) {
        winding_t *w = b->sides[i].winding;
        if (!w) continue;
        for (int j = 0; j < w->numpoints; j++) {
            vec_t d = DotProduct(w->p[j], plane->normal) - plane->dist;
            if (d > max) {
                max = d;
                side = PSIDE_FRONT;
            }
            if (-d > max) {
                max = -d;
                side = PSIDE_BACK;
            }
        }
    }
    return side;
}

/* Two new brushes on either side of the plane; the original stays as it is. */
void SplitBrush(bspbrush_t *brush, int planenum, bspbrush_t **front, bspbrush_t **back) {
    *front = *back = NULL;
    plane_t *plane = &mapplanes[planenum];
    float d_front = 0, d_back = 0;
    for (int i = 0; i < brush->numsides; i++) {
        winding_t *w = brush->sides[i].winding;
        if (!w) continue;
        for (int j = 0; j < w->numpoints; j++) {
            float d = DotProduct(w->p[j], plane->normal) - plane->dist;
            if (d > 0 && d > d_front) d_front = d;
            if (d < 0 && d < d_back) d_back = d;
        }
    }
    if (d_front < 0.1) {
        *back = CopyBrush(brush);
        return;
    }
    if (d_back > -0.1) {
        *front = CopyBrush(brush);
        return;
    }
    /* the cut polygon, computed near the brush's centre */
    vec3_t offset, neg;
    for (int k = 0; k < 3; k++) offset[k] = -0.5f * (brush->mins[k] + brush->maxs[k]);
    VectorNegate(offset, neg);
    winding_t *w = BaseWindingForPlane(plane->normal, plane->dist + DotProduct(plane->normal, offset));
    for (int i = 0; i < brush->numsides && w; i++) {
        plane_t *plane2 = &mapplanes[brush->sides[i].planenum ^ 1];
        ChopWindingInPlace(&w, plane2->normal, plane2->dist + DotProduct(plane2->normal, offset), 0);
    }
    if (!w || WindingIsTiny(w)) {
        if (w) FreeWinding(w);
        int side = BrushMostlyOnSide(brush, plane);
        if (side == PSIDE_FRONT) *front = CopyBrush(brush);
        if (side == PSIDE_BACK) *back = CopyBrush(brush);
        return;
    }
    if (WindingIsHuge(w)) Msg("WARNING: huge winding\n");
    TranslateWinding(w, neg);
    winding_t *midwinding = w;
    bspbrush_t *b[2];
    for (int i = 0; i < 2; i++) {
        b[i] = AllocBrush(brush->numsides + 1);
        b[i]->original = brush->original;
    }
    for (int i = 0; i < brush->numsides; i++) {
        side_t *s = &brush->sides[i];
        w = s->winding;
        if (!w) continue;
        winding_t *cw[2];
        ClipWindingEpsilonOffset(w, plane->normal, plane->dist, 0, &cw[0], &cw[1], offset);
        for (int j = 0; j < 2; j++) {
            if (!cw[j]) continue;
            side_t *cs = &b[j]->sides[b[j]->numsides];
            b[j]->numsides++;
            *cs = *s;
            cs->winding = cw[j];
            cs->tested = 0;
        }
    }
    for (int i = 0; i < 2; i++) {
        BoundBrush(b[i]);
        int j;
        for (j = 0; j < 3; j++)
            if (b[i]->mins[j] < MIN_COORD_INTEGER || b[i]->maxs[j] > MAX_COORD_INTEGER) break;
        if (b[i]->numsides < 3 || j < 3) {
            FreeBrush(b[i]);
            b[i] = NULL;
        }
    }
    if (!(b[0] && b[1])) {
        if (b[0]) {
            FreeBrush(b[0]);
            *front = CopyBrush(brush);
        }
        if (b[1]) {
            FreeBrush(b[1]);
            *back = CopyBrush(brush);
        }
        FreeWinding(midwinding);
        return;
    }
    for (int i = 0; i < 2; i++) {
        side_t *cs = &b[i]->sides[b[i]->numsides];
        b[i]->numsides++;
        memset(cs, 0, sizeof(*cs));
        cs->planenum = planenum ^ i ^ 1;
        cs->texinfo = TEXINFO_NODE;
        cs->winding = i == 0 ? CopyWinding(midwinding) : midwinding;
    }
    for (int i = 0; i < 2; i++) {
        if (BrushVolume(b[i]) < 1.0) {
            FreeBrush(b[i]);
            b[i] = NULL;
        }
    }
    *front = b[0];
    *back = b[1];
}

static void SplitBrushList(bspbrush_t *brushes, node_t *node, bspbrush_t **front, bspbrush_t **back) {
    *front = *back = NULL;
    for (bspbrush_t *brush = brushes; brush; brush = brush->next) {
        int sides = brush->side;
        if (sides == PSIDE_BOTH) {
            bspbrush_t *nb, *nb2;
            SplitBrush(brush, node->planenum, &nb, &nb2);
            if (nb) { nb->next = *front; *front = nb; }
            if (nb2) { nb2->next = *back; *back = nb2; }
            continue;
        }
        bspbrush_t *nb = CopyBrush(brush);
        if (sides & PSIDE_FACING) {
            /* sides on the node's plane are done: they become the node's faces */
            for (int i = 0; i < nb->numsides; i++)
                if ((nb->sides[i].planenum & ~1) == node->planenum) nb->sides[i].texinfo = TEXINFO_NODE;
        }
        if (sides & PSIDE_FRONT) { nb->next = *front; *front = nb; continue; }
        if (sides & PSIDE_BACK) { nb->next = *back; *back = nb; continue; }
        FreeBrush(nb);
    }
}

static void LeafNode(node_t *node, bspbrush_t *brushes) {
    node->planenum = PLANENUM_LEAF;
    node->contents = 0;
    for (bspbrush_t *b = brushes; b; b = b->next) {
        if (b->original->contents & CONTENTS_SOLID) {
            int i;
            for (i = 0; i < b->numsides; i++)
                if (b->sides[i].texinfo != TEXINFO_NODE) break;
            if (i == b->numsides) {
                node->contents = CONTENTS_SOLID;
                break;
            }
        }
        node->contents |= b->original->contents;
    }
    node->brushlist = brushes;
}

static node_t *BuildTree_r(node_t *node, bspbrush_t *brushes) {
    side_t *bestside = SelectSplitSide(brushes, node);
    if (!bestside) {
        node->side = NULL;
        node->planenum = -1;
        LeafNode(node, brushes);
        return node;
    }
    node->side = bestside;
    node->planenum = bestside->planenum & ~1;
    bspbrush_t *children[2];
    SplitBrushList(brushes, node, &children[0], &children[1]);
    FreeBrushList(brushes);
    for (int i = 0; i < 2; i++) {
        node->children[i] = AllocNode();
        node->children[i]->parent = node;
    }
    SplitBrush(node->volume, node->planenum, &node->children[0]->volume, &node->children[1]->volume);
    for (int i = 0; i < 2; i++) node->children[i] = BuildTree_r(node->children[i], children[i]);
    return node;
}

tree_t *BrushBSP(bspbrush_t *list, const vec3_t mins, const vec3_t maxs) {
    tree_t *tree = AllocTree();
    for (bspbrush_t *b = list; b; b = b->next) {
        if (BrushVolume(b) < microvolume) Warning("Brush %i: WARNING, microbrush\n", b->original->id);
        AddPointToBounds(b->mins, tree->mins, tree->maxs);
        AddPointToBounds(b->maxs, tree->mins, tree->maxs);
    }
    node_t *node = AllocNode();
    node->volume = BrushFromBounds(mins, maxs);
    tree->headnode = node;
    BuildTree_r(node, list);
    return tree;
}

void RemovePortalFromNode(portal_t *portal, node_t *l);

void FreeTreePortals_r(node_t *node) {
    if (node->planenum != PLANENUM_LEAF) {
        FreeTreePortals_r(node->children[0]);
        FreeTreePortals_r(node->children[1]);
    }
    portal_t *p, *nextp;
    for (p = node->portals; p; p = nextp) {
        int s = (p->nodes[1] == node);
        nextp = p->next[s];
        RemovePortalFromNode(p, p->nodes[!s]);
        FreePortal(p);
    }
    node->portals = NULL;
}

static void FreeTree_r(node_t *node) {
    if (node->planenum != PLANENUM_LEAF) {
        FreeTree_r(node->children[0]);
        FreeTree_r(node->children[1]);
    }
    FreeBrushList(node->brushlist);
    face_t *f, *nextf;
    for (f = node->faces; f; f = nextf) {
        nextf = f->next;
        FreeFace(f);
    }
    if (node->volume) FreeBrush(node->volume);
    free(node);
}

void FreeTree(tree_t *tree) {
    FreeTreePortals_r(tree->headnode);
    FreeTree_r(tree->headnode);
    free(tree);
}

/* Merge nodes whose children are both solid into one solid leaf. */
static void PruneNodes_r(node_t *node) {
    if (node->planenum == PLANENUM_LEAF) return;
    PruneNodes_r(node->children[0]);
    PruneNodes_r(node->children[1]);
    if ((node->children[0]->contents & CONTENTS_SOLID) && (node->children[1]->contents & CONTENTS_SOLID)) {
        if (node->faces) Error("node->faces seperating CONTENTS_SOLID");
        if (node->children[0]->faces || node->children[1]->faces) Error("!node->faces with children");
        node->planenum = PLANENUM_LEAF;
        node->contents = CONTENTS_SOLID;
        if (node->brushlist) Error("PruneNodes: node->brushlist");
        node->brushlist = node->children[1]->brushlist;
        bspbrush_t *b, *next;
        for (b = node->children[0]->brushlist; b; b = next) {
            next = b->next;
            b->next = node->brushlist;
            node->brushlist = b;
        }
    }
}

void PruneNodes(node_t *node) { PruneNodes_r(node); }

void RemoveAreaPortalBrushes_R(node_t *node) {
    if (node->planenum == PLANENUM_LEAF) {
        bspbrush_t **prev = &node->brushlist;
        for (bspbrush_t *b = node->brushlist; b; b = b->next) {
            if (b->original->contents == CONTENTS_AREAPORTAL) *prev = b->next;
            else prev = &b->next;
        }
    } else {
        RemoveAreaPortalBrushes_R(node->children[0]);
        RemoveAreaPortalBrushes_R(node->children[1]);
    }
}
