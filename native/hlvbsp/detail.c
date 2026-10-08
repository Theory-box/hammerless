/* Detail brushes: not part of the vis tree. Their visible sides become faces listed in the world
 * leaves they touch, and their pieces are filtered into those leaves for collision. */
#include "hlvbsp.h"

static face_t *CopyFace(face_t *f) {
    face_t *n = NewFaceFromFace(f);
    n->w = CopyWinding(f->w);
    return n;
}

static void MergeBrush_r(node_t *node, bspbrush_t *brush) {
    if (node->planenum == PLANENUM_LEAF) {
        if (node->contents & CONTENTS_SOLID) FreeBrush(brush);
        else {
            brush->next = node->brushlist;
            node->brushlist = brush;
        }
        return;
    }
    bspbrush_t *front, *back;
    SplitBrush(brush, node->planenum, &front, &back);
    FreeBrush(brush);
    if (front) MergeBrush_r(node->children[0], front);
    if (back) MergeBrush_r(node->children[1], back);
}

static int MergeFace_r(node_t *node, face_t *face, face_t *original) {
    int referenced = 0;
    if (node->planenum == PLANENUM_LEAF) {
        if (node->contents & CONTENTS_SOLID) {
            FreeFace(face);
            return 0;
        }
        leafface_t *l = xalloc(sizeof(*l));
        l->face = original;
        l->next = node->leaffaces;
        node->leaffaces = l;
        referenced = 1;
    } else {
        plane_t *plane = &mapplanes[node->planenum];
        winding_t *fw, *bw, *on;
        vec3_t offset;
        WindingCenter(face->w, offset);
        VectorNegate(offset, offset);
        ClassifyWindingEpsilonOffset(face->w, plane->normal, plane->dist, 0.001, &fw, &bw, &on, offset);
        if (on) {
            if (DotProduct(mapplanes[face->planenum].normal, mapplanes[node->planenum].normal) > 0) fw = on;
            else bw = on;
        }
        if (fw) {
            face_t *t = NewFaceFromFace(face);
            t->w = fw;
            referenced = MergeFace_r(node->children[0], t, original);
        }
        if (bw) {
            face_t *t = NewFaceFromFace(face);
            t->w = bw;
            int test = MergeFace_r(node->children[1], t, original);
            referenced = referenced || test;
        }
    }
    FreeFace(face);
    return referenced;
}

static face_t *FilterFacesIntoTree(tree_t *out, face_t *faces) {
    face_t *list = NULL;
    for (face_t *f = faces; f; f = f->next) {
        if (f->merged || f->split[0] || f->split[1]) continue;
        face_t *tmp = CopyFace(f), *original = CopyFace(f);
        if (MergeFace_r(out->headnode, tmp, original)) {
            original->portal = NULL;
            original->next = list;
            list = original;
        } else FreeFace(original);
    }
    return list;
}

static void TryMergeFaceList(face_t **facelist) {
    face_t **planelist = xalloc(sizeof(face_t *) * nummapplanes);
    face_t *faces = *facelist, *out = NULL;
    while (faces) {
        face_t *next = faces->next;
        if (faces->merged || faces->split[0] || faces->split[1]) Error("Split face in merge list!");
        faces->next = planelist[faces->planenum];
        planelist[faces->planenum] = faces;
        faces = next;
    }
    int merged = 0;
    for (int i = 0; i < nummapplanes; i++) {
        if (planelist[i]) MergeFaceList(&planelist[i]);
        face_t *list = planelist[i];
        while (list) {
            face_t *next = list->next;
            if (list->merged) merged++;
            list->next = out;
            out = list;
            list = next;
        }
    }
    if (merged) Msg("\nMerged %d detail faces...", merged);
    free(planelist);
    *facelist = out;
}

static int BrushBoxOverlap(bspbrush_t *a, bspbrush_t *b) {
    if (a == b) return 0;
    for (int i = 0; i < 3; i++)
        if (a->mins[i] > b->maxs[i] || a->maxs[i] < b->mins[i]) return 0;
    return 1;
}

/* Clip a face by a brush that shares its plane: returns 1 when the brush covers it entirely, else
 * the outside pieces (if any) are put in *out. */
static int ClipFaceToBrush(face_t *face, bspbrush_t *brush, face_t **out) {
    int planenum = face->planenum & ~1, found = -1, i;
    for (i = 0; i < brush->numsides && found < 0; i++)
        if ((brush->sides[i].planenum & ~1) == planenum) found = i;
    vec3_t offset;
    for (int k = 0; k < 3; k++) offset[k] = -0.5f * (brush->maxs[k] + brush->mins[k]);
    face_t *current = CopyFace(face);
    if (found >= 0) {
        int *sorted = xalloc(sizeof(int) * (brush->numsides + 1)), n = 0;
        /* axial planes first */
        for (i = 0; i < brush->numsides; i++) {
            if (brush->sides[i].bevel) continue;
            if (mapplanes[brush->sides[i].planenum].type <= PLANE_Z) {
                memmove(sorted + 1, sorted, sizeof(int) * n);
                sorted[0] = i;
            } else sorted[n] = i;
            n++;
        }
        for (i = 0; i < n; i++) {
            int index = sorted[i];
            if (index == found) continue;
            plane_t *plane = &mapplanes[brush->sides[index].planenum];
            winding_t *fw, *bw;
            ClipWindingEpsilonOffset(current->w, plane->normal, plane->dist, 0.001, &fw, &bw, offset);
            if (!bw || WindingIsTiny(bw)) {
                FreeFaceList(*out);
                *out = NULL;
                if (fw) FreeWinding(fw);
                if (bw) FreeWinding(bw);
                break;
            }
            if (fw && !WindingIsTiny(fw)) {
                face_t *f = NewFaceFromFace(face);
                f->w = fw;
                f->next = *out;
                *out = f;
            } else if (fw) FreeWinding(fw);
            FreeWinding(current->w);
            current->w = bw;
        }
        FreeFace(current);
        free(sorted);
        if (!*out && i == n) return 1;
        return 0;
    }
    FreeFace(current);
    return 0;
}

