"""Exercise the production radial sampler with valid and invalid coordinates.

Run with a Python that has ziglang: python tests/compile/radial_sample_bounds.py OUTPUT_DIR
Only WorldToLuxelSpace is stubbed, to supply pathological coordinates deterministically.
"""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
source = (root / 'native/hlvrad/direct.c').read_text()
start = source.index('static int SampleRadial(')
end = source.index('\n}\n', start) + 3
sampler = source[start:end]
harness = r'''
#include <math.h>
#include <assert.h>
typedef float vec3_t[3];
typedef struct { int w,h,l; float *weight; vec3_t *light[4]; } radial_t;
#define WEIGHT_EPS 0.00001f
#define VectorClear(a) ((a)[0]=(a)[1]=(a)[2]=0)
#define VectorScale(a,s,b) ((b)[0]=(a)[0]*(s),(b)[1]=(a)[1]*(s),(b)[2]=(a)[2]*(s))
static void WorldToLuxelSpace(int *unused,const vec3_t p,float *c) {c[0]=p[0];c[1]=p[1];}
''' + sampler + r'''
int main(void) {
    float weights[4]={2,2,2,2};
    vec3_t colours[4]={{2,4,6},{2,4,6},{2,4,6},{2,4,6}};
    radial_t r={2,2,0,weights,{colours,colours,colours,colours}};
    vec3_t result[4], good={1,1,0};
    assert(SampleRadial(&r,good,result,4)==1);
    for(int b=0;b<4;b++) for(int k=0;k<3;k++) assert(result[b][k]==k+1);
    float invalid[][2]={{2,0},{0,2},{-2,0},{0,-2},{NAN,0},{0,NAN},{INFINITY,0},{0,-INFINITY},{1e30f,0}};
    for(int i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++) {
        vec3_t p={invalid[i][0],invalid[i][1],0};
        for(int b=0;b<4;b++) for(int k=0;k<3;k++) result[b][k]=123;
        assert(SampleRadial(&r,p,result,4)==0);
        for(int b=0;b<4;b++) for(int k=0;k<3;k++) assert(result[b][k]==0);
    }
    weights[3]=0;
    assert(SampleRadial(&r,good,result,4)==0);
    for(int b=0;b<4;b++) for(int k=0;k<3;k++) assert(result[b][k]==0);
    return 0;
}
'''
c = out / 'radial_sample.c'
exe = out / 'radial_sample.exe'
c.write_text(harness)
subprocess.run([sys.executable, '-m', 'ziglang', 'cc', '-O0', str(c), '-o', str(exe)], check=True)
subprocess.run([str(exe)], check=True)
print('Radial sampler: valid light preserved; invalid, non-finite and empty samples are black.')
