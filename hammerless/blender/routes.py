"""Authored waypoint routes and an editable commons traversal action."""
import bpy
from .logic import _Block
from .actiontemplates import _Builder

class HL_RouteSocket(bpy.types.NodeSocket):
    bl_idname, bl_label = 'HL_RouteSocket', 'Waypoint Route'
    def draw(self, context, layout, node, text): layout.label(text=text)
    def draw_color(self, context, node): return (.55,.65,.22,1)
    @classmethod
    def draw_color_simple(cls): return (.55,.65,.22,1)

class HL_RouteData(_Block, bpy.types.Node):
    bl_idname, bl_label, kind = 'HL_RouteData','Authored Route','ROUTE_DATA'
    bl_description = 'Ordered Empty positions baked at export. Choose a collection; direct Empty children need distinct integer Route Order values. Geometry is ignored. At most 256 points. Does not create a nav path or ensure reachability'
    collection: bpy.props.PointerProperty(type=bpy.types.Collection, name='Route Collection')
    def make_sockets(self): self.data_out('route','route','Route')
    def draw_buttons(self, context, layout):
        layout.prop(self,'collection',text='')
        op=layout.operator('hammerless.route_point',text='Add Point at Cursor');op.node_name=self.name
        layout.label(text='Select a point to change Route Order')
    def to_lnode(self, context):
        n=super().to_lnode(context)
        points=[]
        if self.collection:
            deps=context.evaluated_depsgraph_get()
            for obj in self.collection.objects:
                if obj.type!='EMPTY':continue
                order=obj.get('hl_route_order')
                if isinstance(order,bool) or not isinstance(order,int):
                    n.settings['error']='Route point '+obj.name+' needs an integer Route Order';return n
                if obj.name not in context.scene.objects:
                    n.settings['error']='Route point '+obj.name+' is not in the exported scene';return n
                pos=tuple(float(x)*context.scene.hammerless.units_per_meter for x in obj.evaluated_get(deps).matrix_world.translation)
                points.append((order,pos))
        if len({p[0] for p in points})!=len(points):
            n.settings['error']='Route points must have distinct Route Order values'
        n.settings['points']=[p[1] for p in sorted(points,key=lambda p:p[0])]
        return n

class _Query(_Block):
    def begin(self):
        self.ev_in('run','Run');self.ev_out('next','Next');self.data_in('route','route','Route')
        self.data_out('bool','found','Found');self.data_out('text','error','Error')
        self.data_out('num','count','Point Count');self.data_out('num','index','Index')
        self.data_out('vec','position','Position')

class HL_RoutePoint(_Query,bpy.types.Node):
    bl_idname,bl_label,kind='HL_RoutePoint','Route Point','ROUTE_POINT'
    bl_description='Capture the point at a zero-based index. Found is false for a missing route or invalid index; inspect Error. Position is in Hammer units'
    def make_sockets(self):self.begin();self.data_in('num','index','Index (starts at 0)')

class HL_RouteNearest(_Query,bpy.types.Node):
    bl_idname,bl_label,kind='HL_RouteNearest','Closest Route Point','ROUTE_NEAREST'
    bl_description='Find the closest authored point by straight-line distance, not route distance or reachability. Ties choose the earliest point. Use Index to join a route midway'
    def make_sockets(self):
        self.begin();self.data_in('vec','origin','From Position');self.data_out('num','distance','Distance')

MODES=[('ONCE','Once','Finish after arriving at the endpoint'),('LOOP','Loop','Wrap to the other endpoint'),('PINGPONG','Back and Forth','Reverse at each endpoint without repeating it')]
class HL_RouteAdvance(_Query,bpy.types.Node):
    bl_idname,bl_label,kind='HL_RouteAdvance','Advance Route Index','ROUTE_ADVANCE'
    bl_description='Stateless cursor step: call after arriving at Current Index. Positive direction is forward; negative is reverse. Save both resulting Index and Direction yourself. Once completes at the endpoint; a one-point looping route stays on that point'
    mode:bpy.props.EnumProperty(items=MODES,default='ONCE')
    def make_sockets(self):
        self.begin();self.data_in('num','index','Current Index');self.data_in('num','direction','Direction').value=1
        self.data_out('num','direction','Direction');self.data_out('bool','completed','Completed')
    def draw_buttons(self,context,layout):layout.prop(self,'mode',text='')
    def settings(self):return {'mode':self.mode}