static face_t *MakeBrushFace(side_t *original, winding_t *w) {
    face_t *f = AllocFace();
    f->w = CopyWinding(w);
    f->originalface = original;
    f->texinfo = original->texinfo;
    f->dispinfo = -1;
    f->planenum = original->planenum;
    f->contents = original->contents;
    return f;
}

static side_t *FindOriginalSide(mapbrush_t *mb, side_t *bspside) {
    side_t *best = NULL;
    float bestdot = 0;
    plane_t *p1 = mapplanes + bspside->planenum;
    for (int i = 0; i < mb->numsides; i++) {
        side_t *side = &mb->original_sides[i];
        if (side->bevel || side->texinfo == TEXINFO_NODE) continue;
        if ((side->planenum & ~1) == (bspside->planenum & ~1)) return side;
        plane_t *p2 = &mapplanes[side->planenum & ~1];
        float dot = DotProduct(p1->normal, p2->normal);
        if (dot > bestdot) {
            bestdot = dot;
            best = side;
        }
    }
    if (!best) Error("Bad detail brush side\n");
    return best;
}

static int GetListOfCutBrushes(bspbrush_t **out, bspbrush_t *source, bspbrush_t *list) {
    int n = 0;
    mapbrush_t *mb = source->original;
    for (bspbrush_t *walk = list; walk; walk = walk->next) {
        if (walk == source) continue;
        if ((walk->original->contents & TRANSPARENT_CONTENTS) && !(mb->contents & TRANSPARENT_CONTENTS)) continue;
        if (!(walk->original->contents & ALL_VISIBLE_CONTENTS)) continue;
        if (!BrushBoxOverlap(source, walk)) continue;
        out[n++] = walk;
    }
    return n;
}

static int CountFaceList(face_t *f) {
    int c = 0;
    for (; f; f = f->next)
        if (!f->split[0]) c++;
    return c;
}

static void ClipFaceToBrushList(face_t *f, bspbrush_t **cuts, int ncuts, face_t **out) {
    *out = NULL;
    if (f->split[0]) return;
    face_t *cliplist = CopyFace(f);
    cliplist->next = NULL;
    int clipped = 0;
    for (int i = 0; i < ncuts; i++) {
        for (face_t *cf = cliplist; cf; cf = cf->next) {
            face_t *clip = NULL;
            if (cf->split[0]) continue;
            if (ClipFaceToBrush(cf, cuts[i], &clip)) {
                clipped = 1;
                cf->split[0] = cf;
            } else if (clip) {
                clipped = 1;
                cf->split[0] = cf;
                while (clip) {
                    face_t *next = clip->next;
                    clip->next = cliplist;
                    cliplist = clip;
                    clip = next;
                }
            }
        }
    }
    if (clipped) *out = cliplist;
    else FreeFaceList(cliplist);
}

static face_t *ComputeVisibleBrushSides(bspbrush_t *list) {
    face_t *total = NULL;
    int count = CountBrushList(list);
    bspbrush_t **cuts = xalloc(sizeof(bspbrush_t *) * (count + 1));
    for (bspbrush_t *b = list; b; b = b->next) {
        face_t *faces = NULL;
        mapbrush_t *mb = b->original;
        if (!(mb->contents & ALL_VISIBLE_CONTENTS)) continue;
        for (int i = 0; i < b->numsides; i++) {
            winding_t *w = b->sides[i].winding;
            if (!w) continue;
            if (!(b->sides[i].contents & ALL_VISIBLE_CONTENTS)) continue;
            side_t *side = FindOriginalSide(mb, b->sides + i);
            face_t *f = MakeBrushFace(side, w);
            f->next = faces;
            faces = f;
        }
        int ncuts = GetListOfCutBrushes(cuts, b, list);
        if (ncuts) {
            for (face_t *f = faces; f; f = f->next) {
                face_t *clip = NULL;
                ClipFaceToBrushList(f, cuts, ncuts, &clip);
                if (clip) {
                    if (CountFaceList(clip) <= 1) {
                        f->split[0] = f;
                        while (clip) {
                            face_t *next = clip->next;
                            clip->next = faces;
                            faces = clip;
                            clip = next;
                        }
                    } else FreeFaceList(clip);
                }
            }
        }
        while (faces) {
            face_t *next = faces->next;
            if (faces->split[0]) FreeFace(faces);
            else {
                faces->next = total;
                total = faces;
            }
            faces = next;
        }
    }
    free(cuts);
    return total;
}

face_t *MergeDetailTree(tree_t *worldtree, int start, int end) {
    face_t *leaffaces = NULL;
    bspbrush_t *detail = MakeBspBrushList(start, end, map_mins, map_maxs, ONLY_DETAIL);
    if (detail) {
        Msg("Chop Details...");
        detail = ChopBrushes(detail);
        Msg("done (0)\n");
        Msg("Find Visible Detail Sides...");
        face_t *faces = ComputeVisibleBrushSides(detail);
        TryMergeFaceList(&faces);
        SubdivideFaceList(&faces);
        Msg("done (0)\n");
        Msg("Merging details...");
        leaffaces = FilterFacesIntoTree(worldtree, faces);
        for (bspbrush_t *b = detail; b; b = b->next) MergeBrush_r(worldtree->headnode, CopyBrush(b));
        FreeFaceList(faces);
        FreeBrushList(detail);
        Msg("done (0)\n");
    }
    return leaffaces;
}
