"""Exact shared snapshots for explicitly cached nearby-nav and ambient queries."""
import hashlib
from pathlib import Path
from . import vscript as vs, worldqueries

KINDS=("QC_NEARBY","QC_AMBIENT","QC_INVALIDATE")
FIELDS=dict(worldqueries.FIELDS,cache_hit="false",refreshed="false",age="0.0",error='""',removed="0")

def definition(c,n):
    capacity=n.settings.get("capacity",64)
    if isinstance(capacity,bool) or not isinstance(capacity,int) or not 1<=capacity<=128:
        raise ValueError("Query Cache Pool capacity must be 1–128")
    identity="cache_"+hashlib.sha256(c.key_of(n.id).encode('utf-8')).hexdigest()
    return '{id='+vs.literal('text',identity)+',capacity='+str(capacity)+'}'

def helpers(c):
    c.helper('HLQC',Path(__file__).with_name('query_cache_runtime.nut').read_text(encoding='utf-8'))
    if not c.ir.__dict__.get('_query_cache_reset'):
        c.ir._query_cache_reset=True
        for event in ('round_start','round_end','mission_lost','map_transition'):
            c.ir.logic_script_events.setdefault(event,[]).append('    if ("HLQC" in getroottable()) HLQC.Clear();')

def call(c,n):
    helpers(c)
    v=lambda name,kind:c.typed_in(n,name,kind)
    pool=v('pool','any')
    if n.kind=='QC_INVALIDATE':return f'HLQC.Invalidate({pool})'
    if n.kind=='QC_AMBIENT':
        c.ir._world_query_ambient=True
        c.helper('HLWQ_Ambient',Path(__file__).with_name('ambient_query_runtime.nut').read_text(encoding='utf-8'))
        return f"HLQC.ReadAmbient({pool}, {v('position','vec')}, {v('max_age','num')}, {v('refresh','bool')})"
    if n.kind=='QC_NEARBY':
        c.helper('HLWQ',Path(__file__).with_name('world_queries_runtime.nut').read_text(encoding='utf-8'))
        return f"HLQC.ReadNearby({pool}, {v('position','vec')}, {v('radius','num')}, {v('limit','num')}, {v('max_age','num')}, {v('refresh','bool')})"
    raise ValueError('Unknown cached query')

def output(n,socket,table):
    allowed={'removed','error'} if n.kind=='QC_INVALIDATE' else set(FIELDS)-{'removed'}
    if socket not in allowed:raise ValueError('Unknown cached query output')
    return table+'.'+socket

def stored_output(n,socket,key):
    value=output(n,socket,'::HL_R["'+key+'"]')
    return f'(("{key}" in ::HL_R) ? {value} : {FIELDS[socket]})'
