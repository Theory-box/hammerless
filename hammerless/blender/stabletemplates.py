"""Stable selection assembled entirely from public query, math and state nodes."""
import bpy
from .behaviortemplates import Builder

PARAMETERS = (
    ('run', 'Update', 'event', None), ('results', 'Fresh Ranked Results', 'candidate_results', None),
    ('reset', 'Clear Selection', 'bool', False), ('delta', 'Elapsed Seconds This Update', 'num', .1),
    ('margin', 'Required Score Improvement', 'num', .1), ('hold', 'Minimum Hold Seconds', 'num', .5),
)


class SelectionBuilder(Builder):
    def read_state(self, name, typ):
        n = (self.node('HL_BAStateGet', 'Read '+name, state_name=name, value_type=typ, group_local=True)
             if self.action else self.node('HL_NodeGetVariable', 'Read '+name, scope='GROUP', var_name=name, value_kind=typ))
        return n, 'value' if self.action else 'result'

    def write_state(self, name, typ, value):
        n = (self.node('HL_BAStateSet', 'Store '+name, state_name=name, value_type=typ, group_local=True)
             if self.action else self.node('HL_NodeSetVariable', 'Store '+name, scope='GROUP', var_name=name, value_kind=typ))
        if value is not None:
            self.feed(value, n, 'value')
        return n

    def chain(self, source, steps, finish=False):
        for name, typ, value in steps:
            node = self.write_state(name, typ, value)
            self.feed(source, node, 'run')
            source = node, 'next' if self.action else 'then'
        if finish:
            self.feed(source, self.output, 'next')
        return source

    def compare(self, label, op, a, other):
        n = self.node('HL_NodeCompare', label, op=op)
        self.feed(a, n, 'a'); self.feed(other, n, 'b')
        return n, 'result'


def make_template(list_type='navareas', action=False):
    typ = 'bot' if list_type == 'bots' else 'navarea'
    outputs = [('next','Next','event'), ('item','Selected Item',typ), ('score','Selected Score','num'),
               ('position','Sampled Position','vec'), ('has','Has Selection','bool'),
               ('changed','Selection Changed','bool'), ('held','Held Seconds','num'),
               ('truncated','Snapshot Truncated','bool'), ('missing','Previous Selection Missing','bool')]
    b = SelectionBuilder('Stable '+('Bot' if typ=='bot' else 'Nav Area')+' Selection', PARAMETERS, outputs, action)
    state = {uid:b.read_state(uid, kind) for uid, label, kind in outputs if kind != 'event'}
    for uid, value in state.items(): b.feed(value, b.output, uid)
    current = b.node('HL_FindRankedCandidate', 'Find retained item in fresh snapshot', list_type=list_type)
    best = b.node('HL_RankedCandidate', 'Best challenger (rank zero)', list_type=list_type)
    b.feed(b.param('results'), current, 'results'); b.feed(state['item'], current, 'item')
    b.feed(b.param('results'), best, 'results')
    b.wire(b.input, 'run', current, 'run'); b.wire(current, 'next', best, 'run')
    absent=b.node('HL_NodeBoolMath','Retained item not found',op='NOT'); b.wire(current,'found',absent,'a')
    missing=b.node('HL_NodeBoolMath','A previous selection disappeared from snapshot',op='AND')
    b.wire(absent,'result',missing,'a'); b.feed(state['has'],missing,'b')
    after_query = b.chain((best,'next'), [('missing','bool',(missing,'result')), ('changed','bool',False), ('truncated','bool',(current,'truncated'))])
    reset = b.branch('Clear takes priority', b.param('reset')); b.feed(after_query, reset, 'in')
    clear = [('changed','bool',state['has']), ('item',typ,None), ('has','bool',False), ('score','num',0),
             ('held','num',0), ('position','vec',None)]
    b.chain((reset,'true'), clear, True)
    present = b.branch('Retained item remains in snapshot?', (current,'found'))
    b.wire(reset,'false',present,'in')
    available = b.branch('Any valid challenger?', (best,'found'))
    b.wire(present,'false',available,'in')
    b.chain((available,'false'),clear,True)
    accept = [('changed','bool',True), ('item',typ,(best,'item')), ('score','num',(best,'score')),
              ('position','vec',(best,'position')), ('has','bool',True), ('held','num',0)]
    b.chain((available,'true'),accept,True)
    elapsed = b.math('Ignore negative elapsed time','MAXIMUM',b.param('delta'),0)
    elapsed = b.math('Advance retained time','ADD',state['held'],elapsed)
    after_time = b.chain((present,'true'), [('held','num',elapsed)])
    min_hold = b.math('Nonnegative hold','MAXIMUM',b.param('hold'),0)
    ready = b.compare('Hold time complete','GREATER_EQUAL',state['held'],min_hold)
    margin = b.math('Nonnegative improvement margin','MAXIMUM',b.param('margin'),0)
    threshold = b.math('Current freshly evaluated score plus margin','ADD',(current,'score'),margin)
    better = b.compare('Strictly better; equal scores retain choice','GREATER',(best,'score'),threshold)
    both = b.node('HL_NodeBoolMath','Hold complete and score improved',op='AND')
    b.feed(ready,both,'a');b.feed(better,both,'b')
    valid = b.node('HL_NodeBoolMath','Challenger must still be valid',op='AND')
    b.wire(both,'result',valid,'a');b.wire(best,'found',valid,'b')
    switch = b.branch('Replace retained item?',(valid,'result'));b.feed(after_time,switch,'in')
    b.chain((switch,'true'),accept,True)
    b.chain((switch,'false'), [('score','num',(current,'score')),('position','vec',(current,'position')),('has','bool',True)], True)
    return b.tree


class HL_OT_stable_selection(bpy.types.Operator):
    bl_idname, bl_label = 'hammerless.stable_selection', 'Add Editable Stable Selection'
    bl_description = 'Retain a bot or nav area until another score improves enough after the hold time. Uses membership in fresh Ranked Results; a missing item is replaced even if the snapshot was truncated. Open the group to edit its public base nodes'
    bl_options = {'REGISTER','UNDO'}
    list_type: bpy.props.EnumProperty(items=[('bots','Bots',''),('navareas','Nav Areas','')],default='navareas')
    @classmethod
    def poll(cls,context):
        tree=getattr(getattr(context,'space_data',None),'edit_tree',None)
        return tree is not None and tree.bl_idname=='HL_LogicTree'
    def execute(self,context):
        tree=context.space_data.edit_tree
        action=getattr(tree,'bot_action',False) or any(getattr(getattr(it,'node_tree',None),'bot_action',False) for it in getattr(context.space_data,'path',()))
        asset=make_template(self.list_type,action)
        for n in tree.nodes:n.select=False
        node=tree.nodes.new('HL_NodeGroup');node.group=asset;node.location=context.space_data.cursor_location
        for uid,label,kind,default in PARAMETERS:
            if default is not None:Builder.socket(node,uid).value=default
        node.select=True;tree.nodes.active=node
        return {'FINISHED'}


def draw_menu(layout):
    for typ,label in [('bots','Stable Bot Selection'),('navareas','Stable Nav Area Selection')]:
        layout.operator(HL_OT_stable_selection.bl_idname,text=label).list_type=typ

def register():bpy.utils.register_class(HL_OT_stable_selection)
def unregister():bpy.utils.unregister_class(HL_OT_stable_selection)
