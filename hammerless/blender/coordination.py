"""Bounded shared signals and action-owned destination reservations."""
import bpy
from .logic import _Block
from .botactions import _Inside

class _Signal(_Block):
    def begin(self):
        self.ev_in('run','Run');self.ev_out('next','Next')
        self.data_in('text','channel','Channel').value='alarm'

class HL_SignalPublish(_Signal,bpy.types.Node):
    bl_idname,bl_label,kind='HL_SignalPublish','Publish Signal','CO_SIGNAL_PUBLISH'
    bl_description='Share a position under a channel name until its lifetime expires. Replaces the previous signal on that channel; this is a latest-value message, not a queue. Lifetime must be positive and caps at 300 seconds. At most 128 active channels'
    def make_sockets(self):
        self.begin();self.data_in('vec','position','Position');self.data_in('num','ttl','Lifetime (seconds)').value=5
        self.data_out('bool','success','Published')

class HL_SignalRead(_Signal,bpy.types.Node):
    bl_idname,bl_label,kind='HL_SignalRead','Read Signal','CO_SIGNAL_READ'
    bl_description='Capture the latest unexpired signal on this channel. Check Available before using Position. Revision changes on every publish; connect it to Action Value Changed to detect new messages. Reading does not consume the signal'
    def make_sockets(self):
        self.begin()
        for kind,key,label in [('bool','available','Available'),('vec','position','Position'),('num','age','Age (seconds)'),('num','remaining','Remaining (seconds)'),('num','revision','Revision')]:self.data_out(kind,key,label)

class HL_SignalClear(_Signal,bpy.types.Node):
    bl_idname,bl_label,kind='HL_SignalClear','Clear Signal','CO_SIGNAL_CLEAR'
    bl_description='Remove the shared signal on this channel for every reader. Cleared is true only when an active signal was removed'
    def make_sockets(self):self.begin();self.data_out('bool','success','Cleared')

class HL_ClaimDestination(_Inside,bpy.types.Node):
    bl_idname,bl_label,kind='HL_ClaimDestination','Claim Destination','BA_CLAIM'
    bl_description='Reserve a named destination for this bot action. Acquired is false while another action owns the key. Repeat to renew your lease; use a stable unique key such as a nav area ID. Positive lifetime caps at 300 seconds. Released automatically when this action ends'
    def make_sockets(self):
        self.ev_in('run','Run');self.ev_out('next','Next')
        self.data_in('text','key','Destination Key').value='hiding_spot_1'
        self.data_in('num','ttl','Lease (seconds)').value=5
        self.data_out('bool','acquired','Acquired');self.data_out('num','remaining','Remaining (seconds)')

class HL_ReleaseDestination(_Inside,bpy.types.Node):
    bl_idname,bl_label,kind='HL_ReleaseDestination','Release Destination','BA_RELEASE_CLAIM'
    bl_description='Release this action\'s reservation for a destination key. Cannot release another bot or action\'s claim. Released is false if this action did not own an active claim'
    def make_sockets(self):
        self.ev_in('run','Run');self.ev_out('next','Next')
        self.data_in('text','key','Destination Key').value='hiding_spot_1'
        self.data_out('bool','released','Released')

SIGNALS=(HL_SignalPublish,HL_SignalRead,HL_SignalClear)
CLAIMS=(HL_ClaimDestination,HL_ReleaseDestination)
class HL_MT_coordination(bpy.types.Menu):
    bl_idname,bl_label='HL_MT_coordination','Coordination'
    def draw(self,context):
        classes=SIGNALS+(CLAIMS if getattr(context.space_data.edit_tree,'is_group',False) else ())
        for cls in classes:
            op=self.layout.operator('node.add_node',text=cls.bl_label);op.type=cls.bl_idname;op.use_transform=True
CLASSES=SIGNALS+CLAIMS+(HL_MT_coordination,)
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
def unregister():
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
