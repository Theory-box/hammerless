"""Experimental opaque GPU visibility. Object-local BVHs survive transforms.

Median splits, stackless preorder traversal. Instance bounds are currently scanned
linearly; this is a measurable prototype, not a hardware ray-query replacement.
"""
import numpy as np
import gpu


def build_bvh(positions):
    tris = np.asarray(positions, dtype=np.float32).reshape(-1,3,3)
    nodes, ordered = [], []
    if not len(tris):
        return np.zeros((0,3,4),np.float32), np.zeros((0,3,4),np.float32)
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
    return np.array(nodes), packed


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

    def update(self, scene):
        signature = tuple((key,id(g)) for key,g in scene.geometry.items())
        if signature != self.signature:
            self.meshes = {key:self.meshes[key] for key,g in scene.geometry.items()
                           if key in self.meshes and self.meshes[key][0] is g}
            nodes, tris, node_count, tri_count = [], [], 0, 0
            self.ranges = {}
            for key,g in scene.geometry.items():
                if key not in self.meshes:
                    self.meshes[key] = (g,*build_bvh(g.positions))
                    self.builds += 1
                _, local_nodes, local_tris = self.meshes[key]
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
        inst_signature = tuple((key,tuple(v for row in matrix for v in row)) for key,matrix in scene.instances)
        if inst_signature != self.instance_signature:
            instances = []
            for key,matrix in scene.instances:
                if key not in self.ranges or abs(matrix.determinant())<1e-15:
                    continue
                root = self.meshes[key][1][0]
                low,high = root[0,:3],root[1,:3]
                corners = np.array([[x,y,z,1] for x in (low[0],high[0])
                                    for y in (low[1],high[1]) for z in (low[2],high[2])])
                world = corners @ np.array(matrix).T
                entry = np.zeros((6,4),np.float32)
                entry[:3] = np.array(matrix.inverted())[:3]
                entry[3,:2] = self.ranges[key]
                entry[4,:3], entry[5,:3] = world[:,:3].min(axis=0),world[:,:3].max(axis=0)
                instances.append(entry)
            self.instance_count = len(instances)
            self.instances = texture(np.array(instances) if instances else np.zeros((0,4)))
            self.instance_signature = inst_signature

    def bind(self, shader):
        shader.uniform_sampler('traceNodes',self.nodes)
        shader.uniform_sampler('traceTriangles',self.triangles)
        shader.uniform_sampler('traceInstances',self.instances)
        shader.uniform_int('traceInstanceCount',self.instance_count)


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
bool traceTriangle(vec3 o,vec3 d,int tri,float end) {
    vec3 a=traceFetch(traceTriangles,tri*3).xyz;
    vec3 e1=traceFetch(traceTriangles,tri*3+1).xyz-a;
    vec3 e2=traceFetch(traceTriangles,tri*3+2).xyz-a;
    vec3 p=cross(d,e2);float det=dot(e1,p);
    if(abs(det)<1e-20) return false;
    float inv=1.0/det;vec3 s=o-a;float u=dot(s,p)*inv;
    if(u<0.0 || u>1.0) return false;
    vec3 q=cross(s,e1);float v=dot(d,q)*inv;
    if(v<0.0 || u+v>1.0) return false;
    float t=dot(e2,q)*inv;return t>0.0 && t<end;
}
float traceVisibility(vec3 o,vec3 target) {
    vec3 delta=target-o;float end=length(delta);
    if(end<=0.0)return 1.0;
    vec3 d=delta/end;
    for(int inst=0;inst<traceInstanceCount;inst++) {
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
    return 1.0;
}
'''
