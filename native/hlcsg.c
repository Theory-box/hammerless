/* Brush CSG for the Blender brush tools (Carve, Intersect, Clip): convex brushes as plane sets in double, polygons
 * rebuilt from the planes (never carried as truth), each plane tagged with the side it came from (a material, its
 * texture alignment) so the tools can give each new face its own. Standalone: no state between calls (the add-on
 * keeps this DLL loaded). Included from hlnav.c.
 *
 * A plane (nx, ny, nz, d): inside where n.p <= d. Output planes are snapped as the map compiler snaps them
 * (core/collision.py snap_plane, vbsp's FindFloatPlane): a near-axial normal becomes exact, a distance within 0.01
 * of a whole number becomes it, so axis-aligned cuts stay on the grid and oblique ones stay exact. */

#define CSG_MAX_SIDES 96
#define CSG_MAX_WIND 128
#define CSG_ON_EPS 0.01           /* a point this close to a plane is on it */
#define CSG_MIN_AREA 0.01          /* a face smaller than this is no face (a plane that only touches the brush) */
#define CSG_MIN_VOLUME 0.01        /* a piece smaller than this is no piece */
#define CSG_BIG 1e6

typedef struct { double x, y, z; } CV;
typedef struct { CV n; double d; int src; } CPlane;
typedef struct { int n; CPlane p[CSG_MAX_SIDES]; } CBrush;

