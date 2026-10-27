"""Render installed-game preview samples in a separate factory-startup Blender.

blender --factory-startup --python tools/render_particle_samples.py --
    --game GAME_ROOT --output OUTPUT_DIRECTORY --effects gas_smoke small_sparks

Never loads/saves a blend file. Outputs local diagnostic PNGs and JSON, not
redistributable addon assets. Requires an interactive GPU context (no -b).
"""
import argparse
import json
import re
import sys
import traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bpy
import gpu
import numpy as np
from mathutils import Matrix
from gpu_extras.batch import batch_for_shader


def run():
    parser=argparse.ArgumentParser()
    parser.add_argument('--game',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--effects',nargs='+',default=['gas_smoke','small_sparks'])
    parser.add_argument('--times',nargs='+',type=float,default=[.25,1,2])
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    offscreen=None
    try:
        from hammerless.core.vpk import GameContent
        from hammerless.core.particlepreview import load_library,audit
        from hammerless.core.particleeffect import EffectSimulation
        from hammerless.core.particlesheet import load_atlas
        from hammerless.core.particlegeometry import build_geometry,RENDERERS
        from hammerless.blender.particlepreview import shader
        content=GameContent(args.game);library,errors=load_library(content)
        coverage=audit(library);materials={};report={'parse_errors':errors,'samples':[]}
        offscreen=gpu.types.GPUOffScreen(512,512)
        sh=shader()
        for effect in args.effects:
            for seconds in args.times:
                if not np.isfinite(seconds) or not 0<=seconds<=60:raise ValueError('sample time must be 0..60 seconds')
                systems=[];warnings=set()
                def assets(definition):
                    material=definition.attrs.get('material','')
                    if material not in materials:
                        atlas=load_atlas(content,material) if material else None
                        if atlas is None:materials[material]=(None,{},False)
                        else:
                            rgba,sequences,additive=atlas;h,w=rgba.shape[:2]
                            pixels=np.ascontiguousarray(rgba[::-1],dtype=np.float32)
                            texture=gpu.types.GPUTexture((w,h),format='RGBA32F',data=gpu.types.Buffer('FLOAT',pixels.size,pixels.ravel()))
                            materials[material]=(texture,sequences,additive)
                    texture,sequences,additive=materials[material]
                    return sequences
                effect_tree=EffectSimulation(library,effect,asset_loader=assets)
                effect_tree.advance(seconds)
                warnings.update(effect_tree.warnings)
                for sim in effect_tree.systems:
                    warnings.update(sim.warnings)
                    texture,sequences,additive=materials[sim.definition.attrs.get('material','')]
                    systems.append((sim,texture,sequences,additive))
                batches=[]
                # View along -Y: Source Z is vertical in the sample image.
                for sim,texture,sequences,additive in systems:
                    if texture is None:
                        warnings.add('Missing material: '+sim.definition.attrs.get('material',''));continue
                    for renderer in sim.definition.rules['renderers']:
                        if renderer.get('functionName','').casefold() not in RENDERERS:continue
                        geometry,gaps=build_geometry(sim,renderer,sequences,np.eye(4),[1,0,0],[0,0,1],[0,-10000,0])
                        warnings.update(gaps)
                        if len(geometry['pos']):batches.append((geometry,texture,additive))
                if batches:
                    vertices=np.concatenate([g['pos'] for g,_,_ in batches])
                    lo=vertices.min(axis=0);hi=vertices.max(axis=0);center=(lo+hi)/2
                    extent=max(1,hi[0]-lo[0],hi[2]-lo[2])*1.15
                else:center=np.zeros(3);extent=1
                mvp=Matrix(((2/extent,0,0,-2*center[0]/extent),
                            (0,0,2/extent,-2*center[2]/extent),
                            (0,0,0,0),(0,0,0,1)))
                with offscreen.bind():
                    fb=gpu.state.active_framebuffer_get();fb.clear(color=(.015,.015,.015,1))
                    gpu.state.viewport_set(0,0,512,512);gpu.state.depth_test_set('NONE')
                    for geometry,texture,additive in batches:
                        gpu.state.blend_set('ADDITIVE' if additive else 'ALPHA')
                        sh.bind();sh.uniform_float('mvp',mvp);sh.uniform_sampler('sprite',texture)
                        batch_for_shader(sh,'TRIS',geometry).draw(sh)
                    pixels=np.array(fb.read_color(0,0,512,512,4,0,'FLOAT').to_list(),dtype=np.float32).reshape(-1)
                    if not np.isfinite(pixels).all():raise ValueError('nonfinite GPU output')
                    changed_pixels=int(np.count_nonzero(np.max(np.abs(pixels.reshape(-1,4)[:,:3]-.015),axis=1)>.001))
                    if batches and changed_pixels==0:warnings.add('Geometry produced no visible pixels at this sample time')
                    filename=re.sub(r'[^a-zA-Z0-9_-]','_',effect)+f'_{seconds:g}s.png'
                    image=bpy.data.images.new('Particle diagnostic',512,512,alpha=True)
                    image.pixels.foreach_set(pixels);image.filepath_raw=str(output/filename);image.file_format='PNG';image.save()
                    bpy.data.images.remove(image)
                report['samples'].append({'effect':effect,'seconds':seconds,'file':filename,
                    'particles':sum(len(s.age) for s,_,_,_ in systems),'batches':len(batches),
                    'changed_pixels':changed_pixels,
                    'runtime_warnings':sorted(warnings),'coverage_gaps':coverage[effect.casefold()]['recursive_gaps']})
        (output/'report.json').write_text(json.dumps(report,indent=2))
        print('PASS rendered',len(report['samples']),'particle samples to',output,flush=True)
    except Exception:
        traceback.print_exc()
        (output/'error.txt').write_text(traceback.format_exc())
        import os
        os._exit(1)
    finally:
        if offscreen is not None:offscreen.free()
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(run,first_interval=1)
