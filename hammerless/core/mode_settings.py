"""Map-scoped options, exported through Valve's scripted-mode MapOptions table.

The option definitions are shared with Director nodes. Values are opt-in: a
catalogue starting value is not a claim about the game's difficulty-dependent
default. Native Survival wave timing lives in server convars, not MapOptions.
"""
import math
import re

from .director_options import OPTIONS

# These require other base modes or a custom stage controller.
EXCLUDED = {
    'TankHitDamageModifierVersus', 'ZombieGhostDelayMin', 'ZombieGhostDelayMax',
    'GasCansOnBacks', 'cm_AutoSpawnInfectedGhosts', 'cm_SingleScavengeCluster',
    'ScriptedStageType', 'ScriptedStageValue', 'TotalSpecials', 'TotalSmokers',
    'TotalBoomers', 'TotalHunters', 'TotalSpitters', 'TotalJockeys', 'TotalChargers',
}
# Stock Survival owns spawn scheduling and population escalation. Expose shared
# survivor/gameplay options here rather than advertise ineffective wave controls.
SURVIVAL_KEYS = {
    'SurvivorMaxIncapacitatedCount', 'TempHealthDecayRate', 'WaterSlowsMovement',
    'ZombieTankHealth', 'NearAcquireRange', 'FarAcquireRange', 'FarAcquireTime',
    'MusicDynamicMobSpawnSize', 'MusicDynamicMobStopSize', 'MusicDynamicMobScanStopSize',
    'cm_AllowPillConversion', 'cm_AllowSurvivorRescue', 'cm_AutoReviveFromSpecialIncap',
    'cm_BaseCommonAttackDamage', 'cm_HeadshotOnly', 'cm_HealingGnome',
    'cm_InfiniteFuel', 'cm_NoSurvivorBots', 'cm_ShouldEscortHumanPlayers',
    'cm_ShouldHurry', 'cm_TempHealthOnly',
}


def options(mode):
    if mode not in ('CAMPAIGN', 'SURVIVAL'):
        raise ValueError(f'Unknown game mode: {mode}')
    return [o for o in OPTIONS if o[0] not in EXCLUDED
            and (mode == 'CAMPAIGN' or o[0] in SURVIVAL_KEYS)]


def title(key):
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', key.removeprefix('cm_'))


def option_lines(mode, values):
    """Validate and serialize only known, active-mode settings (no script text)."""
    definitions = {o[0]: o for o in options(mode)}
    lines = []
    for key, value in sorted(values.items()):
        if key not in definitions:
            raise ValueError(f'{key} is not a {mode} map setting')
        kind = definitions[key][1]
        if kind == 'bool':
            if not isinstance(value, bool):
                raise ValueError(f'{key} must be true or false')
            text = 'true' if value else 'false'
        else:
            numbers = tuple(value) if kind == 'vec' else (value,)
            if (kind == 'vec' and len(numbers) != 3) or any(
                    isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in numbers):
                raise ValueError(f'{key} requires finite numeric values')
            if kind == 'int' and int(value) != value:
                raise ValueError(f'{key} requires a whole number')
            text = ('Vector(' + ', '.join(format(n, '.9g') for n in numbers) + ')'
                    if kind == 'vec' else str(int(value)) if kind == 'int' else format(value, '.9g'))
        lines.append(f'    {key} = {text}')
    return lines
