"""Connect reusable action assets into explicit choices and sequences."""
import bpy
from bpy.props import EnumProperty
from .logic import _Block
from .botactions import _Inside

class HL_BAChoose(_Block,bpy.types.Node):
    bl_idname,bl_label,kind='HL_BAChoose','Choose Action','BA_CHOOSE'
    bl_description='Run the True or False action for each bot. Changing the condition stops the previous action before starting the next. An empty selected socket runs no child. Child completion finishes this action until removed and reapplied'
    def make_sockets(self):
        self.data_in('bool','condition','Condition')
        self.data_in('action','true_action','If True')
        self.data_in('action','false_action','If False')
        self.data_out('action','action','Action')

class HL_BAPriority(_Block,bpy.types.Node):
    bl_idname,bl_label,kind='HL_BAPriority','Priority Actions','BA_PRIORITY'
    bl_description='Run the first enabled connected action, from top to bottom. Higher priority interrupts lower priority with cleanup. Child completion finishes this action until it is removed and reapplied'
    def make_sockets(self):
        for i in range(1,4):
            self.data_in('bool',f'enabled_{i}',f'Enable {i}').value=True
            self.data_in('action',f'action_{i}',f'Priority {i}')
        self.data_out('action','action','Action')

class HL_BASequence(_Block,bpy.types.Node):
    bl_idname,bl_label,kind='HL_BASequence','Sequence Actions','BA_SEQUENCE'
    bl_description='Run connected actions in order. A child must report Success before advancing; Failed ends the sequence. Empty sockets are skipped. Completion stays latched until the sequence is removed and reapplied'
    def make_sockets(self):
        for i in range(1,4):
            self.data_in('action',f'action_{i}',f'Step {i}')
        self.data_out('action','action','Action')

class HL_BAResult(_Inside,bpy.types.Node):
    bl_idname,bl_label,kind='HL_BAResult','Finish Action','BA_RESULT'
    bl_description='Report Success or Failed and end this lifecycle callback immediately. Stop cleanup runs once. Success advances a parent sequence; failure ends it. An action without this node remains running'
    result: EnumProperty(name='Result',items=[('SUCCESS','Success','The action achieved its goal'),('FAILED','Failed','The action could not achieve its goal')],default='SUCCESS')
    def make_sockets(self):self.ev_in('run','Run')
    def draw_buttons(self,context,layout):layout.prop(self,'result',text='')
    def settings(self):return {'result':self.result}

OUTSIDE=(HL_BAChoose,HL_BAPriority,HL_BASequence)
INSIDE=(HL_BAResult,)
CLASSES=OUTSIDE+INSIDE

def register():
    for cls in CLASSES:bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
