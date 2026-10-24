"""Ownership policy for the older teaching examples.

Ordinary nodes remain editable. References to partners are RNA pointers on the
owner's behaviour slot, so Blender's normal duplicate/remap/undo machinery owns
their lifetime. No duplicate handler, name matching, or hidden scene graph.
"""
from copy import deepcopy
import bpy


# These objects illustrate a map-wide query, rather than own the query.
GLOBAL_WITH_SAMPLES = {'Gas Can Counter'}

DESCRIPTIONS = {
    'Sleepers': 'Each placed character carries a Sleeper behaviour. A nearby survivor, a nearby gunshot, or a shot at this character wakes it. It stands up using its pose, becomes a real common in the same clothes, and attacks. Duplicate a character to add another; names do not control membership. Damage hooks need Hammerless\'s game mode.',
    'Husks': 'Each placed character carries a Husk behaviour. When this character is hit, it becomes a common that immediately dies and ragdolls. Duplicate the character to add another. Damage hooks need Hammerless\'s game mode.',
    'Power Goes Out': 'The button owns this behaviour, with two editable lamp targets in its settings. Press it: both lights stutter, switch off after 3 seconds, and return after 30 seconds; a horde starts when the lights go out. Duplicate the button and lamps together for a separate assembly. Switchable baked lights consume lighting layers.',
    'Lights Flicker While a Tank Lives': 'Each lamp owns its flicker behaviour. Tank spawn starts the flicker; once no Tank remains the lamp becomes steady. Duplicate either lamp to add another. Switchable baked lights consume lighting layers.',
    'Carry the Crates': 'This crate permits survivors to pick it up. Self compares the requested object with this crate. Duplicate it for another carryable crate.',
}


def _character(ex):
    """Replace the old wildcard scans with one Self instance per character."""
    from .logic_examples import N
    ex = deepcopy(ex)
    sleeper = ex['title'] == 'Sleepers'
    remove = {'vname', 'each', 'each2'} if sleeper else {'vname'}
    test = 'is_sleeper' if sleeper else 'is_husk'
    ex['nodes'] = [n for n in ex['nodes'] if n['id'] not in remove | {test}]
    ex['nodes'] += [N('owner', 'HL_NodeSelf', -1, 0),
                    N(test, 'HL_NodeCheck', 2, 9 if sleeper else 1, op='EQUAL')]
    links = []
    for a, b in ex['links']:
        if b.split('.')[0] in remove or a.split('.')[0] == 'vname':
            continue
        a = {'each.each':'timer.tick', 'each.item':'owner.entity',
             'each2.each':'shot.happened', 'each2.item':'owner.entity'}.get(a, a)
        links.append((a, b))
    links += [('ask.victim', test + '.a'), ('owner.entity', test + '.b')]
    ex['links'] = links
    ex['steps'] = [(label, [i for i in ids if i not in remove]) for label, ids in ex.get('steps', [])]
    ex['about'] = ('Each character carries this behaviour. Duplicate the character to add another; '
                   'Self identifies it, regardless of its name. ' + ex['about'].split('(Shots')[0])
    return ex


def _prepare(ex):
    from .logic_examples import N
    ex = deepcopy(ex)
    if ex['title'] in ('Sleepers', 'Husks'):
        return _character(ex)
    if ex['title'] == 'Top-Down Camera':
        ex['nodes'] = [N('ring', 'HL_NodeSelf', 0, 2) if n['id'] == 'ring' else n for n in ex['nodes']]
        ex['links'] = [('ring.entity' if a == 'ring.result' else a, b) for a,b in ex['links']]
    if ex['title'] == 'Power Goes Out':
        for n in list(ex['nodes']):
            if n['props'].get('target') == '@lamp1':
                other = deepcopy(n)
                other['id'] += '_second'
                other['props']['target'] = '@lamp2'
                other['pos'] = (n['pos'][0], n['pos'][1] + 4)
                ex['nodes'].append(other)
                ex['links'] += [(a, other['id'] + '.' + b.split('.')[1]) for a,b in list(ex['links'])
                                if b.split('.')[0] == n['id']]
    if ex['title'] == 'Carry the Crates':
        # This object's pickup permission; leave the unrelated bot rule out of
        # the object example rather than duplicating a global override with it.
        keep = {'pickup', 'allow'}
        ex['nodes'] = [n for n in ex['nodes'] if n['id'] in keep]
        ex['nodes'] += [N('self', 'HL_NodeSelf', 0, 2), N('mine', 'HL_NodeCheck', 1, 1, op='EQUAL')]
        ex['links'] = [('pickup.asked','allow.run'), ('pickup.object','mine.a'),
                       ('self.entity','mine.b'), ('mine.result','allow.answer')]
        ex['steps'] = []
        ex['about'] = 'This crate permits survivors to pick it up. Self compares the requested object with this crate. Duplicate it for another carryable crate.'
    return ex


def applicable(ex):
    return bool(ex.get('objects') and ex.get('nodes') and not ex.get('behaviours')
                and ex['title'] not in GLOBAL_WITH_SAMPLES)


