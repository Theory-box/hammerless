"""A mapped compiler failure selects its source object and positions the cursor."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.problems import store, go_to_problem
from hammerless.core.build import Report
from hammerless.core.mapcheck import Problem

obj = bpy.data.objects.new('Failing Surface', None)
bpy.context.scene.collection.objects.link(obj)
message = 'Compiler: Bad surface extents: brick/test'
report = Report()
report.errors = [message]
report.problems = [Problem('ERROR', message, 'Failing Surface (part 11)', (100, 200, 300))]
store(bpy.context, report)
assert len(bpy.context.scene.hammerless.problems) == 1
go_to_problem(bpy.context, 0)
assert bpy.context.view_layer.objects.active == obj
assert obj.select_get()
scale = bpy.context.scene.hammerless.units_per_meter
assert all(abs(bpy.context.scene.cursor.location[i] * scale - value) < 0.001
           for i, value in enumerate((100, 200, 300)))
print('PASS compiler problem selects source and locates surface')
hammerless.unregister()
