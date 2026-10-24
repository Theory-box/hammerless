"""Actionable messages for measured game flow, without guessing an obstruction location."""


def message(report, game_mode="CAMPAIGN"):
    state = report["state"]
    if state == "arenaok":
        return "INFO", "In game: Survival start connects to battlefield/finale nav. Spawn availability still depends on visibility, distance and active game rules."
    if state in ("noarena", "arenabroken"):
        return "ERROR", ("Survival needs connected BATTLEFIELD and FINALE nav. Resize the Survival Arena region over the playable floors, check its connections to Player Start, and rebuild nav.")
    if game_mode == "SURVIVAL" and state in ("noexit", "noend", "broken"):
        return "INFO", "This is a campaign flow result. Build & Play in Survival for an arena connectivity check; Survival does not need an end safe room."
    if state == "ok":
        return "INFO", f"In game: Director flow works ({report['length']:.0f} units). This confirms the route, not the number of infected."
    messages = {
        "nonav": "The loaded game map has no nav areas. Choose Build & Play to generate and deploy the nav, then reload the map.",
        "noexit": "The loaded game map has no Change Level Volume (info_changelevel). A CHECKPOINT Nav Attribute Region only marks nav; it does not provide the campaign endpoint. Add a Change Level Volume covering the end room, set Next Map and Landmark, add the matching info_landmark, then Build & Play. Finale maps need their finale setup instead.",
        "nostart": "The loaded nav has no PLAYER_START areas. Check the survivor spawns and floor, or add a Nav Attribute Region with PLAYER_START covering the starting floor. Rebuild nav and use Build & Play.",
        "noend": "The loaded nav has no end CHECKPOINT or FINALE areas. Make the Change Level Volume overlap the safe-room floor, or mark that floor CHECKPOINT using a Nav Attribute Region. Rebuild nav and use Build & Play; a campaign still needs the Change Level Volume.",
    }
    if state == "broken":
        if report.get("connected"):
            text = "The loaded nav connects start to end, including elevator and ladder links (ignoring temporary blockers), but the Director reports zero flow. Check the Change Level Volume's Next Map and matching Landmark, ensure the destination map is installed, and check locked doors or event blockers. Build & Play after fixing the setup. No particular obstruction has been identified."
        else:
            text = "The loaded nav has no start-to-end route even with elevator and ladder links. Check the main route's nav connections and lift boarding areas in the nav view, then rebuild nav and Build & Play. The search does not identify a particular blocking object; a nearby dead end is not proof of the cause."
        return "ERROR", text
    return "ERROR", messages.get(state, "The game's flow result is unknown. Build & Play again to collect a fresh result.")
