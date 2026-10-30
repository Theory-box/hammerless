"""Run the actual compiled example callbacks in optional squirrel-lang inside Blender.

Set SQUIRREL_PYTHONPATH to a Python 3.11 squirrel-lang installation.
"""
import os
from pathlib import Path
import runpy
import sys

scope = runpy.run_path(str(Path(__file__).with_name('test_fearlight.py')))
if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0, os.environ['SQUIRREL_PYTHONPATH'])
import squirrel
script = scope['script']
start = script.index('::HL_Action_')
end = script.index('::HL_BA_Start_', start)
action_script = script[start:end]
cases = Path(__file__).with_suffix('.nut').read_text()
prelude, assertions = cases.split('// GENERATED_ACTION_HERE')
squirrel.SQVM().execute(prelude + action_script + assertions)
print('FEAR_LIGHT_CALLBACKS_OK')
