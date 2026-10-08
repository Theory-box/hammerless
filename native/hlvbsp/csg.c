/* CSG: brushes clipped to a block, and overlapping brushes carved so none overlap. */
#include "hlvbsp.h"

static int minplanenums[3], maxplanenums[3];

static bspbrush_t *SubtractBrush(bspbrush_t *a, bspbrush_t *b) {
    bspbrush_t *front, *back, *out = NULL, *in = a;
    for (int i = 0; i < b->numsides && in; i++) {
        SplitBrush(in, b->sides[i].planenum, &front, &back);
        if (in != a) FreeBrush(in);
        if (front) {
            front->next = out;
            out = front;
        }
        in = back;
    }
    if (in) FreeBrush(in);
    else {
        FreeBrushList(out);     /* didn't really intersect */
        return a;
    }
    return out;
}

bspbrush_t *IntersectBrush(bspbrush_t *a, bspbrush_t *b) {
    bspbrush_t *front, *back, *in = a;
    for (int i = 0; i < b->numsides && in; i++) {
        SplitBrush(in, b->sides[i].planenum, &front, &back);
        if (in != a) FreeBrush(in);
        if (front) FreeBrush(front);
        in = back;
    }
    if (in == a || !in) return NULL;
    in->next = NULL;
    return in;
}

static int BrushesDisjoint(bspbrush_t *a, bspbrush_t *b) {
    for (int i = 0; i < 3; i++)
        if (a->mins[i] >= b->maxs[i] || a->maxs[i] <= b->mins[i]) return 1;
    for (int i = 0; i < a->numsides; i++)
        for (int j = 0; j < b->numsides; j++)
            if (a->sides[i].planenum == (b->sides[j].planenum ^ 1)) return 1;
    return 0;
}

/* Cut to the block's x / y range; sides on the block's edges get no texture. */
static bspbrush_t *ClipBrushToBox(bspbrush_t *brush, const vec3_t clipmins, const vec3_t clipmaxs) {
    bspbrush_t *front, *back;
    for (int j = 0; j < 2; j++) {
        if (brush->maxs[j] > clipmaxs[j]) {
            SplitBrush(brush, maxplanenums[j], &front, &back);
            FreeBrush(brush);
            if (front) FreeBrush(front);
            brush = back;
            if (!brush) return NULL;
        }
        if (brush->mins[j] < clipmins[j]) {
            SplitBrush(brush, minplanenums[j], &front, &back);
            FreeBrush(brush);
            if (back) FreeBrush(back);
            brush = front;
            if (!brush) return NULL;
        }
    }
    for (int i = 0; i < brush->numsides; i++) {
        int p = brush->sides[i].planenum & ~1;
        if (p == maxplanenums[0] || p == maxplanenums[1] || p == minplanenums[0] || p == minplanenums[1]) {
            brush->sides[i].texinfo = TEXINFO_NODE;
            brush->sides[i].visible = 0;
        }
    }
    return brush;
}

bspbrush_t *CreateClippedBrush(mapbrush_t *mb, const vec3_t clipmins, const vec3_t clipmaxs) {
    int n = mb->numsides;
    if (!n) return NULL;
    for (int j = 0; j < 3; j++)
        if (mb->mins[j] >= clipmaxs[j] || mb->maxs[j] <= clipmins[j]) return NULL;
    bspbrush_t *nb = AllocBrush(n);
    nb->original = mb;
    nb->numsides = n;
    memcpy(nb->sides, mb->original_sides, n * sizeof(side_t));
    for (int j = 0; j < n; j++) {
        if (nb->sides[j].winding) nb->sides[j].winding = CopyWinding(nb->sides[j].winding);
        if (nb->sides[j].surf & SURF_HINT) nb->sides[j].visible = 1;     /* hints are always visible */
    }
    VectorCopy(mb->mins, nb->mins);
    VectorCopy(mb->maxs, nb->maxs);
    return ClipBrushToBox(nb, clipmins, clipmaxs);
}

void ComputeBoundingPlanes(const vec3_t clipmins, const vec3_t clipmaxs) {
    vec3_t normal;
    for (int i = 0; i < 2; i++) {
        VectorClear(normal);
        normal[i] = 1;
        maxplanenums[i] = FindFloatPlane(normal, clipmaxs[i]);
        VectorClear(normal);
        normal[i] = 1;
        minplanenums[i] = FindFloatPlane(normal, clipmins[i]);
    }
}

static void CopyMatchingTexinfos(side_t *dest, int numdest, const bspbrush_t *source) {
    for (int i = 0; i < numdest; i++) {
        side_t *side = &dest[i];
        plane_t *plane = &mapplanes[side->planenum];
        mapbrush_t *sb = source->original;
        const side_t *ss = sb->original_sides, *best = NULL;
        float bestdot = -1.0f;
        for (int j = 0; j < sb->numsides; ++j, ++ss) {
            if (ss->texinfo == TEXINFO_NODE) continue;
            plane_t *sp = &mapplanes[ss->planenum];
            float dot = DotProduct(plane->normal, sp->normal);
            if (dot == 1.0f || side->planenum == ss->planenum) {
                best = ss;
                break;
            } else if (dot > bestdot) {
                best = ss;
                bestdot = dot;
            }
        }
        if (best) {
            side->texinfo = best->texinfo;
            if (side->original) side->original->texinfo = side->texinfo;
        }
    }
}

