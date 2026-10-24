# Game mode settings

Choose **Game Mode** in **Map**, then open **Map > Game Mode Settings**.
Campaign and Survival store independent overrides in the blend file. Switching
modes preserves both sets; only the selected mode is exported.

Search by name or description, or filter by category. Check the small override
checkbox before editing a value. Unchecked options are omitted from the export:
the game chooses its defaults for its mode and difficulty. The numbers shown
before enabling an override are suggested starting values, not queried defaults.
For Boolean options, the first checkbox enables the override and the second
sets the actual true/false value.

Campaign includes Director pacing, mobs, wanderers, specials, bosses, survivor
rules, finale options, music and mutation-style gameplay options. Survival
offers the shared survivor, health, awareness, music and gameplay settings;
its native scheduler still controls waves and escalating infected limits.
Options specific to Versus/Scavenge or requiring a custom stage controller are
excluded. This is the supported map-settings catalogue, not every server cvar.

Native Survival wave intervals, decay and population escalation are protected
server console settings. They are not exposed as working MapOptions and this
panel does not turn on cheats. Custom Survival pacing needs separate support.

Build & Play after editing. Enabled overrides use the existing Hammerless
Campaign/Survival wrapper and Valve's `MapOptions` table, available before map
entities spawn. No logic graph is required. The same table also retains logic
override hooks. MapOptions take precedence over lower-priority map Director
settings for the same key; avoid configuring the same option in both panels.
Turning all overrides off returns to stock launch mode unless the graph needs
the wrapper for HUD or hooks. Settings do not change global server convars.

Implementation references: the existing Director node catalogue and
[Valve's shipped scripted-mode merge code](https://github.com/Tsuey/L4D2-Community-Update/blob/master/root/scripts/vscripts/scriptedmode.nut).