class HL_OT_route_point(bpy.types.Operator):
    bl_idname,bl_label='hammerless.route_point','Add Route Point'
    bl_description='Add an Empty at the scene cursor with the next Route Order; creates and assigns a route collection if needed'
    bl_options={'REGISTER','UNDO'}
    node_name:bpy.props.StringProperty()
    def execute(self,context):
        tree=getattr(context.space_data,'edit_tree',None)
        node=tree.nodes.get(self.node_name) if tree else None
        if not node or node.bl_idname!='HL_RouteData':return {'CANCELLED'}
        col=node.collection
        if col is None:
            col=bpy.data.collections.new('Waypoint Route');context.scene.collection.children.link(col);node.collection=col
        elif col not in [context.scene.collection, *context.scene.collection.children_recursive]:
            self.report({'WARNING'},'Choose a route collection linked to this scene')
            return {'CANCELLED'}
        orders=[o.get('hl_route_order') for o in col.objects if o.type=='EMPTY' and isinstance(o.get('hl_route_order'),int)]
        obj=bpy.data.objects.new('Route Point',None);col.objects.link(obj)
        obj.location=context.scene.cursor.location;obj.empty_display_type='SPHERE';obj.empty_display_size=.15
        obj['hl_route_order']=max(orders,default=-1)+1
        obj.id_properties_ui('hl_route_order').update(description='Ascending route order; must be unique within the route collection')
        context.view_layer.update()
        if obj.name in context.view_layer.objects:
            for selected in context.selected_objects:selected.select_set(False)
            obj.select_set(True);context.view_layer.objects.active=obj
        return {'FINISHED'}

class HL_PT_route_point(bpy.types.Panel):
    bl_label='Waypoint Route Point';bl_idname='HL_PT_route_point';bl_space_type='PROPERTIES';bl_region_type='WINDOW';bl_context='object'
    @classmethod
    def poll(cls,context):return context.object is not None and 'hl_route_order' in context.object
    def draw(self,context):self.layout.prop(context.object,'["hl_route_order"]',text='Route Order')

PARAMETERS=(('route','Route','route',None),('index','Initial Index','num',0),('direction','Initial Direction','num',1),
            ('nearest','Join at Closest Point','bool',False),('radius','Arrival Radius','num',32),('stall','Stall After (seconds)','num',5),('timeout','Point Timeout (seconds)','num',30))

def follow_route(mode='ONCE'):
    b=_Builder('Follow Route (experimental)',PARAMETERS,'Movement','COMMONS')
    def read(name,kind='num'):
        return b.node('HL_BAStateGet',name,state_name=name,value_type=kind)
    def save(name,kind='num'):
        return b.node('HL_BAStateSet',name,state_name=name,value_type=kind)
    def test(n,port,label):
        t=b.node('HL_NodeIf',label);b.wire(n,port,t,'condition');return t
    start_index=save('route_index');b.wire(b.params,'index',start_index,'value');b.wire(b.entry,'start',start_index,'run')
    start_direction=save('route_direction');b.wire(b.params,'direction',start_direction,'value');b.wire(start_index,'next',start_direction,'run')
    join=test(b.params,'nearest','Join at nearest authored point?');b.wire(start_direction,'next',join,'in')
    origin=b.node('HL_NodePlayerView','Bot center (not feet)');b.wire(b.entry,'bot',origin,'player')
    nearest=b.node('HL_RouteNearest','Closest point (straight line)');b.wire(b.params,'route',nearest,'route');b.wire(origin,'eye',nearest,'origin');b.wire(join,'true',nearest,'run')
    join_index=save('route_index');b.wire(nearest,'index',join_index,'value');b.wire(nearest,'next',join_index,'run')
    point=b.node('HL_RoutePoint','Current destination');b.wire(b.params,'route',point,'route');b.wire(read('route_index'),'value',point,'index')
    for n,p in ((join,'false'),(join_index,'next'),(b.entry,'update')):b.wire(n,p,point,'run')
    valid=test(point,'found','Valid destination?');b.wire(point,'next',valid,'in')
    failed=b.node('HL_BAResult','Route or movement failed',result='FAILED');b.wire(valid,'false',failed,'run')
    observe=b.node('HL_BAMovement','Measure progress toward this point')
    b.wire(point,'position',observe,'goal')
    for a,z in (('radius','radius'),('stall','stall_seconds'),('timeout','timeout')):b.wire(b.params,a,observe,z)
    b.wire(read('route_reset','bool'),'value',observe,'reset');b.wire(valid,'true',observe,'run')
    clear=save('route_reset','bool');b.value(clear,'value',False);b.wire(observe,'next',clear,'run')
    arrived=test(observe,'arrived','Point reached?');b.wire(clear,'next',arrived,'in')
    advance=b.node('HL_RouteAdvance','Choose next route point',mode=mode)
    b.wire(b.params,'route',advance,'route');b.wire(read('route_index'),'value',advance,'index');b.wire(read('route_direction'),'value',advance,'direction');b.wire(arrived,'true',advance,'run')
    advanced=test(advance,'found','Cursor valid?');b.wire(advance,'next',advanced,'in');b.wire(advanced,'false',failed,'run')
    complete=test(advance,'completed','Route finished?');b.wire(advanced,'true',complete,'in')
    success=b.node('HL_BAResult','Route completed',result='SUCCESS');b.wire(complete,'true',success,'run')
    set_index=save('route_index');b.wire(advance,'index',set_index,'value');b.wire(complete,'false',set_index,'run')
    set_direction=save('route_direction');b.wire(advance,'direction',set_direction,'value');b.wire(set_index,'next',set_direction,'run')
    reset=save('route_reset','bool');b.value(reset,'value',True);b.wire(set_direction,'next',reset,'run')
    # Continue on the next update, bounding traversal even for coincident points.
    stalled=b.node('HL_NodeBoolMath','Stalled or timed out',op='OR');b.wire(observe,'stalled',stalled,'a');b.wire(observe,'timed_out',stalled,'b')
    bad=test(stalled,'result','Unable to reach this point?');b.wire(arrived,'false',bad,'in');b.wire(bad,'true',failed,'run')
    cooldown=b.node('HL_BACooldown','Refresh command at most twice a second');b.value(cooldown,'seconds',.5);b.wire(bad,'false',cooldown,'run')
    command=b.node('HL_BACommand','Move to current point',command='MOVE');b.wire(point,'position',command,'destination');b.wire(cooldown,'passed',command,'run')
    owned=save('movement_owned','bool');b.value(owned,'value',True);b.wire(command,'next',owned,'run')
    cleanup=test(read('movement_owned','bool'),'value','Release our movement command');b.wire(b.entry,'stop',cleanup,'in')
    release=b.node('HL_BACommand','Return control to native AI',command='RESET');b.wire(cleanup,'true',release,'run')
    return b.tree

