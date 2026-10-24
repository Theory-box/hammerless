"""Editable economy examples. Every rule below is an ordinary node or wire."""
from .logic_examples import N, ev, fn
from ..core.wall_weapon import ITEMS


def variable(nid, name, col, row, *, write=False, scope='MAP', kind='num', **kw):
    return N(nid, 'HL_NodeSetVariable' if write else 'HL_NodeGetVariable', col, row,
             var_name=name, scope=scope, value_kind=kind, **kw)


BALANCE = dict(
    about='Read a balance by human identity. Initial Value is stored on first access; changing Starting Points does not overwrite an existing balance.',
    inputs=[('key', 'Human Key', 'text')], outputs=[('value', 'Balance', 'num')],
    nodes=[variable('initial', 'points_starting', 0, 1),
           variable('balance', 'points', 1, 0, scope='KEY')],
    links=[('in.key', 'balance.key'), ('initial.result', 'balance.default'), ('balance.result', 'out.value')])

AWARD = dict(
    about='Validate a human killer, cap the balance, store it, and optionally print feedback. Edit these ordinary nodes to change the scoring rules.',
    inputs=[('run', 'Award', 'event'), ('player', 'Killer', 'thing'), ('amount', 'Reward', 'num'),
            ('feedback', 'Show Earned Points', 'bool')], outputs=[],
    nodes=[N('identity', 'HL_NodeHumanIdentity', 0, 2),
           N('valid', 'HL_NodeTextCheck', 1, 2, op='EQUAL', sockets={'b': ''}),
           N('human', 'HL_NodeIf', 2, 0),
           N('balance', 'HL_NodeGroup', 1, 3, group='Points: Balance'),
           N('positive', 'HL_NodeMath', 2, 3, op='MAXIMUM', sockets={'b': 0}),
           N('reward_limit', 'HL_NodeMath', 2, 5, op='MINIMUM', sockets={'b': 1000000}),
           N('sum', 'HL_NodeMath', 3, 3, integer=True, op='ADD'),
           N('cap', 'HL_NodeMath', 4, 3, integer=True, op='MINIMUM', sockets={'b': 1000000000}),
           variable('earned', 'earned', 3, 0, write=True, scope='GROUP'),
           N('difference', 'HL_NodeMath', 4, 4, integer=True, op='SUBTRACT'),
           variable('set', 'points', 4, 0, write=True, scope='KEY'),
           variable('earned_read', 'earned', 5, 3, scope='GROUP'),
           N('nonzero', 'HL_NodeCompare', 6, 3, op='GREATER', sockets={'b': 0}),
           N('show', 'HL_NodeBoolMath', 7, 3, op='AND'),
           N('feedback', 'HL_NodeIf', 5, 0),
           N('message', 'HL_NodeFormatText', 6, 1, template='+{a} points | Total: {b}'),
           fn('print', 'ClientPrint', 7, 0, {'p1': 4})],
    links=[('in.player', 'identity.player'), ('identity.key', 'valid.a'), ('in.run', 'human.in'),
           ('valid.result', 'human.condition'), ('human.false', 'earned.run'),
           ('identity.key', 'balance.key'), ('in.amount', 'positive.a'), ('positive.value', 'reward_limit.a'),
           ('reward_limit.value', 'sum.a'),
           ('balance.value', 'sum.b'), ('sum.value', 'cap.a'), ('cap.value', 'difference.a'),
           ('balance.value', 'difference.b'), ('difference.value', 'earned.value'),
           ('earned.then', 'set.run'), ('identity.key', 'set.key'), ('cap.value', 'set.value'),
           ('set.then', 'feedback.in'), ('in.feedback', 'show.a'), ('earned_read.result', 'nonzero.a'),
           ('nonzero.result', 'show.b'), ('show.result', 'feedback.condition'),
           ('feedback.true', 'print.run'), ('in.player', 'print.p0'), ('message.result', 'print.p2'),
           ('earned_read.result', 'message.a'), ('balance.value', 'message.b')])


