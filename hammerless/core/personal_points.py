"""Round-scoped personal points, emitted only when a map opts in."""

DEFAULTS = {"starting": 0, "common": 10, "special": 50, "tank": 500, "witch": 250}


def script(settings):
    values = {key: max(0, min(1000000, int(settings.get(key, default))))
              for key, default in DEFAULTS.items()}
    config = ", ".join(f"{key} = {value}" for key, value in values.items())
    feedback = "true" if settings.get("feedback", True) else "false"
    return f"::HL_PointsConfig <- {{ {config}, feedback = {feedback} }};\n" + RUNTIME


EVENTS = {
    "round_start": "    HL_PointsReset();",
    "infected_death": '    if ("attacker" in params) HL_PointsAward(GetPlayerFromUserID(params.attacker), HL_PointsConfig.common);',
    "player_death": '''    if (p != null && !p.IsSurvivor() && "attacker" in params) {
        local z = p.GetZombieType();
        if (z >= 1 && z <= 6 || z == 8)
            HL_PointsAward(GetPlayerFromUserID(params.attacker), z == 8 ? HL_PointsConfig.tank : HL_PointsConfig.special);
    }''',
    "witch_killed": '    if ("userid" in params) HL_PointsAward(GetPlayerFromUserID(params.userid), HL_PointsConfig.witch);',
    "player_say": '''    if ("text" in params && "userid" in params && params.text.tolower() == "!points")
        HL_PointsShow(GetPlayerFromUserID(params.userid), 3);''',
}

RUNTIME = r'''
// Accounts belong to humans, never to a survivor character or a temporary bot.
::HL_PointsBalances <- {};
::HL_PointsKey <- function(p) {
    if (p == null || !p.IsValid() || !p.IsPlayer() || !p.IsSurvivor() || IsPlayerABot(p)) return null;
    local id = p.GetNetworkIDString();
    if (id != null && id != "" && id != "BOT" && id != "STEAM_ID_PENDING"
        && id != "STEAM_ID_LAN" && id != "VALVE_ID_LAN" && id != "UNKNOWN") return "steam:" + id;
    // LAN/pending identities must not share a wallet. These last for this connection only.
    return "session:" + p.GetPlayerUserId();
};
::HL_PointsReset <- function() { ::HL_PointsBalances = {}; };
::HL_PointsGet <- function(p) {
    local key = HL_PointsKey(p);
    if (key == null) return 0;
    if (!(key in HL_PointsBalances)) HL_PointsBalances[key] <- HL_PointsConfig.starting;
    return HL_PointsBalances[key];
};
::HL_PointsShow <- function(p, destination) {
    if (HL_PointsKey(p) != null) ClientPrint(p, destination, "Points: " + HL_PointsGet(p));
};
::HL_PointsAward <- function(p, amount) {
    local key = HL_PointsKey(p);
    if (key == null || amount <= 0) return;
    local balance = HL_PointsGet(p);
    // Keep arithmetic within signed 32-bit integers, even during very long sessions.
    local earned = amount < 1000000000 - balance ? amount : 1000000000 - balance;
    HL_PointsBalances[key] = balance + earned;
    if (HL_PointsConfig.feedback && earned > 0)
        ClientPrint(p, 4, "+" + earned + " points | Total: " + HL_PointsBalances[key]);
};
'''
