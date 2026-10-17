"""A logic graph that uses every Combat & Physics node, for the Blender compile test and the in-game check
(tests/ingame/test_combat_nodes.py). Every second:
- each survivor traces a line from their eyes along their view; on a hit: a beam to it, a fire particle there,
  Damage on what it hit, and the variable hlt_hits counts up;
- for each survivor, every common within 400 units: an Explosion there, Damage and a Push up on it, Aim At it
  (the survivor's view), and hlt_blasts counts up; hlt_seen holds whether anyone sees the survivor."""


def _in(n, ident):
    return next(s for s in n.inputs if s.identifier == ident)


def _out(n, ident):
    return next(s for s in n.outputs if s.identifier == ident)


def build(tree):
    nodes, link = tree.nodes, tree.links.new

    def add(t, **props):
        n = nodes.new(t)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    # --- traces along each survivor's view
    t1 = add("HL_NodeTimer", seconds=1.0)
    each = add("HL_NodeForEach", what="SURVIVORS")
    view = add("HL_NodePlayerView")
    scale = add("HL_NodeVectorMath", op="SCALE")
    _in(scale, "scale").value = 1500.0
    end = add("HL_NodeVectorMath", op="ADD")
    trace = add("HL_NodeTrace", hits="SHOTS")
    hits = add("HL_NodeSetVariable", var_name="hlt_hits", op="ADD")
    _in(hits, "value").value = 1.0
    beam = add("HL_NodeBeam")
    _in(beam, "seconds").value = 0.5
    fire = add("HL_NodeParticle")
    _in(fire, "effect").value = "fire_small_01"
    _in(fire, "seconds").value = 1.0
    hurt = add("HL_NodeDamage", damage_type="2")
    _in(hurt, "amount").value = 5.0
    link(_out(t1, "tick"), _in(each, "run"))
    link(_out(each, "item"), _in(view, "player"))
    link(_out(view, "forward"), _in(scale, "a"))
    link(_out(view, "eye"), _in(end, "a"))
    link(_out(scale, "vector"), _in(end, "b"))
    link(_out(each, "each"), _in(trace, "run"))
    link(_out(view, "eye"), _in(trace, "start"))
    link(_out(end, "vector"), _in(trace, "end"))
    link(_out(each, "item"), _in(trace, "ignore"))
    link(_out(trace, "hit"), _in(hits, "run"))
    link(_out(hits, "then"), _in(beam, "run"))
    link(_out(view, "eye"), _in(beam, "start"))
    link(_out(trace, "pos"), _in(beam, "end"))
    link(_out(beam, "then"), _in(fire, "run"))
    link(_out(trace, "pos"), _in(fire, "position"))
    link(_out(fire, "then"), _in(hurt, "run"))
    link(_out(trace, "entity"), _in(hurt, "entity"))
    link(_out(each, "item"), _in(hurt, "attacker"))
    # --- commons near each survivor
    t2 = add("HL_NodeTimer", seconds=1.0)
    each2 = add("HL_NodeForEach", what="SURVIVORS")
    view2 = add("HL_NodePlayerView")
    near = add("HL_NodeFindNearby", what="COMMONS")
    _in(near, "radius").value = 400.0
    each3 = add("HL_NodeForEach", what="LIST")
    zview = add("HL_NodePlayerView")             # (on a common: its centre)
    boom = add("HL_NodeExplosion")
    _in(boom, "damage").value = 200.0
    _in(boom, "radius").value = 100.0
    hit2 = add("HL_NodeDamage", damage_type="64")
    _in(hit2, "amount").value = 1.0
    up = add("HL_NodeMakeVector")
    _in(up, "z").value = 300.0
    push = add("HL_NodePush", mode="ADD")
    aim = add("HL_NodeAimAt")
    _in(aim, "max_turn").value = 30.0
    blasts = add("HL_NodeSetVariable", var_name="hlt_blasts", op="ADD")
    _in(blasts, "value").value = 1.0
    seen = add("HL_NodeSeenBy")
    seen_var = add("HL_NodeSetVariable", var_name="hlt_seen", value_kind="bool")
    link(_out(t2, "tick"), _in(each2, "run"))
    link(_out(each2, "item"), _in(view2, "player"))
    link(_out(view2, "eye"), _in(near, "position"))
    link(_out(each2, "each"), _in(each3, "run"))
    link(_out(near, "list"), _in(each3, "list"))
    link(_out(each3, "item"), _in(zview, "player"))
    link(_out(each3, "each"), _in(boom, "run"))
    link(_out(zview, "eye"), _in(boom, "position"))
    link(_out(boom, "then"), _in(hit2, "run"))
    link(_out(each3, "item"), _in(hit2, "entity"))
    link(_out(each2, "item"), _in(hit2, "attacker"))
    link(_out(hit2, "then"), _in(push, "run"))
    link(_out(each3, "item"), _in(push, "entity"))
    link(_out(up, "result"), _in(push, "velocity"))
    link(_out(push, "then"), _in(aim, "run"))
    link(_out(each2, "item"), _in(aim, "entity"))
    link(_out(zview, "eye"), _in(aim, "target"))
    link(_out(aim, "then"), _in(blasts, "run"))
    link(_out(each2, "done"), _in(seen_var, "run"))
    link(_out(each2, "item"), _in(seen, "target"))
    link(_out(seen, "seen"), _in(seen_var, "value"))
    return tree
