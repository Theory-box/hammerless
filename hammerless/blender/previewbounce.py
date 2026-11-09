"""Diffuse path integrator; bounded depth, no denoising or realtime claim.

Cosine-weighted hemisphere mean matches VRAD gi.comp's transport convention.
Direct light is evaluated at hits instead of fetching quantized baked luxels;
this difference requires a bake comparison, not an assertion of exact parity.
"""
GLSL = r'''
uint bounceHash(uint h) {
    h^=h>>16;h*=0x7feb352du;h^=h>>15;h*=0x846ca68bu;h^=h>>16;return h;
}
vec3 previewBounce(vec3 p,vec3 n) {
    if(bounceSamples<=0)return vec3(0);
    vec3 sum=vec3(0);
    for(int j=0;j<bounceSamples;j++) {
        vec3 pathNormal=n,origin=p+n*(0.25/unitScale),throughput=vec3(1);
        for(int depth=0;depth<bounceDepth;depth++) {
        vec3 x=normalize(abs(pathNormal.z)<0.9?cross(pathNormal,vec3(0,0,1)):cross(pathNormal,vec3(1,0,0)));
        vec3 y=cross(pathNormal,x);
        float u=(float(j)+0.5)/float(bounceSamples);
        float v=float(bitfieldReverse(uint(j)))/4294967296.0;
        if(bounceSeed>0) {
            uint h=bounceHash(uint(bounceSeed)*6271u);
            u=fract(u+float(h&0xFFFFu)/65536.0);
            v=fract(v+float(h>>16)/65536.0);
        }
        if(depth>0) {
            uint h=bounceHash(uint(j)*9781u+uint(bounceSeed)*6271u+uint(depth)*13007u);
            u=float(h&0xFFFFu)/65536.0;v=float(h>>16)/65536.0;
        }
        float phi=6.28318530718*u,ct=sqrt(1.0-v),st=sqrt(v);
        vec3 d=x*(cos(phi)*st)+y*(sin(phi)*st)+pathNormal*ct;
        float distance;vec3 hn;vec2 bary;int tri,inst;
        if(!traceClosest(origin,d,56755.84/unitScale,distance,hn,bary,tri,inst))break;
        if(dot(hn,d)>=0.0)break;
        vec4 surface=traceFetch(traceSurfaces,tri);
        if(surface.w<0.5)break;
        vec3 hit=origin+d*distance;
        // Uniform selection from the SAME 162 VRAD directions; previewDirect
        // keeps their full cosine normalization and multiplies this term by
        // 162. Expected sky illumination is unchanged, at greater variance.
        int skyIndex=bounceSkySampled!=0?int(bounceHash(uint(j)+uint(bounceSeed)*9781u+uint(depth)*13007u+719u)%162u):-1;
        throughput*=surface.rgb;
        sum+=previewDirect(hit+hn*(1.0/unitScale),hn,skyIndex,1)*throughput;
        pathNormal=hn;origin=hit+hn*(1.25/unitScale);
        }
    }
    return sum/float(bounceSamples);
}
'''