class HL_OT_follow_route(bpy.types.Operator):
    bl_idname,bl_label='hammerless.follow_route','Add Follow Route (experimental)'
    bl_description='Create an editable commons action using public route, state, movement, and command nodes. Native AI may interrupt it. Closest join uses bot center and straight-line distance. Connect an Authored Route; Initial Index resumes a cursor only, not a previous action instance'
    bl_options={'REGISTER','UNDO'}
    mode:bpy.props.EnumProperty(items=MODES,default='ONCE')
    @classmethod
    def poll(cls,context):
        tree=getattr(getattr(context,'space_data',None),'edit_tree',None)
        return tree is not None and tree.bl_idname=='HL_LogicTree' and not tree.is_group
    def execute(self,context):
        tree=context.space_data.edit_tree;n=tree.nodes.new('HL_BAAction');n.group=follow_route(self.mode)
        for key,label,kind,default in PARAMETERS:
            if default is not None:_Builder.socket(n,key).value=default
        n.location=context.space_data.cursor_location;tree.nodes.active=n
        return {'FINISHED'}

NODES=(HL_RouteData,HL_RoutePoint,HL_RouteNearest,HL_RouteAdvance)
class HL_MT_routes(bpy.types.Menu):
    bl_idname,bl_label='HL_MT_routes','Waypoint Routes'
    def draw(self,context):
        for cls in NODES:
            op=self.layout.operator('node.add_node',text=cls.bl_label);op.type=cls.bl_idname;op.use_transform=True
        if not context.space_data.edit_tree.is_group:
            self.layout.separator()
            for mode,label,_ in MODES:self.layout.operator('hammerless.follow_route',text='Follow Route: '+label+' (experimental)').mode=mode
CLASSES=(HL_RouteSocket,)+NODES+(HL_OT_route_point,HL_PT_route_point,HL_OT_follow_route,HL_MT_routes)
def register():
    from . import logic
    logic.SOCKET_OF_KIND['route']='HL_RouteSocket';logic.DATA_SOCKETS+=('HL_RouteSocket',)
    for cls in CLASSES:bpy.utils.register_class(cls)
def unregister():
    from . import logic
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
    logic.SOCKET_OF_KIND.pop('route',None);logic.DATA_SOCKETS=tuple(s for s in logic.DATA_SOCKETS if s!='HL_RouteSocket')

