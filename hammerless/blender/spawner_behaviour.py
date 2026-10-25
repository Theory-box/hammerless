"""Optional spawner automation built entirely from editable, ordinary nodes."""
from .logic_examples import N, _groups


def attach(obj):
    from .logic import ZOMBIE_TYPES
    from .exposed import sync_slot

    name = 'Zombie Spawner: Automatic Spawn'
    spec = dict(
        behaviour=True,
        about='Off by default: SpawnZombie inputs still work normally. Enable Spawn Automatically '
              'to spawn one chosen infected here when the map loads, after Delay Seconds. '
              'Each object has its own settings. This does not create a recurring spawn or a Director spawn spot.',
        nodes=[
            N('self', 'HL_NodeSelf', 0, 1),
            N('start', 'HL_NodeMapStart', 0, 0),
            N('enabled', 'HL_NodeExposedSetting', 0, 2, ui_name='Spawn Automatically',
              value_type='BOOL', default_boolean=False),
            N('type', 'HL_NodeExposedSetting', 0, 3, ui_name='Infected Type',
              value_type='ENUM', choices='; '.join(t+'='+t.title() for t in ZOMBIE_TYPES),
              default_text='witch'),
            N('delay', 'HL_NodeExposedSetting', 0, 4, ui_name='Delay Seconds',
              value_type='FLOAT', default_number=0.0),
            N('nonnegative', 'HL_NodeMath', 1, 4, op='MAXIMUM', sockets={'b': 0.0}),
            N('if', 'HL_NodeIf', 1, 0),
            N('spawn', 'HL_NodeFireInput', 2, 0, sockets={'input': 'SpawnZombie'}),
        ],
        links=[('start.start', 'if.in'), ('enabled.value', 'if.condition'),
               ('if.true', 'spawn.run'), ('self.entity', 'spawn.entity'),
               ('type.value', 'spawn.value'), ('delay.value', 'nonnegative.a'),
               ('nonnegative.value', 'spawn.delay')])
    problems = []
    tree = _groups({'groups': {name: spec}}, {}, problems, cleanup=False)[name]
    if problems:
        raise RuntimeError('; '.join(str(p) for p in problems))
    slot = obj.hammerless.behaviours.add()
    slot.graph = tree
    sync_slot(slot)
    return slot
