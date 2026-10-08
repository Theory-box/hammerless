"""The AI Director's settings (DirectorOptions keys) and the game's override hooks, for logic nodes.

Setting names were checked against the game (left4dead2/bin/server.dll reads each one by name);
descriptions are Hammerless's own. Kinds: num (decimal), int (whole number), bool. The value given
is only what a new node starts with, not necessarily the game's default.

Hooks: the game asks the map's script before doing something (allow this damage? replace this
weapon spawn?). It only asks in scripted mode, which needs a game mode with its own script: maps
that use hooks or the custom HUD get Hammerless's own mode (base co-op), see gamefiles.
"""
from __future__ import annotations

# (key, kind, starting value, group, description)
OPTIONS = [
    # --- pacing (the build up / peak / relax cycle)
    ("LockTempo", "bool", True, "Pacing", "Never relax: mobs and specials keep coming without the usual quiet spells."),
    ("IntensityRelaxThreshold", "num", 0.9, "Pacing", "How stressed (0-1) every survivor must be before the Director relaxes."),
    ("IntensityRelaxAllowWanderersThreshold", "num", 0.5, "Pacing", "While relaxing, below this stress wanderers may be placed again."),
    ("RelaxMinInterval", "num", 30, "Pacing", "Shortest relax spell (seconds)."),
    ("RelaxMaxInterval", "num", 45, "Pacing", "Longest relax spell (seconds)."),
    ("RelaxMaxFlowTravel", "num", 3000, "Pacing", "A relax spell ends once the survivors have moved this far along the route."),
    ("SustainPeakMinTime", "num", 3, "Pacing", "Shortest time the Director keeps the pressure at its peak (seconds)."),
    ("SustainPeakMaxTime", "num", 5, "Pacing", "Longest time the Director keeps the pressure at its peak (seconds)."),
    ("DBBuildUpMinInterval", "num", 15, "Pacing", "Shortest build-up time before a peak (seconds)."),
    ("PausePanicWhenRelaxing", "bool", False, "Pacing", "Panic events wait while the Director is relaxing."),
    ("ZombieSpawnRange", "num", 1500, "Pacing", "How far from the survivors infected may spawn."),
    ("ZombieDiscardRange", "num", 2500, "Pacing", "Infected further than this from every survivor are removed."),
    ("ZombieSpawnInFog", "bool", False, "Pacing", "Infected may spawn in plain sight when fog hides them."),
    ("SpawnSetRule", "int", 0, "Pacing", "Where spawns are measured from (scripted mode): survivors, battlefield, finale or a position."),
    ("SpawnSetPosition", "vec", None, "Pacing", "The position used when the spawn rule is a position (a vector)."),
    ("SpawnSetRadius", "num", 2000, "Pacing", "The radius around that position."),
    ("SpawnDirectionMask", "int", 0, "Pacing", "Compass directions infected may come from (bits: SPAWNDIR_N, NE, ...)."),
    ("SpawnDirectionCount", "int", 0, "Pacing", "How many directions to pick from the mask."),
    ("PreferredMobDirection", "int", -1, "Pacing", "Where mobs come from: SPAWN_BEHIND_SURVIVORS, SPAWN_ABOVE_SURVIVORS, SPAWN_FAR_AWAY_FROM_SURVIVORS..."),
    ("PreferredMobPosition", "vec", None, "Pacing", "A position mobs should come from (a vector)."),
    ("PreferredMobPositionRange", "num", 1000, "Pacing", "How close to that position mobs spawn."),
    ("PreferredSpecialDirection", "int", -1, "Pacing", "Where specials come from (same choices as mobs)."),
    ("ShouldIgnoreClearStateForSpawn", "bool", False, "Pacing", "Spawn in areas the survivors have already cleared."),
    ("EnforceFinaleNavSpawnRules", "bool", False, "Pacing", "Keep to finale spawn areas outside finales too."),
    ("IgnoreNavThreatAreas", "bool", False, "Pacing", "Ignore THREAT nav areas when choosing spawns."),
    ("AllowCrescendoEvents", "bool", True, "Pacing", "Crescendo (panic) events may run."),
    # --- mobs
    ("CommonLimit", "int", 30, "Mobs", "Most common infected alive at once."),
    ("MobMinSize", "int", 10, "Mobs", "Smallest mob."),
    ("MobMaxSize", "int", 30, "Mobs", "Largest mob."),
    ("MobSpawnSize", "int", 20, "Mobs", "Size of the next mob (overrides min/max)."),
    ("MegaMobSize", "int", 50, "Mobs", "Size of panic-event (mega) mobs."),
    ("MobMaxPending", "int", 30, "Mobs", "Most infected waiting to spawn for a mob."),
    ("MobSpawnMinTime", "num", 90, "Mobs", "Shortest wait between mobs (seconds)."),
    ("MobSpawnMaxTime", "num", 180, "Mobs", "Longest wait between mobs (seconds)."),
    ("MobRechargeRate", "num", 0.0025, "Mobs", "How fast the next mob grows while waiting."),
    ("NoMobSpawns", "bool", True, "Mobs", "No mobs at all (wanderers still appear)."),
    ("BileMobSize", "int", 20, "Mobs", "How many commons a bile jar or boomer vomit brings."),
    ("PanicForever", "bool", True, "Mobs", "A panic event never stops."),
    ("PanicWavePauseMin", "num", 5, "Mobs", "Shortest pause between panic waves (seconds)."),
    ("PanicWavePauseMax", "num", 15, "Mobs", "Longest pause between panic waves (seconds)."),
    ("PanicSpecialsOnly", "bool", True, "Mobs", "Panic events send specials only, no commons."),
    ("HordeEscapeCommonLimit", "int", 15, "Mobs", "Most commons during the finale escape."),
    ("InfectedFlags", "int", 0, "Mobs", "Flags given to new commons (INFECTED_FLAG_CANT_SEE_SURVIVORS, ...)."),
    # --- wanderers
    ("WanderingZombieDensityModifier", "num", 1.0, "Wanderers", "How many wandering commons are placed (0 = none, 1 = normal)."),
    ("AlwaysAllowWanderers", "bool", True, "Wanderers", "Place wanderers even while the Director builds up or peaks."),
    ("NumReservedWanderers", "int", 0, "Wanderers", "Commons kept for wanderers that mobs can't use."),
    ("ClearedWandererRespawnChance", "num", 0, "Wanderers", "Chance (0-100) wanderers come back to cleared areas."),
    ("ZombieDontClear", "bool", True, "Wanderers", "Wanderers don't despawn when out of range."),
    # --- specials
    ("MaxSpecials", "int", 4, "Specials", "Most special infected alive at once."),
    ("DominatorLimit", "int", 4, "Specials", "Most pinning specials (hunter, smoker, jockey, charger) at once."),
    ("SmokerLimit", "int", 1, "Specials", "Most smokers at once."),
    ("BoomerLimit", "int", 1, "Specials", "Most boomers at once."),
    ("HunterLimit", "int", 1, "Specials", "Most hunters at once."),
    ("SpitterLimit", "int", 1, "Specials", "Most spitters at once."),
    ("JockeyLimit", "int", 1, "Specials", "Most jockeys at once."),
    ("ChargerLimit", "int", 1, "Specials", "Most chargers at once."),
    ("TotalSpecials", "int", 10, "Specials", "Specials allowed in all (scripted stages)."),
    ("TotalSmokers", "int", 2, "Specials", "Smokers allowed in all."),
    ("TotalBoomers", "int", 2, "Specials", "Boomers allowed in all."),
    ("TotalHunters", "int", 2, "Specials", "Hunters allowed in all."),
    ("TotalSpitters", "int", 2, "Specials", "Spitters allowed in all."),
    ("TotalJockeys", "int", 2, "Specials", "Jockeys allowed in all."),
    ("TotalChargers", "int", 2, "Specials", "Chargers allowed in all."),
    ("SpecialRespawnInterval", "num", 45, "Specials", "Seconds before a killed special's slot is filled again."),
    ("SpecialInitialSpawnDelayMin", "num", 0, "Specials", "Shortest wait for the first specials (seconds)."),
    ("SpecialInitialSpawnDelayMax", "num", 60, "Specials", "Longest wait for the first specials (seconds)."),
    ("SpecialInfectedAssault", "bool", True, "Specials", "Specials attack right away instead of waiting for an opening."),
    ("ShouldAllowSpecialsWithTank", "bool", True, "Specials", "Specials may spawn while a Tank is in play."),
    ("NearAcquireRange", "num", 200, "Specials", "Infected notice survivors this close at once."),
    ("FarAcquireRange", "num", 2500, "Specials", "Infected may notice survivors this far away."),
    ("FarAcquireTime", "num", 5, "Specials", "How long infected take to notice survivors far away (seconds)."),
    # --- tank and witch
    ("TankLimit", "int", 1, "Tank & Witch", "Most Tanks at once (0 = the Director spawns none)."),
    ("WitchLimit", "int", 1, "Tank & Witch", "Most Witches at once."),
    ("ProhibitBosses", "bool", True, "Tank & Witch", "The Director spawns no Tanks or Witches."),
    ("ShouldAllowMobsWithTank", "bool", True, "Tank & Witch", "Mobs may come while a Tank is in play."),
    ("ZombieTankHealth", "int", 4000, "Tank & Witch", "A Tank's health."),
    ("TankHitDamageModifierVersus", "num", 1.0, "Tank & Witch", "Tank punch damage multiplier in versus."),
    ("TankRunSpawnDelay", "num", 15, "Tank & Witch", "Seconds between Tanks in Tank-run style modes."),
    ("CustomTankKiteDistance", "num", 3000, "Tank & Witch", "How far survivors may run from a Tank before it gets frustrated faster."),
    ("PreTankMobMax", "int", 20, "Tank & Witch", "Most commons in the mob before a finale Tank."),
    ("EscapeSpawnTanks", "bool", True, "Tank & Witch", "Tanks spawn during the finale escape."),
    ("AllowWitchesInCheckpoints", "bool", False, "Tank & Witch", "Witches may be placed in safe rooms."),
    # --- survivors
    ("SurvivorMaxIncapacitatedCount", "int", 2, "Survivors", "Times a survivor can go down before the next is fatal."),
    ("TempHealthDecayRate", "num", 0.27, "Survivors", "How fast temporary health drains (per second)."),
    ("WaterSlowsMovement", "bool", True, "Survivors", "Deep water slows survivors."),
    ("GasCansOnBacks", "bool", False, "Survivors", "Survivors carry gas cans on their backs (scavenge style)."),
    ("ZombieGhostDelayMin", "num", 20, "Survivors", "Versus: shortest respawn wait for infected players (seconds)."),
    ("ZombieGhostDelayMax", "num", 30, "Survivors", "Versus: longest respawn wait for infected players (seconds)."),
    # --- finale and gauntlet
    ("MinimumStageTime", "num", 1, "Finale", "Shortest time a finale stage lasts (seconds)."),
    ("ScriptedStageType", "int", 0, "Finale", "The current custom stage: STAGE_PANIC, STAGE_TANK, STAGE_DELAY, STAGE_CLEAROUT..."),
    ("ScriptedStageValue", "num", 1000, "Finale", "The custom stage's value (waves, seconds...)."),
    ("GauntletMovementThreshold", "num", 500, "Finale", "Gauntlet: how far survivors must move to count as moving."),
    ("GauntletMovementTimerLength", "num", 5, "Finale", "Gauntlet: how often movement is checked (seconds)."),
    ("GauntletMovementBonus", "num", 2, "Finale", "Gauntlet: extra calm time for moving on."),
    ("GauntletMovementBonusMax", "num", 30, "Finale", "Gauntlet: most extra calm time."),
    # --- music
    ("MusicDynamicMobSpawnSize", "int", 25, "Music", "Mob size that starts the horde music."),
    ("MusicDynamicMobStopSize", "int", 8, "Music", "Commons left when the horde music stops."),
    ("MusicDynamicMobScanStopSize", "int", 3, "Music", "Commons left near survivors when the horde music stops."),
    # --- challenge (mutation-style rules: scripted mode)
    ("cm_AggressiveSpecials", "bool", True, "Challenge", "Specials attack aggressively."),
    ("cm_AllowPillConversion", "bool", False, "Challenge", "Pills may turn into other items."),
    ("cm_AllowSurvivorRescue", "bool", False, "Challenge", "Dead survivors can be rescued from closets."),
    ("cm_AutoReviveFromSpecialIncap", "bool", True, "Challenge", "Survivors get up by themselves after a special lets go."),
    ("cm_AutoSpawnInfectedGhosts", "bool", True, "Challenge", "Infected players spawn by themselves."),
    ("cm_BaseCommonAttackDamage", "num", 1, "Challenge", "Common infected hit damage."),
    ("cm_BaseSpecialLimit", "int", 4, "Challenge", "Specials of each type allowed."),
    ("cm_CommonLimit", "int", 30, "Challenge", "Most commons at once."),
    ("cm_FirstManOut", "bool", True, "Challenge", "The round ends when the first survivor leaves the end safe room."),
    ("cm_HeadshotOnly", "bool", True, "Challenge", "Common infected only die from headshots."),
    ("cm_HealingGnome", "bool", True, "Challenge", "The gnome heals whoever carries it."),
    ("cm_InfiniteFuel", "bool", True, "Challenge", "Chainsaws never run out."),
    ("cm_MaxSpecials", "int", 4, "Challenge", "Most specials at once."),
    ("cm_NoSurvivorBots", "bool", True, "Challenge", "No survivor bots."),
    ("cm_ProhibitBosses", "bool", True, "Challenge", "No Tanks or Witches."),
    ("cm_ShouldEscortHumanPlayers", "bool", True, "Challenge", "Bots stay with the human players."),
    ("cm_ShouldHurry", "bool", True, "Challenge", "Bots hurry."),
    ("cm_SingleScavengeCluster", "bool", True, "Challenge", "Scavenge items come in one cluster."),
    ("cm_SpecialSlotCountdownTime", "num", 10, "Challenge", "Seconds before a special slot refills."),
    ("cm_SpecialsRetreatToCover", "bool", True, "Challenge", "Specials retreat to cover."),
    ("cm_TankLimit", "int", 1, "Challenge", "Most Tanks at once."),
    ("cm_TankRun", "bool", True, "Challenge", "Tank-run rules."),
    ("cm_TempHealthOnly", "bool", True, "Challenge", "Survivors only have temporary health."),
    ("cm_VIPTarget", "bool", True, "Challenge", "Infected focus one survivor."),
    ("cm_WanderingZombieDensityModifier", "num", 1.0, "Challenge", "How many wanderers are placed."),
    ("cm_WitchLimit", "int", 1, "Challenge", "Most Witches at once."),
]
BY_KEY = {o[0]: o for o in OPTIONS}

