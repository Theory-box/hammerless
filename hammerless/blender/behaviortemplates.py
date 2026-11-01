"""Editable temporal groups; all calculations and storage are visible base nodes."""
import bpy
from .logicgroups import new_group_tree, new_item, sync


class Builder:
    def __init__(self, name, parameters, outputs, action=False):
        self.tree = new_group_tree(name + (' (Action Memory)' if action else ''))
        self.action = action
        self.parameters = parameters
        for uid, label, kind, default in parameters:
            new_item(self.tree, label, kind, False, uid)
        for uid, label, kind in outputs:
            new_item(self.tree, label, kind, True, uid)
        self.index = 0
        self.input = self.node('HL_NodeGroupInput', 'Inputs')
        self.output = self.node('HL_NodeGroupOutput', 'Results after Next')
        sync(self.tree)

    def node(self, cls, label, **props):
        n = self.tree.nodes.new(cls)
        n.label, n.width = label, 230
        n.location = (self.index % 5 * 280, -(self.index // 5) * 270)
        self.index += 1
        for key, value in props.items():
            setattr(n, key, value)
        return n

    @staticmethod
    def socket(n, uid, output=False):
        return next(s for s in (n.outputs if output else n.inputs) if s.identifier == uid)

    def wire(self, source, uid, target, inp):
        self.tree.links.new(self.socket(source, uid, True), self.socket(target, inp))

    def feed(self, source, target, inp):
        if isinstance(source, tuple):
            self.wire(source[0], source[1], target, inp)
        else:
            self.socket(target, inp).value = source

    def math(self, label, op, a, b=0):
        n = self.node('HL_NodeMath', label, op=op)
        self.feed(a, n, 'a'); self.feed(b, n, 'b')
        return n, 'value'

    def param(self, uid):
        return self.input, uid

    def read(self):
        if self.action:
            n = self.node('HL_BAStateGet', 'Inspect saved value', state_name='value', value_type='num', group_local=True)
            return n, 'value'
        n = self.node('HL_NodeGetVariable', 'Inspect saved value', scope='GROUP', var_name='value')
        return n, 'result'

    def save(self, label, value):
        n = (self.node('HL_BAStateSet', label, state_name='value', value_type='num', group_local=True) if self.action else
             self.node('HL_NodeSetVariable', label, scope='GROUP', var_name='value'))
        self.feed(value, n, 'value')
        self.wire(n, 'next' if self.action else 'then', self.output, 'next')
        return n

    def branch(self, label, condition):
        n = self.node('HL_NodeIf', label)
        self.feed(condition, n, 'condition')
        return n

    def finish(self, value, update):
        reset = self.branch('Reset takes priority over updating', self.param('reset'))
        self.wire(self.input, 'run', reset, 'in')
        clear = self.save('Reset stored value', 0)
        self.wire(reset, 'true', clear, 'run')
        self.wire(reset, 'false', update, 'run' if update.kind in {'SET_VAR', 'BA_STATE_SET'} else 'in')
        self.feed(value, self.output, 'value')
        return self.tree


COMMON = (('run', 'Update', 'event', None), ('reset', 'Reset to Zero', 'bool', False),
          ('delta', 'Elapsed Seconds This Update', 'num', .1))
PARAMETERS = {
    'SUSTAINED': COMMON + (('condition', 'Condition', 'bool', False), ('duration', 'Required Seconds', 'num', 1.0)),
    'ACCUMULATE': COMMON + (('rate', 'Add Per Second', 'num', 1.0), ('decay', 'Decay Per Second', 'num', .25), ('maximum', 'Maximum Value', 'num', 1.0)),
    'RATE': COMMON + (('target', 'Target Value', 'num', 1.0), ('speed', 'Maximum Change Per Second', 'num', 1.0)),
}


def make_template(kind, action=False):
    names = {'SUSTAINED': 'Sustained Condition', 'ACCUMULATE': 'Accumulate With Decay', 'RATE': 'Limit Rate of Change'}
    outputs = [('next', 'Next', 'event'), ('value', 'Elapsed Seconds' if kind == 'SUSTAINED' else 'Value', 'num')]
    if kind == 'SUSTAINED':
        outputs.append(('ready', 'Satisfied', 'bool'))
    b = Builder(names[kind], PARAMETERS[kind], outputs, action)
    old = b.read()
    delta = b.math('Ignore negative elapsed time', 'MAXIMUM', b.param('delta'), 0)
    if kind == 'SUSTAINED':
        elapsed = b.math('Accumulate consecutive true time', 'ADD', old, delta)
        update = b.branch('Condition still true?', b.param('condition'))
        save = b.save('Save elapsed time', elapsed)
        clear = b.save('False condition clears elapsed time', 0)
        b.wire(update, 'true', save, 'run'); b.wire(update, 'false', clear, 'run')
        threshold = b.math('Clamp required duration to zero', 'MAXIMUM', b.param('duration'), 0)
        compare = b.node('HL_NodeCompare', 'Required duration reached', op='GREATER_EQUAL')
        b.feed(old, compare, 'a'); b.feed(threshold, compare, 'b')
        ready = b.node('HL_NodeBoolMath', 'Only satisfied while condition is true', op='AND')
        b.wire(compare, 'result', ready, 'a'); b.feed(b.param('condition'), ready, 'b')
        not_reset = b.node('HL_NodeBoolMath', 'Reset is never satisfied', op='NOT')
        b.feed(b.param('reset'), not_reset, 'a')
        result = b.node('HL_NodeBoolMath', 'Satisfied', op='AND')
        b.wire(ready, 'result', result, 'a'); b.wire(not_reset, 'result', result, 'b')
        b.wire(result, 'result', b.output, 'ready')
    elif kind == 'ACCUMULATE':
        decay = b.math('Nonnegative decay', 'MAXIMUM', b.param('decay'), 0)
        rate = b.math('Net rate', 'SUBTRACT', b.param('rate'), decay)
        amount = b.math('Change over elapsed time', 'MULTIPLY', rate, delta)
        value = b.math('Add change', 'ADD', old, amount)
        value = b.math('Floor at zero', 'MAXIMUM', value, 0)
        maximum = b.math('Nonnegative maximum', 'MAXIMUM', b.param('maximum'), 0)
        value = b.math('Cap accumulated value', 'MINIMUM', value, maximum)
        update = b.save('Save accumulated value', value)
    else:
        speed = b.math('Nonnegative speed', 'MAXIMUM', b.param('speed'), 0)
        step = b.math('Maximum step', 'MULTIPLY', speed, delta)
        negative = b.math('Maximum downward step', 'MULTIPLY', step, -1)
        difference = b.math('Distance to target', 'SUBTRACT', b.param('target'), old)
        change = b.math('Limit upward change', 'MINIMUM', difference, step)
        change = b.math('Limit downward change', 'MAXIMUM', change, negative)
        update = b.save('Save limited value', b.math('Apply step', 'ADD', old, change))
    return b.finish(old, update)


class HL_OT_behavior_template(bpy.types.Operator):
    bl_idname, bl_label = 'hammerless.behavior_template', 'Add Editable Temporal Group'
    bl_description = 'Editable base-node group with per-copy memory. Update once per interval and supply its elapsed seconds; read results after Next. When added inside an action, memory is per bot and action'
    bl_options = {'REGISTER', 'UNDO'}
    template: bpy.props.EnumProperty(items=[('SUSTAINED', 'Sustained Condition', 'Measure consecutive true updates'),
        ('ACCUMULATE', 'Accumulate With Decay', 'Integrate addition minus decay per second, bounded by zero and Maximum'),
        ('RATE', 'Limit Rate of Change', 'Move a number toward a target at a maximum rate')])

    @classmethod
    def poll(cls, context):
        tree = getattr(getattr(context, 'space_data', None), 'edit_tree', None)
        return tree is not None and tree.bl_idname == 'HL_LogicTree'

    def execute(self, context):
        tree = context.space_data.edit_tree
        action = getattr(tree, 'bot_action', False) or any(
            getattr(getattr(item, 'node_tree', None), 'bot_action', False)
            for item in getattr(context.space_data, 'path', ()))
        asset = make_template(self.template, action=action)
        for n in tree.nodes:
            n.select = False
        node = tree.nodes.new('HL_NodeGroup'); node.group = asset
        node.location = context.space_data.cursor_location
        for uid, label, kind, default in PARAMETERS[self.template]:
            if default is not None:
                Builder.socket(node, uid).value = default
        node.select = True; tree.nodes.active = node
        return {'FINISHED'}


def draw_menu(layout):
    for key, title in [('SUSTAINED', 'Sustained Condition'), ('ACCUMULATE', 'Accumulate With Decay'), ('RATE', 'Limit Rate of Change')]:
        layout.operator(HL_OT_behavior_template.bl_idname, text=title).template = key


class HL_MT_behavior_utilities(bpy.types.Menu):
    bl_idname, bl_label = 'HL_MT_behavior_utilities', 'Behavior Utilities'

    def draw(self, context):
        draw_menu(self.layout)
        self.layout.separator()
        from . import stabletemplates
        stabletemplates.draw_menu(self.layout)


def register():
    bpy.utils.register_class(HL_OT_behavior_template)
    bpy.utils.register_class(HL_MT_behavior_utilities)


def unregister():
    bpy.utils.unregister_class(HL_MT_behavior_utilities)
    bpy.utils.unregister_class(HL_OT_behavior_template)


