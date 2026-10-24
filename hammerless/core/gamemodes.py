"""Supported launch modes and their authoring requirements."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    launch: str
    campaign_route: bool
    director_overrides: bool


MODES = {
    "CAMPAIGN": Mode("coop", True, True),
    "SURVIVAL": Mode("survival", False, False),
}


def policy(name: str) -> Mode:
    return MODES[name]
