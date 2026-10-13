# Sound: what a map can control of L4D2's audio, and how new sounds ship

What was measured about L4D2's sound engine from a map's side (per-map sound overrides, per-player DSP, mix layers, distance filtering), how new sounds can be shipped (an addon VPK with its own `sound.cache`), the automatic soundscapes Hammerless writes, and the shelved impulse-response ("Gunfire Acoustics v2") work. User-facing: `docs/GETTING_STARTED.md` (Sound panel), `docs/systems/MODELS_AND_TEXTURES.md` ("Sounds"), `docs/systems/LOGIC_AND_SCRIPTING.md` (the measurements not yet in nodes).

Code: `hammerless/core/acoustics.py` (ray-traced soundscape zones and sound portals; the ray casting is passed in: Blender's BVH over the scene), `hammerless/blender/props.py` (`sound_mode`: Off / Automatic Reverb / Reverb + City Ambience), the Sound view (*Trace Sound*), the Control Volume's Sound tick (the trace skips a Not Inside volume / listens only inside an Only Inside one). The game-side file is `scripts/soundscapes_<map>.txt` (the one file the game loads by name alone, so a stale one survives Sound being turned off: audit 6).

Timeline (2026): automatic soundscapes 10-07 (b37692f); sound control measured 10-10; gunfire v1 (positioned through-wall copies) and v2 (impulse responses) on branch `audio-director` 10-10, shelved 10-10; new-sound shipping blocked 10-10, unblocked (addon `sound.cache`) 10-11.

## Automatic soundscapes (status: `done`, first version; the user's listening verdict not recorded)

Why: L4D2's engine does reverb at runtime through soundscape `dsp "1"` (its automatic preset traces around the listener and blends room / hall / tunnel / open-space reverb); Valve's maps use it in 619 of 626 soundscapes, but a map without soundscapes gets none. What the engine can't work out is the ambience (outdoors, sheltered or indoors; where the outside comes in), so Hammerless precomputes it by ray tracing the scene at build (`acoustics.py`): listening spots on a 128-unit grid dropped onto every floor (several storeys) at ear height 64, at most 6,000 spots (coarser on bigger maps); 96 rays per spot; a ray travelling 8192 units reached the sky; sky share >= 0.45 of the upper hemisphere = outdoor, >= 0.08 sheltered, else indoor; neighbouring spots of a kind form zones, each a `trigger_soundscape` plus a soundscape; indoor zones get up to 8 "sound portals" (positions at openings to outdoor zones, louder through wider openings), the soundscape system's `position` entries as Valve's sound designers place by hand. *Reverb + City Ambience* uses Valve's urban ambience. Off for imported maps (they keep their own; the trace only sees the scene's brushes and had placed spots in the void, CHANGELOG 0.9.x). A survivor start reaching no floor crashed the trace once (fixed, audit). Open from the audits: soundscape brush counts on huge maps (needs the game).

Ideas not built: more themes (rural...), map-placed sounds (alarms, generators) played from the doorways they'd be heard through.

## What a map can control (measured 2026-10-10 with the bench on a test map)

| Mechanism | Measured | Status |
|---|---|---|
| `maps/<map>_level_sounds.txt` | works: overrides any game sound for that map only (`Weapon_Pistol.Single` pointed at a 76 s file read back 76.48 s with `GetSoundDuration(name, "")`) and adds new entries (`HL.Test.Sound`) | measured; not in nodes |
| `point_clientcommand` (input `Command`, activator = the player) | runs console commands on one player: `echo` and `dsp_player 17` took effect on the host: per-listener DSP; untested on a remote client (`FCVAR_SERVER_CAN_EXECUTE`) | measured; used by the Camera node |
| `snd_setmixlayer`, `snd_setmixlayer_amount`, `snd_setmixer`, `dsp_parms`, `snd_soundmixer_parse_debug` | "Unknown command" in retail | measured |
| `sound_mix_layer` entity | exists: `MixLayerName` + `Level` keyvalue, input `Level`, or NetProps `m_fMixLayerLevel` (networked); layers and groups come from the global `scripts/soundmixers.txt` (`mobBeatingLayer` -> the "infected" group, `pzAttackLayer`, `rainLayer`, `stormLayer`, `safeRoomLayer`...); audible effect not measured | partial |
| Script sound API | `EmitAmbientSoundOn(name, vol, soundlevel, pitch, ent)`, `EmitSoundOn`, `EmitSoundOnClient`, `StopSoundOn`, `StopAmbientSoundOn`, `PrecacheScriptSound`, `GetSoundDuration` | from the API dump |
| Positional vs per-client | `EmitSoundOn` on an `info_target` pans hard left/right; `EmitSoundOnClient(sound, player)` plays at the listener (L == R exactly) and doesn't play for non-players; a `weapon_fire` -> `EmitSoundOnClient` blip starts 30-60 ms **before** the stock gunshot on the host | measured |
| DSP cvars in retail | `dsp_facingaway`, `dsp_mix_min/max`, `dsp_automatic`, `dsp_room`, `dsp_db_*`: "Unknown command" (no per-sound facing-away filter). Existing: `dsp_dist_min 0` / `dsp_dist_max 1440` (each player's DSP mixed into a sound by distance: dry at 0, full at 1440+), `dsp_player`, `dsp_off`, `snd_refdb 60`, `snd_refdist 36`; preset 31 = low-pass 1000 Hz; the engine's auto DSP (`dsp 1`) is a room/courtyard classifier interpolating presets 100+; local gunfire gets only dsp 0.4 (soundmixers) | measured |
| Distance dulling | with real sources (steady hiss, `SNDLVL_NONE`; an earlier run was invalid because `models/props_junk/wood_crate001a.mdl` doesn't exist and gib-model `prop_dynamic`s vanish: use `SpawnEntityFromTable("info_target")` and keep the handle) the engine dulls a sound with distance, live, per sound: 4-10 kHz / 200-800 Hz bands at 200 vs 2000 units: dsp 0: +10/-2 vs -7/-14 dB; `dsp_player 31`: -28/-27 vs -50/-39 dB (takes vary ~10 dB, trend consistent). So a through-wall copy can be muffled by placing it farther, volume compensated | measured |
| Recording | `startmovie` wav; needs `snd_mute_losefocus 0` or recordings are silent when the game is unfocused; `play <path>` console errors are unreliable after a first failure | method |

Not tested: whether a map's pakfile can override `scripts/soundmixers.txt` or `dsp_presets.txt` (a pakfile `dsp_presets.txt` showed no sign of being read; `dsp_reload` exists). The bench note: a map flooding events (hl_test_examples) starves the bench after console commands; audio probes used c2m1_highway.

## New sounds: blocked, then unblocked (2026-10-10/11)

| Finding | Result |
|---|---|
| A **new** wav path: loose under `left4dead2/sound/`, in an addon VPK (enabled in `addonlist.txt`, mounted), in the map's pakfile, with the `*` streamed prefix, precached at map load, from a fresh start | all fail: "S_StartSound: Failed to load sound ..., file probably missing from disk/repository"; `GetSoundDuration` 0 |
| Why | at startup the game reads only its five stock `sound/sound.cache` files (`left4dead2`, `update`, `dlc1-3`) and plays only cached paths |
| `snd_rebuildaudiocache` (cheat) | rebuilds the five caches (~90 s, includes loose files present), then the game **quits**; the loose wav then played; **damage**: it rewrites Steam's cache files, after which the user heard a mostly muted shotgun (2026-10-11). Never run it on a user's install; *Verify integrity of game files* in Steam restores them |
| Fable's review | every installed workshop addon that adds new sound paths (7 of 7) ships its own `sound/sound.cache` **inside** the VPK; the probe VPK had none |
| **Confirmed in game 2026-10-11** (c2m1) | an addon VPK with its own `sound/sound.cache` plays new wavs (a cached 3 kHz tone recorded at +108 dB); a second new wav in the same VPK but missing from the cache failed. **New sounds ship in one addon VPK with a cache Hammerless writes** |
| Also confirmed 2026-10-11 | an addon VPK's `scripts/dsp_presets.txt` overrides the stock presets from a fresh game start (preset 17 replaced by `DLY PLAIN 500.0 0.7 1.0 LP 20000 0 LO 0 0 0`, mix `0.9 0.9`: a rifle shot echoed every 0.5 s with the addon, decayed smoothly without; no `dsp_reload` needed): baked per-zone reverb presets are shippable |
| Replacing an existing path (same-format) | the earlier "the cached original still plays" result was likely flawed (mono tones replaced stereo files); same-format replacement probably works, as gun mods do: unverified |

`sound.cache` format (decoded from workshop addons 760657091 and 2249963776): header int32 2, 3, 0, count; per record int32 length, the path with a NUL (`sound\dir\f.wav`), int32 file size, u8 flags (0x01 | 0x40 16-bit | 0x20 8-bit | 0x80 mono), int32 `rate << 6 | 1 << 4 | log2(blockAlign) << 2 | (channels - 1)`, int32 data start, int32 data size, int32 loop start (-1), int32 sample count (25 bytes after the path for PCM). Untested engine commands: `snd_buildsoundcachefordirectory`, `snd_updateaudiocache`. Status: `not done` (custom sounds aren't part of the build; the shipping rules are known).

## Gunfire acoustics (shelved)

v1 (branch `audio-director`, 2026-10-10): positioned through-wall copies of shots. The user found it unrealistic in courtyards, and the review found: copies heard by every player, grid cells ignore thin walls, static-channel exhaustion, the server-played own shot lags for guests.

v2 plan ("impulse response", `core/impulse.py` prototype on the branch): trace each spot's response from the map geometry (first-order mirror echoes plus a diffuse tail, 3 bands, sky = lost), convolve the game's gun WAVs at build, pack wet tails per spot class, play the tail per listener with `EmitSoundOnClient`, keep the game's own predicted dry shot; a `#` (dry) prefix to avoid doubling engine reverb (unverified). Measured: the gun WAVs are plain PCM 16-bit stereo 44.1 kHz (MP5 22.05 kHz); the Python tracer took ~1-4 s per spot; on a large user map a closed room's tail energy was ~9x the 1 m dry level, a courtyard 0.29 with echoes at 36-120 ms. Open in-game unknowns: `EmitSoundOnClient` timing against the shot, the `#` prefix.

Status: `shelved` by the user 2026-10-10 (the blocker was shipping custom tails; the addon-cache finding of 10-11 removes it, but the work is not to restart unasked). Main got only the cancel feature from the branch (d49bf15).

## Limits and open items

| Item | Status |
|---|---|
| Custom sounds in the build (addon VPK + cache writer) | not done |
| Per-map sound overrides, mix layers, per-player DSP as nodes | not done |
| Per-player commands on remote clients | untested |
| Addon override of `soundmixers.txt` | untested |
| Soundscape brush counts on huge maps | open (needs the game) |
| Stale `soundscapes_<map>.txt` after Sound is turned off | known issue (audit 6) |

Last checked: 2026-10-13 against branch light-layers, commit 46f75ac.