# Hooks: (name, where it lives, arguments [(name, kind)], answer kind or None, description).
# "mode": a function in the map's mode script; "options": a function in the Director options.
HOOKS = [
    ("AllowTakeDamage", "mode", [("attacker", "thing"), ("victim", "thing"), ("inflictor", "thing"), ("damage", "num"),
                                 ("damage_type", "num"), ("weapon", "thing"), ("position", "vec")], "bool",
     "Before anything takes damage: answer false to stop it. Damage can be changed."),
    ("InterceptChat", "mode", [("text", "text"), ("speaker", "thing")], None, "Someone typed in chat."),
    ("UserConsoleCommand", "mode", [("player", "thing"), ("command", "text")], None,
     "A player ran 'scripted_user_func <text>' in their console (bind it to a key)."),
    ("AllowWeaponSpawn", "options", [("classname", "text")], "bool", "A weapon or item is about to spawn: answer false to stop it."),
    ("ConvertWeaponSpawn", "options", [("classname", "text")], "text",
     "A weapon spawn may become something else: answer with the new class name (empty = unchanged)."),
    ("ConvertZombieClass", "options", [("zombie_type", "num")], "num",
     "A special is about to spawn: answer with the type it should be instead (1 smoker ... 6 charger, 8 tank)."),
    ("ShouldAvoidItem", "options", [("classname", "text")], "bool", "Bots ask whether to leave an item alone."),
    ("GetDefaultItem", "options", [("slot", "num")], "text",
     "What survivors start with: answer with an item name for slot 0, 1, 2... (empty = no more)."),
    ("CanPickupObject", "mode", [("object", "thing")], "bool", "A survivor tries to pick up a physics object."),
    ("ShouldPlayBossMusic", "options", [("music", "num")], "bool", "The Tank or Witch music is about to play."),
]
HOOKS_BY_NAME = {h[0]: h for h in HOOKS}

GROUPS = sorted({o[3] for o in OPTIONS})

# the game's HUD slots and flags (values read from the game's DirectorScript constants)
HUD_SLOTS = [("0", "Left Top", ""), ("1", "Left Bottom", ""), ("2", "Middle Top", ""), ("3", "Middle Bottom", ""),
             ("4", "Right Top", ""), ("5", "Right Bottom", ""), ("6", "Ticker", ""), ("7", "Far Left", ""),
             ("8", "Far Right", ""), ("9", "Middle Box", ""), ("10", "Score Title", ""), ("11", "Score 1", ""),
             ("12", "Score 2", ""), ("13", "Score 3", ""), ("14", "Score 4", "")]
HUD_FLAG_BLINK, HUD_FLAG_NOBG = 8, 64
HUD_ALIGN = {"LEFT": 256, "CENTER": 512, "RIGHT": 768}
HUD_TEAM = {"ALL": 0, "SURVIVORS": 1024, "INFECTED": 2048}
GAME_MODE = "hammerless"          # Hammerless's own mode (base co-op) that turns scripted mode on