def build(context, ex, objects, problems, number):
    from .logic_examples import _fill, _socket, _place, _note, _groups, COL, ROW
    from .logic import _rebuild_entity_sockets
    from . import exposed
    ex = _prepare(ex)
    for spec in [ex, *ex.get('groups', {}).values()]:
        for n in spec['nodes']:
            if n['type'] in ('HL_NodeGetVariable', 'HL_NodeSetVariable') and n['props'].get('scope', 'MAP') == 'MAP':
                n['props']['scope'] = 'OBJECT'
    groups = _groups(ex, objects, problems)
    # Identical autonomous characters/lights each get an instance. An assembly
    # lives on its initiating object, with visible, editable partner references.
    each = ex['title'] in ('Sleepers', 'Husks', 'Lights Flicker While a Tank Lives')
    owners = list(objects) if each else [next(iter(objects))]
    trees = []
    for owner_key in owners:
        owner = objects[owner_key]
        tree = bpy.data.node_groups.new(ex['title'] + ' Behaviour', 'HL_LogicTree')
        tree.is_behaviour = True
        slot = owner.hammerless.behaviours.add()
        slot.graph = tree
        owner.hammerless.behaviours_index = len(owner.hammerless.behaviours) - 1
        nodes = _fill(tree, ex['nodes'], [], objects, groups, problems)
        self_node = next((n for n in nodes.values() if n.bl_idname == 'HL_NodeSelf'), None)
        if self_node is None:
            self_node = tree.nodes.new('HL_NodeSelf')
            self_node.location = (-COL, 0)
        refs = {}

        def source(obj, entity=False):
            if obj == owner or (each and ex['title'] == 'Lights Flicker While a Tank Lives'):
                return _socket(self_node, 'entity' if entity else 'object', True)
            key = next(k for k,v in objects.items() if v == obj)
            if key not in refs:
                n = tree.nodes.new('HL_NodeExposedSetting')
                n.setting_id = 'example_target_' + key
                n.value_type = 'OBJECT'
                n.ui_name = key.replace('_', ' ').title()
                n.location = (-COL, -(len(refs)+1)*ROW)
                refs[key] = n
                exposed.sync_slot(slot)
                value = slot.values[n.setting_id]
                value.object = obj
                value.overridden = True
            return _socket(refs[key], 'value' if entity else 'object', True)

        for node in list(nodes.values()):
            if node.bl_idname == 'HL_NodeObject' and node.target in objects.values():
                obj, cls = node.target, node.classname
                parameters = {s.identifier:s.value for s in node.inputs if s.bl_idname == 'HL_EventSocket'}
                node.target = None
                node.classname = cls
                if obj == owner or each:
                    node.use_self = True
                else:
                    _rebuild_entity_sockets(node, cls)
                    tree.links.new(source(obj), _socket(node, 'object', False))
                for sock in node.inputs:
                    if sock.bl_idname == 'HL_EventSocket' and sock.identifier in parameters:
                        sock.value = parameters[sock.identifier]
            for sock in list(node.inputs):
                if sock.bl_idname not in ('HL_ObjectSocket', 'HL_ThingSocket'):
                    continue
                if sock.value is not None and sock.value in objects.values():
                    obj = sock.value
                    sock.value = None
                    tree.links.new(source(obj, sock.bl_idname == 'HL_ThingSocket'), sock)
        for a,b in ex['links']:
            an,ao = a.split('.'); bn,bi = b.split('.')
            out, inp = _socket(nodes[an], ao, True), _socket(nodes[bn], bi, False)
            if out is None or inp is None:
                problems.append(f"{ex['title']}: missing socket {a} -> {b}")
            else:
                tree.links.new(out, inp)
        for label, ids in ex.get('steps', []):
            frame = tree.nodes.new('NodeFrame'); frame.label = label
            for nid in ids:
                if nid in nodes: nodes[nid].parent = frame
        for spec in ex['nodes']:
            c,r = spec['pos']; _place(nodes[spec['id']], c*COL, r*ROW)
        _note(tree, f'Example {number:02d}: {ex["title"]}',
              ['Owned by this object. Self follows a duplicate.',
               'Duplicate an assembly together to copy its targets; partner settings are on the owner.',
               'Game-wide actions (time scale, hordes, cameras) still affect the whole map.'], 0, 220, 950)
        trees.append(tree)
    # Wildcard selectors were replaced with Self. Remove shared light/character
    # names so exported copies cannot accidentally address the original entity.
    if each or ex['title'] == 'Power Goes Out':
        for obj in objects.values():
            for i in reversed(range(len(obj.hammerless.keyvalues))):
                if obj.hammerless.keyvalues[i].key == 'targetname': obj.hammerless.keyvalues.remove(i)
    bpy.ops.object.select_all(action='DESELECT')
    objects[owners[0]].select_set(True)
    context.view_layer.objects.active = objects[owners[0]]
    return trees[0]
