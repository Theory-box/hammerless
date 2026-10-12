import sys
sys.path.insert(0, r"C:\Users\John\Documents\Hammerless-combined2")
from hammerless.core.vpk import GameContent
GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Left 4 Dead 2"
_gc = None
def gc():
    global _gc
    if _gc is None: _gc = GameContent(GAME)
    return _gc
def which(path):
    path = path.lower()
    return [v.path for v in gc().vpks if path in v.entries]