def points_group():
    nodes = [ev('round', 'round_start', 0, 0), N('map', 'HL_NodeMapStart', 0, 1),
             N('reset', 'HL_NodeResetKeyVariable', 1, 0, var_name='points', exclusive=True),
             variable('initial', 'points_starting', 2, 0, write=True),
             variable('enabled', 'points_enabled', 3, 0, write=True, kind='bool', sockets={'value': True}),
             N('starting_min','HL_NodeMath',1,2,op='MAXIMUM',sockets={'b':0}),
             N('starting_max','HL_NodeMath',2,2,op='MINIMUM',sockets={'b':1000000})]
    links = [('round.happened', 'reset.run'), ('map.start', 'reset.run'), ('reset.then', 'initial.run'),
             ('in.starting', 'starting_min.a'), ('starting_min.value','starting_max.a'),
             ('starting_max.value', 'initial.value'), ('initial.then', 'enabled.run')]
    for nid, event, player, reward, row in [('common', 'infected_death', 'attacker', 'common', 3),
                                            ('witch', 'witch_killed', 'userid', 'witch', 6)]:
        nodes += [ev(nid, event, 0, row), N(nid+'_award', 'HL_NodeGroup', 2, row, group='Points: Award')]
        links += [(nid+'.happened', nid+'_award.run'), (nid+'.'+player, nid+'_award.player'),
                  ('in.'+reward, nid+'_award.amount'), ('in.feedback', nid+'_award.feedback')]
    nodes += [ev('death', 'player_death', 0, 9), fn('type', 'player:GetZombieType', 0, 11),
              fn('survivor', 'player:IsSurvivor', 0, 12), N('team', 'HL_NodeIf', 1, 9),
              N('tank', 'HL_NodeCompare', 1, 11, op='EQUAL', sockets={'b': 8}),
              N('is_tank', 'HL_NodeIf', 2, 9),
              N('lower', 'HL_NodeCompare', 2, 11, op='GREATER_EQUAL', sockets={'b': 1}),
              N('upper', 'HL_NodeCompare', 2, 12, op='LESS_EQUAL', sockets={'b': 6}),
              N('both', 'HL_NodeBoolMath', 3, 11, op='AND'), N('special', 'HL_NodeIf', 3, 10),
              N('tank_award', 'HL_NodeGroup', 4, 9, group='Points: Award'),
              N('special_award', 'HL_NodeGroup', 4, 12, group='Points: Award'),
              ev('chat', 'player_say', 0, 16), N('command', 'HL_NodeTextCheck', 1, 17, op='EQUAL', ignore_case=True, sockets={'b': '!points'}),
              N('chat_if', 'HL_NodeIf', 1, 16), N('chat_identity', 'HL_NodeHumanIdentity', 2, 17),
              N('chat_valid', 'HL_NodeTextCheck', 3, 17, op='EQUAL', sockets={'b': ''}),
              N('chat_human', 'HL_NodeIf', 3, 16), N('chat_balance', 'HL_NodeGroup', 4, 17, group='Points: Balance'),
              N('chat_text', 'HL_NodeFormatText', 5, 17, template='Points: {a}'), fn('chat_print', 'ClientPrint', 5, 16, {'p1': 3})]
    nodes += [N('victim_valid','HL_NodeCheck',-1,11), N('victim','HL_NodeIf',-1,9)]
    links += [('death.userid','victim_valid.a'), ('victim_valid.result','victim.condition'),
              ('death.happened','victim.in'), ('victim.true','team.in')]
    links += [('death.userid', 'type.target'), ('death.userid', 'survivor.target'),
              ('survivor.result', 'team.condition'), ('team.false', 'is_tank.in'), ('type.result', 'tank.a'),
              ('tank.result', 'is_tank.condition'), ('is_tank.true', 'tank_award.run'),
              ('is_tank.false', 'special.in'), ('type.result', 'lower.a'), ('type.result', 'upper.a'),
              ('lower.result', 'both.a'), ('upper.result', 'both.b'), ('both.result', 'special.condition'),
              ('special.true', 'special_award.run'), ('death.attacker', 'tank_award.player'),
              ('death.attacker', 'special_award.player'), ('in.tank', 'tank_award.amount'),
              ('in.special', 'special_award.amount'), ('in.feedback', 'tank_award.feedback'),
              ('in.feedback', 'special_award.feedback'), ('chat.happened', 'chat_if.in'),
              ('chat.text', 'command.a'), ('command.result', 'chat_if.condition'),
              ('chat_if.true', 'chat_human.in'), ('chat.userid', 'chat_identity.player'),
              ('chat_identity.key', 'chat_valid.a'), ('chat_valid.result', 'chat_human.condition'),
              ('chat_human.false', 'chat_print.run'), ('chat.userid', 'chat_print.p0'),
              ('chat_identity.key', 'chat_balance.key'), ('chat_balance.value', 'chat_text.a'),
              ('chat_text.result', 'chat_print.p2')]
    return dict(about='Personal points built from game events, variables, arithmetic and messages. Tab into Award and Balance to edit them. One controller per map.',
                inputs=[(key, title, 'bool' if key=='feedback' else 'num') for key,title in
                        [('starting','Starting Points'),('common','Common Kill'),('special','Special Kill'),
                         ('tank','Tank Kill'),('witch','Witch Kill'),('feedback','Show Earned Points')]],
                outputs=[], nodes=nodes, links=links,
                steps=[('Reset at round start', [n['id'] for n in nodes[:5]]),
                       ('Common rewards', ['common','common_award']), ('Witch rewards', ['witch','witch_award']),
                       ('Special and Tank rewards', [n['id'] for n in nodes if 9 <= n['pos'][1] <= 12]),
                       ('Private !points command', [n['id'] for n in nodes if n['pos'][1] >= 16])])