static double c_dot(CV a, CV b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static CV c_cross(CV a, CV b) { CV r = {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; return r; }
static CV c_add(CV a, CV b) { CV r = {a.x + b.x, a.y + b.y, a.z + b.z}; return r; }
static CV c_sub(CV a, CV b) { CV r = {a.x - b.x, a.y - b.y, a.z - b.z}; return r; }
static CV c_scale(CV a, double s) { CV r = {a.x * s, a.y * s, a.z * s}; return r; }

static CPlane c_snap(CPlane p) {
    double c[3] = {p.n.x, p.n.y, p.n.z};
    for (int i = 0; i < 3; i++)
        if (fabs(c[i]) > 1.0 - 1e-5) {          /* (vbsp's NORMAL_EPSILON) */
            double sign = c[i] > 0 ? 1.0 : -1.0;
            c[0] = c[1] = c[2] = 0.0;
            c[i] = sign;
            break;
        }
    p.n.x = c[0], p.n.y = c[1], p.n.z = c[2];
    double r = nearbyint(p.d);
    if (fabs(p.d - r) < 0.01) p.d = r + 0.0;   /* (DIST_EPSILON-ish: integer distances kept integer) */
    return p;
}

/* the polygon of plane i of the brush: a big square on it, cut by every other plane. Returns its vertex count. */
static int c_winding(const CBrush *b, int i, CV *out) {
    CPlane pl = b->p[i];
    CV n = pl.n;
    CV up = fabs(n.z) < 0.9 ? (CV){0, 0, 1} : (CV){1, 0, 0};
    CV u = c_cross(up, n);
    double len = sqrt(c_dot(u, u));
    u = c_scale(u, 1.0 / len);
    CV v = c_cross(n, u);
    CV o = c_scale(n, pl.d);
    CV w[CSG_MAX_WIND], t[CSG_MAX_WIND];
    int nw = 4;
    w[0] = c_add(o, c_add(c_scale(u, -CSG_BIG), c_scale(v, -CSG_BIG)));
    w[1] = c_add(o, c_add(c_scale(u, CSG_BIG), c_scale(v, -CSG_BIG)));
    w[2] = c_add(o, c_add(c_scale(u, CSG_BIG), c_scale(v, CSG_BIG)));
    w[3] = c_add(o, c_add(c_scale(u, -CSG_BIG), c_scale(v, CSG_BIG)));
    for (int j = 0; j < b->n && nw > 0; j++) {
        if (j == i) continue;
        CPlane q = b->p[j];
        /* (the same plane facing away: nothing of this one is inside) */
        if (fabs(c_dot(q.n, n) + 1.0) < 1e-9 && fabs(q.d + pl.d) < 1e-9) return 0;
        if (fabs(c_dot(q.n, n) - 1.0) < 1e-9 && fabs(q.d - pl.d) < 1e-9 && j < i) return 0;   /* (a duplicate) */
        int nt = 0;
        for (int k = 0; k < nw; k++) {
            CV a = w[k], c = w[(k + 1) % nw];
            double da = c_dot(q.n, a) - q.d, dc = c_dot(q.n, c) - q.d;
            if (da <= CSG_ON_EPS) t[nt++] = a;
            if ((da < -CSG_ON_EPS && dc > CSG_ON_EPS) || (da > CSG_ON_EPS && dc < -CSG_ON_EPS)) {
                double f = da / (da - dc);
                t[nt++] = c_add(a, c_scale(c_sub(c, a), f));
            }
            if (nt >= CSG_MAX_WIND - 2) return -1;
        }
        nw = nt;
        for (int k = 0; k < nw; k++) w[k] = t[k];
    }
    /* drop repeated points (a cut exactly through a corner) */
    int m = 0;
    for (int k = 0; k < nw; k++) {
        CV d = c_sub(w[k], m ? out[m - 1] : w[(k + nw - 1) % nw]);
        if (m && c_dot(d, d) < 1e-12) continue;
        out[m++] = w[k];
    }
    if (m > 1) {
        CV d = c_sub(out[0], out[m - 1]);
        if (c_dot(d, d) < 1e-12) m--;
    }
    return m >= 3 ? m : 0;
}

static double c_area(const CV *w, int n, CV normal) {
    CV s = {0, 0, 0};
    for (int k = 1; k + 1 < n; k++) s = c_add(s, c_cross(c_sub(w[k], w[0]), c_sub(w[k + 1], w[0])));
    return 0.5 * fabs(c_dot(s, normal));
}

/* The brush with the planes that bound it only (ones that just touch it dropped); its volume, or 0 when nothing
 * is left (an empty or degenerate brush). */
static double c_tidy(CBrush *b) {
    CBrush out;
    out.n = 0;
    double vol = 0.0;
    CV w[CSG_MAX_WIND];
    for (int i = 0; i < b->n; i++) {
        int nw = c_winding(b, i, w);
        if (nw <= 0) continue;
        double a = c_area(w, nw, b->p[i].n);
        if (a < CSG_MIN_AREA) continue;
        vol += b->p[i].d * a / 3.0;
        out.p[out.n++] = b->p[i];
    }
    if (out.n < 4) return 0.0;
    /* (the volume from the kept faces, about the origin: d * area / 3 summed, the divergence theorem) */
    *b = out;
    return vol;
}

/* (the same plane, within the tolerances: a face drawn on a surface lies on it, give or take float noise) */
static int c_same(CPlane a, CPlane b) {
    return c_dot(a.n, b.n) > 1.0 - 1e-9 && fabs(a.d - b.d) < CSG_ON_EPS;
}

static int c_add_plane(CBrush *b, CPlane p) {
    p = c_snap(p);
    for (int i = 0; i < b->n; i++)
        if (c_same(b->p[i], p)) return 1;          /* (already bounded there: the first one's tag stays) */
    if (b->n >= CSG_MAX_SIDES) return 0;
    b->p[b->n++] = p;
    return 1;
}

static CPlane c_flip(CPlane p) {
    CPlane r = {c_scale(p.n, -1.0), -p.d, p.src};
    return r;
}

/* Output: each piece's planes and tags, then its faces (vertices) for the mesh. */
typedef struct {
    double *planes; int *src; int *sides; double *verts; int *face_verts; int cap_sides, cap_verts, cap_pieces;
    int pieces, nsides, nverts, overflow;
} COut;

static void c_emit(COut *o, const CBrush *b) {
    if (o->pieces >= o->cap_pieces || o->nsides + b->n > o->cap_sides) { o->overflow = 1; return; }
    CV w[CSG_MAX_WIND];
    int first_side = o->nsides, first_vert = o->nverts;
    for (int i = 0; i < b->n; i++) {
        int nw = c_winding(b, i, w);
        if (nw < 0 || o->nverts + nw > o->cap_verts) { o->overflow = 1; o->nsides = first_side; o->nverts = first_vert; return; }
        double *pp = o->planes + 4 * o->nsides;
        pp[0] = b->p[i].n.x, pp[1] = b->p[i].n.y, pp[2] = b->p[i].n.z, pp[3] = b->p[i].d;
        o->src[o->nsides] = b->p[i].src;
        o->face_verts[o->nsides] = nw;
        for (int k = 0; k < nw; k++) {
            double *vv = o->verts + 3 * o->nverts++;
            vv[0] = w[k].x, vv[1] = w[k].y, vv[2] = w[k].z;
        }
        o->nsides++;
    }
    o->sides[o->pieces++] = b->n;
}

static void c_load(CBrush *b, int n, const double *planes, const int *src) {
    b->n = 0;
    for (int i = 0; i < n && i < CSG_MAX_SIDES; i++) {
        CPlane p = {{planes[4 * i], planes[4 * i + 1], planes[4 * i + 2]}, planes[4 * i + 3], src ? src[i] : i};
        double l = sqrt(c_dot(p.n, p.n));
        if (l < 1e-12) continue;
        p.n = c_scale(p.n, 1.0 / l);
        p.d /= l;
        c_add_plane(b, p);                         /* (coplanar faces of one brush: one plane) */
    }
}

/* The cutter's planes in the order to peel pieces off the target: the ones whose face inside the target is
 * biggest first (big clean slabs before slivers), planes that don't cut the target skipped. */
static int c_order(const CBrush *target, const CBrush *cutter, int *order) {
    double area[CSG_MAX_SIDES];
    int n = 0;
    CV w[CSG_MAX_WIND];
    for (int i = 0; i < cutter->n; i++) {
        CBrush t = *target;
        if (!c_add_plane(&t, cutter->p[i])) continue;
        int nw = c_winding(&t, t.n - 1, w);         /* (the cutter's face clipped to the target) */
        area[i] = nw > 0 ? c_area(w, nw, cutter->p[i].n) : 0.0;
        order[n++] = i;
    }
    for (int a = 1; a < n; a++)                      /* (insertion sort: a handful of planes) */
        for (int b = a; b > 0 && area[order[b]] > area[order[b - 1]]; b--) {
            int x = order[b]; order[b] = order[b - 1]; order[b - 1] = x;
        }
    return n;
}

/* hl_csg: op 0 = subtract b from a (the pieces of a outside b; a itself when they don't overlap), 1 = intersect
 * (the overlap, or nothing), 2 = clip a by b's first plane (the piece in front, then the piece behind; an empty
 * side gives a piece of 0 sides). Planes are (nx ny nz d) with tags (src); out_sides[i] = piece i's plane count,
 * out_face_verts[j] = side j's vertex count. Returns the piece count, -1 when a buffer is too small. */
EXPORT int hl_csg(int op, int na, const double *ap, const int *asrc, int nb, const double *bp, const int *bsrc,
                  double *out_planes, int *out_src, int *out_sides, double *out_verts, int *out_face_verts,
                  int cap_sides, int cap_verts, int cap_pieces) {
    static CBrush a, b, in, piece;               /* (big: kept off the stack; no state between calls) */
    COut o = {out_planes, out_src, out_sides, out_verts, out_face_verts, cap_sides, cap_verts, cap_pieces,
              0, 0, 0, 0};
    c_load(&a, na, ap, asrc);
    c_load(&b, nb, bp, bsrc);
    if (c_tidy(&a) <= CSG_MIN_VOLUME) return 0;
    if (op == 2) {
        if (b.n < 1) return 0;
        CPlane cut = b.p[0];
        piece = a;
        int ok_f = c_add_plane(&piece, c_flip(cut));
        if (ok_f && c_tidy(&piece) > CSG_MIN_VOLUME) c_emit(&o, &piece);
        else { if (o.pieces < cap_pieces) o.sides[o.pieces++] = 0; }
        piece = a;
        int ok_b = c_add_plane(&piece, cut);
        if (ok_b && c_tidy(&piece) > CSG_MIN_VOLUME) c_emit(&o, &piece);
        else { if (o.pieces < cap_pieces) o.sides[o.pieces++] = 0; }
        return o.overflow ? -1 : o.pieces;
    }
    if (c_tidy(&b) <= CSG_MIN_VOLUME) {
        if (op == 0) c_emit(&o, &a);
        return o.overflow ? -1 : o.pieces;
    }
    /* do they overlap at all? (the target cut by every cutter plane) */
    in = a;
    for (int i = 0; i < b.n; i++) c_add_plane(&in, b.p[i]);
    if (c_tidy(&in) <= CSG_MIN_VOLUME) {
        if (op == 0) c_emit(&o, &a);              /* (no overlap: the target as it was) */
        return o.overflow ? -1 : o.pieces;
    }
    if (op == 1) {
        c_emit(&o, &in);
        return o.overflow ? -1 : o.pieces;
    }
    /* subtract: peel off, for each cutter plane in turn, the part of what's left that's in front of it */
    int order[CSG_MAX_SIDES];
    int no = c_order(&a, &b, order);
    in = a;
    for (int k = 0; k < no; k++) {
        CPlane cut = b.p[order[k]];
        piece = in;
        if (c_add_plane(&piece, c_flip(cut)) && c_tidy(&piece) > CSG_MIN_VOLUME) c_emit(&o, &piece);
        if (!c_add_plane(&in, cut) || c_tidy(&in) <= CSG_MIN_VOLUME) break;
    }
    return o.overflow ? -1 : o.pieces;
}
