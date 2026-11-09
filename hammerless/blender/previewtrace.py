"""Experimental opaque GPU visibility. Object-local BVHs survive transforms.

Median splits, stackless preorder traversal of mesh and instance BVHs. Object
transforms rebuild only the instance hierarchy. This uses shader traversal,
not hardware ray-query instructions.
"""
import numpy as np
import gpu


def build_bvh(positions, return_order=False):
    tris = np.asarray(positions, dtype=np.float32).reshape(-1,3,3)
    nodes, ordered = [], []
    if not len(tris):
        result = (np.zeros((0,3,4),np.float32), np.zeros((0,3,4),np.float32))
        return (*result,np.zeros(0,dtype=np.int64)) if return_order else result
    lows, highs = tris.min(axis=1), tris.max(axis=1)
    centers = (lows+highs)*0.5

    def split(ids):
        at = len(nodes)
        node = np.zeros((3,4),np.float32)
        nodes.append(node)
        node[0,:3], node[1,:3] = lows[ids].min(axis=0), highs[ids].max(axis=0)
        node[0,3] = -1
        if len(ids)<=8:
            node[0,3], node[1,3] = len(ordered), len(ids)
            ordered.extend(ids)
        else:
            axis = int(np.ptp(centers[ids],axis=0).argmax())
            ids = ids[np.argsort(centers[ids,axis],kind='stable')]
            half = len(ids)//2
            split(ids[:half])
            split(ids[half:])
        node[2,0] = len(nodes)  # next node after this subtree
        return at

    split(np.arange(len(tris)))
    packed = np.zeros((len(tris),3,4),np.float32)
    packed[:,:,:3] = tris[ordered]
    result = (np.array(nodes), packed)
    return (*result, np.array(ordered)) if return_order else result


