"""The fixed shooting rule shared by scripted and learned policies."""

from __future__ import annotations

from auto_th10 import Action, Snapshot

DIALOGUE_BULLET_RADIUS = 33.0
"""How close a bullet has to be before a quiet screen counts as combat.

TH10AI asks for bullets within ``maxDepth * playerSpeed[0] + 15`` pixels
before using an empty result as its dialogue heuristic: 4 * 4.5 + 15 = 33.
"""


def shoot_action(snapshot: Snapshot, decision: int) -> Action:
    """Hold shoot in combat, or tap it every other decision in dialogue.

    The game exposes no dialogue state in the snapshot. TH10AI's practical
    heuristic is a quiet field: at most one enemy and no bullet within 33
    pixels of the player. Alternating the shoot bit advances dialogue much
    faster than holding it continuously because the game sees fresh presses.
    """
    radius_sqr = DIALOGUE_BULLET_RADIUS**2
    nearby_bullet = any(
        (bullet.x - snapshot.player.x) ** 2 + (bullet.y - snapshot.player.y) ** 2
        <= radius_sqr
        for bullet in snapshot.enemy_bullets
    )
    if len(snapshot.enemies) <= 1 and not nearby_bullet:
        return Action.SHOOT if decision % 2 else Action.NONE
    return Action.SHOOT
