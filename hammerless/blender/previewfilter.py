"""Experimental regular planar radial filter; not wired into live rendering.

Caller supplies lightmap coordinates and a filterLighting(vec2) evaluator in
that plane. This excludes clipped cells, neighboring faces and displacements.
A complete nine-phase average equals the full filter for a stationary field.
Do not lock sky sampling to this phase: correlated subsets bias the result.
"""
GLSL = r'''
vec3 regularPlanarFilter(vec2 coord, int phase) {
    vec2 base=floor(coord), f=fract(coord);
    vec3 wx=vec3((1.0-f.x)*0.5,0.5,f.x*0.5);
    vec3 wy=vec3((1.0-f.y)*0.5,0.5,f.y*0.5);
    vec3 result=vec3(0.0);
    for(int j=0;j<9;j++) {
        if(phase>=0 && j!=phase%9) continue;
        int x=j%3, y=j/3;
        float weight=wx[x]*wy[y];
        if(weight>0.0)
            result+=filterLighting(base+vec2(x,y)-0.5)*weight*(phase>=0?9.0:1.0);
    }
    return result;
}
'''