/* An areaportal inside water takes the water's contents (and its surface's texture). */
void FixupAreaportalWaterBrushes(bspbrush_t *list) {
    for (bspbrush_t *ap = list; ap; ap = ap->next) {
        if (!(ap->original->contents & CONTENTS_AREAPORTAL)) continue;
        for (bspbrush_t *water = list; water; water = water->next) {
            if (water->original->contents & CONTENTS_AREAPORTAL) continue;
            if (!(water->original->contents & MASK_SPLITAREAPORTAL)) continue;
            if (BrushesDisjoint(ap, water)) continue;
            bspbrush_t *x = IntersectBrush(ap, water);
            if (!x) continue;
            FreeBrush(x);
            ap->original->contents |= water->original->contents;
            CopyMatchingTexinfos(ap->sides, ap->numsides, water);
            CopyMatchingTexinfos(ap->original->original_sides, ap->original->numsides, water);
        }
    }
}

bspbrush_t *MakeBspBrushList(int start, int end, const vec3_t clipmins, const vec3_t clipmaxs, int detail_screen) {
    ComputeBoundingPlanes(clipmins, clipmaxs);
    bspbrush_t *list = NULL;
    for (int i = start; i < end; i++) {
        mapbrush_t *mb = &mapbrushes[i];
        if (detail_screen != FULL_DETAIL) {
            int only = detail_screen == ONLY_DETAIL, detail = (mb->contents & CONTENTS_DETAIL) != 0;
            if (only ^ detail) continue;
        }
        bspbrush_t *nb = CreateClippedBrush(mb, clipmins, clipmaxs);
        if (nb) {
            nb->next = list;
            list = nb;
        }
    }
    return list;
}

static bspbrush_t *AddBrushListToTail(bspbrush_t *list, bspbrush_t *tail) {
    bspbrush_t *walk, *next;
    for (walk = list; walk; walk = next) {
        next = walk->next;
        walk->next = NULL;
        tail->next = walk;
        tail = walk;
    }
    return tail;
}

/* A new list without skip1 (and in reverse order, as vbsp builds it). */
static bspbrush_t *CullList(bspbrush_t *list, bspbrush_t *skip1) {
    bspbrush_t *newlist = NULL, *next;
    for (; list; list = next) {
        next = list->next;
        if (list == skip1) {
            FreeBrush(list);
            continue;
        }
        list->next = newlist;
        newlist = list;
    }
    return newlist;
}

/* May b1 cut into b2? */
static int BrushGE(bspbrush_t *b1, bspbrush_t *b2) {
    if ((b2->original->contents & MASK_SPLITAREAPORTAL) && (b1->original->contents & CONTENTS_AREAPORTAL)) return 1;
    if ((b1->original->contents & CONTENTS_DETAIL) && !(b2->original->contents & CONTENTS_DETAIL)) return 0;
    if (b1->original->contents & CONTENTS_SOLID) return 1;
    if ((b1->original->contents & TRANSPARENT_CONTENTS) && (b2->original->contents & TRANSPARENT_CONTENTS)) return 1;
    return 0;
}

/* Carve intersecting brushes into the fewest non-intersecting pieces. */
bspbrush_t *ChopBrushes(bspbrush_t *head) {
    bspbrush_t *b1, *b2, *next, *tail, *keep = NULL, *sub, *sub2;
    int c1, c2;
newlist:
    if (!head) return NULL;
    for (tail = head; tail->next; tail = tail->next);
    for (b1 = head; b1; b1 = next) {
        next = b1->next;
        for (b2 = b1->next; b2; b2 = b2->next) {
            if (BrushesDisjoint(b1, b2)) continue;
            sub = sub2 = NULL;
            c1 = c2 = 999999;
            if (BrushGE(b2, b1)) {
                sub = SubtractBrush(b1, b2);
                if (sub == b1) continue;
                if (!sub) {             /* b1 is swallowed by b2 */
                    head = CullList(b1, b1);
                    goto newlist;
                }
                c1 = CountBrushList(sub);
            }
            if (BrushGE(b1, b2)) {
                sub2 = SubtractBrush(b2, b1);
                if (sub2 == b2) continue;
                if (!sub2) {            /* b2 is swallowed by b1 */
                    FreeBrushList(sub);
                    head = CullList(b1, b2);
                    goto newlist;
                }
                c2 = CountBrushList(sub2);
            }
            if (!sub && !sub2) continue;
            if (c1 > 1 && c2 > 1) {
                int ca = b1->original->contents, cb = b2->original->contents;
                /* (L4D2's vbsp also lets solid cut water into pieces: measured from its code) */
                if (!((ca & cb) & CONTENTS_DETAIL) && !((ca | cb) & CONTENTS_AREAPORTAL) &&
                    ((ca | cb) & (CONTENTS_SOLID | CONTENTS_WATER)) != (CONTENTS_SOLID | CONTENTS_WATER)) {
                    if (sub2) FreeBrushList(sub2);
                    if (sub) FreeBrushList(sub);
                    continue;
                }
            }
            if (c1 < c2) {
                if (sub2) FreeBrushList(sub2);
                tail = AddBrushListToTail(sub, tail);
                head = CullList(b1, b1);
                goto newlist;
            } else {
                if (sub) FreeBrushList(sub);
                tail = AddBrushListToTail(sub2, tail);
                head = CullList(b1, b2);
                goto newlist;
            }
        }
        if (!b2) {          /* b1 doesn't intersect anything any more: keep it */
            b1->next = keep;
            keep = b1;
        }
    }
    return keep;
}