def texture(data):
    data = np.asarray(data,np.float32).reshape(-1,4)
    width = min(1024,max(1,len(data)))
    height = max(1,(len(data)+width-1)//width)
    padded = np.zeros((height,width,4),np.float32)
    padded.reshape(-1,4)[:len(data)] = data
    return gpu.types.GPUTexture((width,height),format='RGBA32F',
        data=gpu.types.Buffer('FLOAT',padded.size,padded.ravel()))


class TraceCache:
    def __init__(self):
        self.meshes = {}
        self.signature = None
        self.instance_signature = None
        self.ranges = {}
        self.builds = 0
        self.instance_entries = {}
        self.instance_entry_builds = 0

    def update(self, scene):
        signature = tuple((key,id(g),g.shadow_revision) for key,g in scene.geometry.items())
        if signature != self.signature:
            self.meshes = {key:self.meshes[key] for key,g in scene.geometry.items()
                           if key in self.meshes and self.meshes[key][0] is g
                           and self.meshes[key][3] == g.shadow_revision}
            nodes, tris, node_count, tri_count = [], [], 0, 0
            self.ranges = {}
            for key,g in scene.geometry.items():
                if key not in self.meshes:
                    positions = g.positions if g.shadow_positions is None else g.shadow_positions
                    local_nodes,local_tris,order = build_bvh(positions,return_order=True)
                    self.meshes[key] = (g,local_nodes,local_tris,g.shadow_revision,order)
                    self.builds += 1
                _, local_nodes, local_tris, _, _ = self.meshes[key]
                if not len(local_nodes):
                    continue
                n = local_nodes.copy()
                leaves = n[:,0,3]>=0
                n[leaves,0,3] += tri_count
                n[:,2,0] += node_count
                self.ranges[key] = (node_count,node_count+len(n))
                nodes.append(n); tris.append(local_tris)
                node_count += len(n); tri_count += len(local_tris)
            self.nodes = texture(np.concatenate(nodes) if nodes else np.zeros((0,4)))
            self.triangles = texture(np.concatenate(tris) if tris else np.zeros((0,4)))
            self.signature = signature
            self.instance_signature = None
            # Mesh/range changes invalidate packed offsets and local bounds.
            # Transform-only updates keep entries for unchanged instances.
            self.instance_entries = {}
        inst_signature = tuple((key,tuple(v for row in matrix for v in row)) for key,matrix in scene.instances)
        if inst_signature != self.instance_signature:
            instances = []
            retained = {}
            for (key,matrix),token in zip(scene.instances,inst_signature):
                if token in retained:
                    entry = retained[token]
                elif token in self.instance_entries:
                    entry = self.instance_entries[token]
                elif key not in self.ranges or abs(matrix.determinant())<1e-15:
                    entry = None
                else:
                    root = self.meshes[key][1][0]
                    low,high = root[0,:3],root[1,:3]
                    corners = np.array([[x,y,z,1] for x in (low[0],high[0])
                                        for y in (low[1],high[1]) for z in (low[2],high[2])])
                    world = corners @ np.array(matrix).T
                    entry = np.zeros((6,4),np.float32)
                    entry[:3] = np.array(matrix.inverted())[:3]
                    entry[3,:2] = self.ranges[key]
                    entry[4,:3], entry[5,:3] = world[:,:3].min(axis=0),world[:,:3].max(axis=0)
                    self.instance_entry_builds += 1
                retained[token] = entry
                if entry is not None:instances.append(entry)
            # Retain only current transforms, not a history of a dragged object.
            # Equal mesh/transform instances share data but still appear once
            # per instance in the hierarchy, preserving traversal ordering.
            self.instance_entries = retained
            self.instance_count = len(instances)
            if instances:
                entries = np.array(instances)
                boxes = np.stack((entries[:,4,:3],entries[:,5,:3],entries[:,4,:3]),axis=1)
                top, _, order = build_bvh(boxes,return_order=True)
                self.top_count = len(top)
                self.top = texture(top)
                self.instances = texture(entries[order])
            else:
                self.top_count = 0
                self.top = texture(np.zeros((0,4)))
                self.instances = texture(np.zeros((0,4)))
            self.instance_signature = inst_signature

    def update_bounce_materials(self, scene, resolver):
        """Upload per-hit reflectivity in BVH triangle order, without rebuilding it.

        Called on a material snapshot change, not every frame. Models block
        rays but do not return bounced radiance, matching VRAD's gi.comp.
        """
        self.update(scene)
        rows = []
        for key,g in scene.geometry.items():
            order = self.meshes[key][4]
            if not len(order):continue
            ids = g.shadow_triangle_ids
            original = order if ids is None else ids[order]
            material_ids = g.material_ids if g.material_ids is not None else np.zeros(len(g.positions)//3,dtype=int)
            data = np.zeros((len(order),4),np.float32)
            if not g.use_uv:
                for mi in np.unique(material_ids[original]):
                    mat = g.materials[mi] if 0<=mi<len(g.materials) else None
                    surface = resolver.resolve(mat)
                    mask = material_ids[original]==mi
                    data[mask,:3] = surface.reflectivity
                    data[mask,3] = float(surface.receives_light and surface.verified)
            rows.append(data)
        self.surfaces = texture(np.concatenate(rows) if rows else np.zeros((0,4)))

    def bind(self, shader):
        shader.uniform_sampler('traceNodes',self.nodes)
        shader.uniform_sampler('traceTriangles',self.triangles)
        shader.uniform_sampler('traceInstances',self.instances)
        shader.uniform_int('traceInstanceCount',self.instance_count)
        shader.uniform_sampler('traceTop',self.top)
        shader.uniform_int('traceTopCount',self.top_count)


GLSL = r'''
vec4 traceFetch(sampler2D t,int i) {int w=textureSize(t,0).x;return texelFetch(t,ivec2(i%w,i/w),0);}
bool traceBox(vec3 o,vec3 d,vec3 lo,vec3 hi,float end) {
    float nearT=0.0, farT=end;
    for(int a=0;a<3;a++) {
        if(abs(d[a])<1e-20) {if(o[a]<lo[a] || o[a]>hi[a]) return false;}
        else {float t0=(lo[a]-o[a])/d[a],t1=(hi[a]-o[a])/d[a];
            nearT=max(nearT,min(t0,t1));farT=min(farT,max(t0,t1));}
    }
    return nearT<=farT;
}
bool traceTriangleHit(vec3 o,vec3 d,int tri,float end,out vec3 hit) {
    vec3 a=traceFetch(traceTriangles,tri*3).xyz;
    vec3 e1=traceFetch(traceTriangles,tri*3+1).xyz-a;
    vec3 e2=traceFetch(traceTriangles,tri*3+2).xyz-a;
    vec3 p=cross(d,e2);float det=dot(e1,p);
    if(abs(det)<1e-20) return false;
    float inv=1.0/det;vec3 s=o-a;float u=dot(s,p)*inv;
    if(u<0.0 || u>1.0) return false;
    vec3 q=cross(s,e1);float v=dot(d,q)*inv;
    if(v<0.0 || u+v>1.0) return false;
    float t=dot(e2,q)*inv;hit=vec3(t,u,v);return t>0.0 && t<end;
}
bool traceTriangle(vec3 o,vec3 d,int tri,float end) {
    vec3 hit;return traceTriangleHit(o,d,tri,end,hit);
}
// Distance remains in world units: inverse-transformed directions must NOT be
// normalized. Return the geometric front normal, not a face-forward normal;
// VRAD rejects back-facing bounce hits instead of reflecting light from them.
bool traceClosest(vec3 o,vec3 direction,float maxDistance,
                  out float distance,out vec3 normal,out vec2 bary,
                  out int triangle,out int instance) {
    distance=maxDistance;normal=vec3(0);bary=vec2(0);triangle=-1;instance=-1;
    if(traceInstanceCount==0 || maxDistance<=0.0 || dot(direction,direction)==0.0)return false;
    vec3 d=normalize(direction);
    int topNode=0;
    while(topNode<traceTopCount) {
        vec4 topLo=traceFetch(traceTop,topNode*3),topHi=traceFetch(traceTop,topNode*3+1);
        int topEscape=int(traceFetch(traceTop,topNode*3+2).x);
        if(!traceBox(o,d,topLo.xyz,topHi.xyz,distance)) {topNode=topEscape;continue;}
        if(topLo.w<0.0) {topNode++;continue;}
        for(int inst=int(topLo.w);inst<int(topLo.w+topHi.w);inst++) {
            int base=inst*6;
            if(!traceBox(o,d,traceFetch(traceInstances,base+4).xyz,traceFetch(traceInstances,base+5).xyz,distance))continue;
            vec4 r0=traceFetch(traceInstances,base),r1=traceFetch(traceInstances,base+1),r2=traceFetch(traceInstances,base+2);
            vec3 localO=vec3(dot(r0,vec4(o,1)),dot(r1,vec4(o,1)),dot(r2,vec4(o,1)));
            vec3 localD=vec3(dot(r0.xyz,d),dot(r1.xyz,d),dot(r2.xyz,d));
            ivec2 range=ivec2(traceFetch(traceInstances,base+3).xy);
            int node=range.x;
            while(node<range.y) {
                vec4 lo=traceFetch(traceNodes,node*3),hi=traceFetch(traceNodes,node*3+1);
                int escape=int(traceFetch(traceNodes,node*3+2).x);
                if(!traceBox(localO,localD,lo.xyz,hi.xyz,distance)) {node=escape;continue;}
                if(lo.w<0.0) {node++;continue;}
                for(int k=0;k<int(hi.w);k++) {
                    vec3 hit;int tri=int(lo.w)+k;
                    if(traceTriangleHit(localO,localD,tri,distance,hit)) {
                        distance=hit.x;bary=hit.yz;triangle=tri;instance=inst;
                    }
                }
                node=escape;
            }
        }
        topNode=topEscape;
    }
    if(triangle<0)return false;
    vec3 a=traceFetch(traceTriangles,triangle*3).xyz;
    vec3 e1=traceFetch(traceTriangles,triangle*3+1).xyz-a;
    vec3 e2=traceFetch(traceTriangles,triangle*3+2).xyz-a;
    vec3 n=cross(e1,e2);
    vec3 r0=traceFetch(traceInstances,instance*6).xyz;
    vec3 r1=traceFetch(traceInstances,instance*6+1).xyz;
    vec3 r2=traceFetch(traceInstances,instance*6+2).xyz;
    normal=normalize(r0*n.x+r1*n.y+r2*n.z);
    return true;
}
float traceVisibility(vec3 o,vec3 target) {
    vec3 delta=target-o;float end=length(delta);
    if(end<=0.0)return 1.0;
    vec3 d=delta/end;
    if(traceInstanceCount==0)return 1.0;
    int topNode=0;
    while(topNode<traceTopCount) {
        vec4 topLo=traceFetch(traceTop,topNode*3),topHi=traceFetch(traceTop,topNode*3+1);
        int topEscape=int(traceFetch(traceTop,topNode*3+2).x);
        if(!traceBox(o,d,topLo.xyz,topHi.xyz,end)) {topNode=topEscape;continue;}
        if(topLo.w<0.0) {topNode++;continue;}
        for(int inst=int(topLo.w);inst<int(topLo.w+topHi.w);inst++) {
        int base=inst*6;
        if(!traceBox(o,d,traceFetch(traceInstances,base+4).xyz,traceFetch(traceInstances,base+5).xyz,end))continue;
        vec4 r0=traceFetch(traceInstances,base),r1=traceFetch(traceInstances,base+1),r2=traceFetch(traceInstances,base+2);
        vec3 localO=vec3(dot(r0,vec4(o,1)),dot(r1,vec4(o,1)),dot(r2,vec4(o,1)));
        vec3 localD=vec3(dot(r0.xyz,d),dot(r1.xyz,d),dot(r2.xyz,d));
        ivec2 range=ivec2(traceFetch(traceInstances,base+3).xy);
        int node=range.x;
        while(node<range.y) {
            vec4 lo=traceFetch(traceNodes,node*3),hi=traceFetch(traceNodes,node*3+1);
            int escape=int(traceFetch(traceNodes,node*3+2).x);
            if(!traceBox(localO,localD,lo.xyz,hi.xyz,end)) {node=escape;continue;}
            if(lo.w<0.0) {node++;continue;}
            for(int k=0;k<int(hi.w);k++)if(traceTriangle(localO,localD,int(lo.w)+k,end))return 0.0;
            node=escape;
        }
        }
        topNode=topEscape;
    }
    return 1.0;
}
'''
