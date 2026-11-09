"""Read-only saved-map chart diagnostics; captures exact rejected export axes."""
from pathlib import Path
import sys,json,traceback
import bpy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,previewsurfacecharts as charts
OUT=ROOT.parents[1]/'outputs/l4d2-preview/chart-rejections.json'
def run():
    props.register()
    bpy.ops.wm.open_mainfile(filepath=str(ROOT.parents[1]/'outputs/c1m1_hotel_with_transition.blend'),load_ui=False,use_scripts=False)
    snapshot=charts.ChartSnapshot(bpy.context);deps=bpy.context.evaluated_depsgraph_get()
    results=[];original=charts.chart_from_side
    def capture(side,vertices,index):
        try:return original(side,vertices,index)
        except Exception as exc:
            results.append({'material':side.get('material'),'uaxis':side.get('uaxis'),'vaxis':side.get('vaxis'),
                'plane':side.get('plane'),'vertices':vertices,'error':str(exc)})
            raise
    charts.chart_from_side=capture
    for name in ('brush 2','brush 9','brush 30'):
        obj=bpy.data.objects.get(name)
        if obj:
            try:snapshot.object_charts(obj,deps)
            except Exception:results[-1]['object']=name
    return {'imported':snapshot.document is not None,'rejections':results}
try:result=run()
except Exception:result={'error':traceback.format_exc()}
OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
