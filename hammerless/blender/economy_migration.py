"""Expand legacy economy nodes without replacing graphs or exposed setting IDs."""
import bpy


def expand(tree):
    from .logic_examples import _groups, _fill, _socket
    from .logic_examples_economy import BALANCE, AWARD, points_group, purchase_group, station
    legacy = [n for n in tree.nodes if n.bl_idname in ('HL_NodePersonalPoints', 'HL_NodeWallWeapon')]
    if not legacy or tree.library:
        return 0
    problems = []
    groups = _groups({'groups': {'Points: Balance': BALANCE, 'Points: Award': AWARD,
                                'Personal Points': points_group(), 'Purchase: Rules': purchase_group()}}, {}, problems)
    if problems:
        raise RuntimeError('; '.join(problems))
    for old in legacy:
        created = []
        before = {n.as_pointer() for n in tree.nodes}
        try:
            if old.bl_idname == 'HL_NodePersonalPoints':
                node = tree.nodes.new('HL_NodeGroup')
                created.append(node)
                node.group = groups['Personal Points']
                for key in ('starting','common','special','tank','witch','feedback'):
                    _socket(node,key,False).value = getattr(old,key)
                node.parent, node.location, node.mute = old.parent, old.location, old.mute
                node.label = old.label
            else:
                # Keep every existing setting node and its UUID; only replace the opaque action.
                spec = station(old.item, old.price)
                node_specs = [n for n in spec['nodes'] if n['id'] != 'self' and not n['id'].endswith('_setting')]
                keep = {n['id'] for n in node_specs}
                links = [(a,b) for a,b in spec['links'] if a.split('.')[0] in keep and b.split('.')[0] in keep]
                nodes = _fill(tree,node_specs,links,{},groups,problems)
                created = [n for n in tree.nodes if n.as_pointer() not in before]
                if problems:
                    raise RuntimeError('; '.join(problems))
                item = nodes['item']
                for key in ('item','custom_item','inventory_class','inventory_model'):
                    setattr(item,key,getattr(old,key))
                def transfer(source, destination, fallback=None):
                    src = _socket(old,source,False)
                    dst = _socket(nodes[destination.split('.')[0]],destination.split('.')[1],False)
                    if src.is_linked:
                        for link in list(src.links):
                            tree.links.new(link.from_socket,dst)
                    elif hasattr(dst,'value'):
                        dst.value = src.value if fallback is None else fallback
                transfer('object','item.object')
                if _socket(old,'item',False).is_linked:
                    transfer('item','item.item')
                transfer('price','nonnegative.a',old.price)
                transfer('title','named.a',old.title)
                transfer('title','title.b',old.title)
                transfer('range','near.a',old.reach)
                tree.links.new(_socket(item,'entity',True),_socket(nodes['buy'],'entity',False))
                for ns in node_specs:
                    node = nodes[ns['id']]
                    node.parent = old.parent
                    node.location = (old.location.x + ns['pos'][0]*280, old.location.y - ns['pos'][1]*190)
                    node.mute = old.mute
            tree.nodes.remove(old)
        except Exception:
            for node in [n for n in tree.nodes if n.as_pointer() not in before]:
                tree.nodes.remove(node)
            raise
    return len(legacy)


@bpy.app.handlers.persistent
def on_load(*_):
    for tree in list(bpy.data.node_groups):
        try:
            if tree.bl_idname == 'HL_LogicTree':
                expand(tree)
        except ReferenceError:
            continue  # An unused example group was removed during group reuse.
        except Exception as error:
            # Keep legacy nodes usable if an unusual graph cannot be expanded.
            print(f'Hammerless: could not expand legacy economy in {tree.name}: {error}')