def purchase_group():
    nodes = [N('target', 'HL_NodeInteraction', 0, 0), N('balance', 'HL_NodeGroup', 1, 4, group='Points: Balance'),
             variable('enabled', 'points_enabled', 0, 5, kind='bool'), N('available', 'HL_NodeIf', 1, 0),
             N('has', 'HL_NodeHasInventory', 2, 4), N('owned', 'HL_NodeIf', 2, 0),
             N('enough', 'HL_NodeCompare', 3, 4, op='GREATER_EQUAL'), N('funds', 'HL_NodeIf', 3, 0),
             N('subtract', 'HL_NodeMath', 4, 4, integer=True, op='SUBTRACT'),
             variable('reserve', 'points', 4, 0, write=True, scope='KEY'),
             N('give', 'HL_NodeGiveInventory', 5, 0),
             variable('refund', 'points', 6, 2, write=True, scope='KEY', op='ADD')]
    links = [('in.entity','target.entity'), ('in.range','target.range'), ('target.key','balance.key'),
             ('target.used','available.in'), ('enabled.result','available.condition'),
             ('available.true','owned.in'), ('target.player','has.player'), ('in.item','has.item'),
             ('has.result','owned.condition'), ('owned.false','funds.in'), ('balance.value','enough.a'),
             ('in.price','enough.b'), ('enough.result','funds.condition'), ('funds.true','reserve.run'),
             ('balance.value','subtract.a'), ('in.price','subtract.b'), ('subtract.value','reserve.value'),
             ('target.key','reserve.key'), ('reserve.then','give.run'), ('target.player','give.player'),
             ('in.item','give.item'), ('give.failure','refund.run'), ('target.key','refund.key'), ('in.price','refund.value')]
    for nid, event, template, col, row in [
            ('prompt','target.focused','{a} - {b} points | You: {c} | Press USE to buy', 1, 7),
            ('unavailable','available.false','Purchases unavailable: add Personal Kill Points to this map', 2, 9),
            ('already','owned.true','Already carrying {a}', 3, 7),
            ('poor','funds.false','Not enough points: {b} required | You have {c}', 4, 9),
            ('bought','give.success','Bought {a} | Points: {c}', 6, 7),
            ('failed','refund.then','Could not give item; points refunded', 7, 9)]:
        nodes += [N(nid+'_text','HL_NodeFormatText',col,row+1,template=template),
                  fn(nid,'ClientPrint',col,row,{'p1':4})]
        links += [('target.player',nid+'.p0'), (nid+'_text.result',nid+'.p2'),
                  ('in.title',nid+'_text.a'), ('in.price',nid+'_text.b'), ('balance.value',nid+'_text.c')]
        if nid in ('bought','failed'):
            nodes += [N(nid+'_valid','HL_NodeCheck',col,row+2), N(nid+'_if','HL_NodeIf',col-1,row)]
            links += [(event,nid+'_if.in'), ('target.player',nid+'_valid.a'),
                      (nid+'_valid.result',nid+'_if.condition'), (nid+'_if.true',nid+'.run')]
        else:
            links.append((event,nid+'.run'))
    return dict(about='All purchase rules are here: prompt, controller check, inventory check, funds check, reserve, deliver, refund. Look / Use Target and Give Inventory Item are reusable independently of points.',
                inputs=[('entity','Display','thing'),('item','Item','any'),('title','Display Name','text'),
                        ('price','Price','num'),('range','Use Range','num')], outputs=[],nodes=nodes,links=links,
                steps=[('Check, reserve, deliver or refund', [n['id'] for n in nodes[:12]]),
                       ('Private messages', [n['id'] for n in nodes[12:]])])


