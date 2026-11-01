"""Explicit, shared caches for read-only engine observations."""
import bpy
from bpy.props import IntProperty
from .logic import _Block
from .botactions import _Wire
from .worldqueries import HL_WQNearby, HL_WQAmbient


class HL_QueryCacheSocket(_Wire, bpy.types.NodeSocket):
    bl_idname, bl_label = "HL_QueryCacheSocket", "Query Cache"


class HL_QueryCachePool(_Block, bpy.types.Node):
    """Share this descriptor between queries that should reuse identical observations. Capacity bounds stored query results, not engine work. Separate cache nodes are independent. Entries expire according to each caller's Max Age; Invalidate Query Cache clears this pool explicitly."""
    bl_idname, bl_label, kind = "HL_QueryCachePool", "Query Cache", "QC_POOL"
    capacity: IntProperty(name="Cached Queries", default=64, min=1, max=128,
                          description="Maximum results kept in this cache; least recently used entries are evicted")
    def make_sockets(self): self.data_out("query_cache", "pool", "Cache")
    def settings(self): return {"capacity":self.capacity}
    def draw_buttons(self, context, layout): layout.prop(self,"capacity")


def cache_sockets(node):
    node.data_in("query_cache", "pool", "Cache")
    node.data_in("num", "max_age", "Max Age (seconds)").value=0.25
    node.data_in("bool", "refresh", "Force Refresh").value=False
    node.data_out("bool", "cache_hit", "Cache Hit")
    node.data_out("bool", "refreshed", "Refreshed")
    node.data_out("num", "age", "Sample Age (seconds)")
    node.data_out("text", "error", "Cache Error")


class HL_CachedNearby(_Block, bpy.types.Node):
    """Reuse nearby-nav results only for exactly matching position, radius and result limit in the same cache. Age is time since sampling, not a guarantee of reachability or current blockers. Stale handles force a new query. Max Age=0 bypasses reuse; Force Refresh replaces this entry. Moving positions do not share unless you explicitly quantize them."""
    bl_idname, bl_label, kind = "HL_CachedNearby", "Cached Nearby Nav Areas", "QC_NEARBY"
    begin=HL_WQNearby.begin;found=HL_WQNearby.found;area_out=HL_WQNearby.area_out
    def make_sockets(self): HL_WQNearby.make_sockets(self);cache_sockets(self)


class HL_CachedAmbient(_Block, bpy.types.Node):
    """Reuse baked ambient lighting at exactly the same position. Available=false is missing data, not darkness. This excludes flashlights, dynamic lighting and exposure. Cache Error explains a failed lookup; no stale fallback is used. Max Age=0 forces a new sample."""
    bl_idname, bl_label, kind = "HL_CachedAmbient", "Cached Baked Ambient Light", "QC_AMBIENT"
    begin=HL_WQAmbient.begin
    def make_sockets(self): HL_WQAmbient.make_sockets(self);cache_sockets(self)


class HL_CandidateCachedAmbient(_Block, bpy.types.Node):
    """Read cached baked ambient lighting inside a Candidate Rule. Supply Cache through a rule input. Outputs share one lookup per candidate invocation. Check Available before using brightness. Force Refresh=true intentionally refreshes each evaluated candidate and defeats sharing. Sample Age describes this lighting sample, not the age of the whole scan."""
    bl_idname, bl_label, kind = "HL_CandidateCachedAmbient", "Sample Cached Baked Ambient Light", "CAND_CACHED_AMBIENT"
    @classmethod
    def poll(cls,tree):return tree is not None and tree.bl_idname=="HL_LogicTree" and bool(tree.get("hl_candidate_rule"))
    def make_sockets(self):
        self.data_in("vec","position","Position")
        self.data_out("bool","available","Available")
        self.data_out("num","brightness","Brightness")
        self.data_out("vec","color","Color")
        self.data_out("bool","fallback","Neighbor Sample")
        cache_sockets(self)


class HL_InvalidateQueryCache(_Block, bpy.types.Node):
    """Clear all stored observations in the connected cache immediately. Other queries in this same callback will miss and resample. Round/map lifecycle events also clear caches. This does not change engine navmesh or lighting data."""
    bl_idname, bl_label, kind = "HL_InvalidateQueryCache", "Invalidate Query Cache", "QC_INVALIDATE"
    def make_sockets(self):
        self.ev_in("run","Run");self.ev_out("next","Next")
        self.data_in("query_cache","pool","Cache")
        self.data_out("num","removed","Removed Entries")
        self.data_out("text","error","Cache Error")


NODES=(HL_QueryCachePool,HL_CachedNearby,HL_CachedAmbient,HL_CandidateCachedAmbient,HL_InvalidateQueryCache)
class HL_MT_querycache(bpy.types.Menu):
    bl_idname,bl_label="HL_MT_querycache","Query Caching"
    def draw(self,context):
        for cls in NODES:
            if cls is HL_CandidateCachedAmbient and not cls.poll(context.space_data.edit_tree):
                continue
            op=self.layout.operator("node.add_node",text=cls.bl_label)
            op.type=cls.bl_idname;op.use_transform=True
        self.layout.separator()
        self.layout.operator("hammerless.shared_shade_scan",text="Shared Shade Scan Example")


CLASSES=(HL_QueryCacheSocket,)+NODES+(HL_MT_querycache,)
def register():
    from . import logic, cacheexample
    logic.SOCKET_OF_KIND["query_cache"]="HL_QueryCacheSocket"
    logic.DATA_SOCKETS+=("HL_QueryCacheSocket",)
    for cls in CLASSES:bpy.utils.register_class(cls)
    cacheexample.register()

def unregister():
    from . import logic, cacheexample
    cacheexample.unregister()
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
    logic.SOCKET_OF_KIND.pop("query_cache",None)
    logic.DATA_SOCKETS=tuple(s for s in logic.DATA_SOCKETS if s!="HL_QueryCacheSocket")
