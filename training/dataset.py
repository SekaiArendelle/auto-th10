"""The versioned JSON shape used for recorded agent transitions.

Keeping serialization outside the collector makes the dataset a contract rather
than an incidental view of the command line tool. Version 1 records both sides
of each transition and every field in the memory-backed observation; an
incompatible observation change introduces a new version rather than silently
changing these rows.
"""

from auto_th10 import Snapshot

from .loop import Step

SCHEMA_VERSION = 1


def snapshot_to_dict(snapshot: Snapshot) -> dict[str, object]:
    """Return every field a memory-reading policy can observe."""
    return {
        "player": {"x": snapshot.player.x, "y": snapshot.player.y},
        "score": snapshot.score,
        "power": snapshot.power,
        "lives": snapshot.lives,
        "game_over": snapshot.game_over,
        "enemies": [
            {"x": enemy.x, "y": enemy.y, "width": enemy.width, "height": enemy.height}
            for enemy in snapshot.enemies
        ],
        "enemy_bullets": [
            {
                "x": bullet.x,
                "y": bullet.y,
                "width": bullet.width,
                "height": bullet.height,
                "dx": bullet.dx,
                "dy": bullet.dy,
            }
            for bullet in snapshot.enemy_bullets
        ],
        "enemy_lasers": [
            {
                "x": laser.x,
                "y": laser.y,
                "width": laser.width,
                "height": laser.height,
                "radian": laser.radian,
            }
            for laser in snapshot.enemy_lasers
        ],
        "resources": [{"x": resource.x, "y": resource.y} for resource in snapshot.resources],
    }


def to_row(step: Step) -> dict[str, object]:
    """Serialize one complete state-action-state transition."""
    return {
        "schema_version": SCHEMA_VERSION,
        "episode": step.episode,
        "step": step.index,
        "observation": snapshot_to_dict(step.observation.snapshot),
        "action": int(step.action),
        "reward": step.reward,
        "next_observation": snapshot_to_dict(step.next_observation.snapshot),
        "terminated": step.terminated,
    }