def station(item='first_aid_kit', price=250):
    choices = 'AUTO=Auto from Display Model; ' + '; '.join(k+'='+v['title'] for k,v in sorted(ITEMS.items()))
    return dict(behaviour=True, about='Each object has its own exposed settings. Tab into Purchase to edit the rules; Item is reusable for grants and inventory checks.',
        nodes=[N('self','HL_NodeSelf',0,0),
               N('item_setting','HL_NodeExposedSetting',0,2,ui_name='Item',value_type='ENUM',choices=choices,default_text=item),
               N('price_setting','HL_NodeExposedSetting',0,3,ui_name='Price',value_type='INT',default_integer=price),
               N('title_setting','HL_NodeExposedSetting',0,4,ui_name='Display Name',value_type='TEXT',default_text=''),
               N('range_setting','HL_NodeExposedSetting',0,5,ui_name='Use Range',value_type='INT',default_integer=96),
               N('item','HL_NodeInventoryItem',1,0,item=item),
               N('named','HL_NodeTextCheck',1,4,op='EQUAL',sockets={'b':''}),
               N('title','HL_NodePickValue',2,4,value_kind='text'),
               N('nonnegative','HL_NodeMath',1,3, integer=True,op='MAXIMUM',sockets={'b':0}),
               N('price','HL_NodeMath',2,3, integer=True,op='MINIMUM',sockets={'b':1000000}),
               N('near','HL_NodeMath',1,6, integer=True,op='MAXIMUM',sockets={'b':32}),
               N('range','HL_NodeMath',2,6, integer=True,op='MINIMUM',sockets={'b':256}),
               N('buy','HL_NodeGroup',3,0,group='Purchase: Rules')],
        links=[('self.object','item.object'),('item_setting.value','item.item'),('self.entity','buy.entity'),
               ('item.value','buy.item'),('title_setting.value','named.a'),('named.result','title.condition'),
               ('item.title','title.a'),('title_setting.value','title.b'),('title.value','buy.title'),
               ('price_setting.value','nonnegative.a'),('nonnegative.value','price.a'),('price.value','buy.price'),
               ('range_setting.value','near.a'),('near.value','range.a'),('range.value','buy.range')])


def examples():
    result = [dict(title='Personal Kill Points', level='Game Modes',
        about='Personal points for each human survivor. Rewards, balance storage, round reset and !points chat are editable node groups. Tab into Personal Points, Award or Balance to see every rule. Add one controller per map. Steam identities retain their balance through reconnects in the same round; bots do not earn points.',
        groups={'Points: Balance':BALANCE,'Points: Award':AWARD,'Personal Points':points_group()},
        nodes=[N('points','HL_NodeGroup',0,0,group='Personal Points',
                 sockets=dict(starting=0,common=10,special=50,tank=500,witch=250,feedback=True))], links=[])]
    for title, item, price, name, key, height in [
            ('Wall Weapon - Uzi','smg',500,'Wall Weapon: Uzi','wall_gun',1.4),
            ('Wall Weapon - M16','rifle',1000,'Wall Weapon: M16','wall_gun',1.4),
            ('Wall Weapon - Pump Shotgun','pumpshotgun',750,'Wall Weapon: Pump Shotgun','wall_gun',1.4),
            ('Purchase - Any Weapon or Item','first_aid_kit',250,'Purchase: Item','purchase_display',1.2)]:
        result.append(dict(title=title,level='Game Modes',
            about='An editable purchase behaviour with independent settings per object. Choose Item, Price, Display Name and Use Range in Selected Object > Behaviours. Tab into Purchase: Rules to edit checks, deductions, delivery, refunds and messages. Add Personal Kill Points once per map.',
            objects={key:('ENTITY:prop_dynamic_override',(0,0,height),
                          {'model':ITEMS[item]['model'],'solid':'0','disableshadows':'1'})},
            behaviours={key:name},
            groups={'Points: Balance':BALANCE,'Purchase: Rules':purchase_group(),
                    name:station(item if key=='purchase_display' else 'AUTO',price)},
            nodes=[],links=[]))
    return result
