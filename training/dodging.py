"""The geometry a dodging policy needs, kept out of the policy itself.

A policy that only reacts to how close a bullet is cannot really dodge: what
matters is where the bullet will be while the player is there, and where the
player could be instead. Both questions are arithmetic over a Snapshot, so they
live here as pure functions - which is also what lets them be tested without a
game and without a policy.

The constants and the shape of the value function are transcribed from TH10AI
(`D:\\projects\\TH10AI\\Src\\GameManager.cpp`), a rule-based player that was
written against this same game by reading the same memory, and each one names
the function it came from. Only the constants are borrowed; the search built on
top of them differs, and `EvasivePolicy` says how.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from auto_th10 import Action, EnemyLaser, Point, Rect, Snapshot

Box = tuple[float, float, float, float, float, float]
"""One hazard as the search walks it: centre x and y, half width, half height,
and the distance it travels per frame in x and y."""

FOCUS_SPEED = 2.0
NORMAL_SPEED = 4.5
"""The player's speed per frame with and without the focus key held
(GameManager.cpp's `playerSpeed[2] = {4.5, 2.0}`, in playfield pixels)."""

HALF_WIDTH = 200.0
FIELD_HEIGHT = 480.0
"""The playfield as the game measures it: x runs -200 to 200 with 0 in the
middle, y from 0 at the top to 480 at the bottom (GameManager.cpp's `ulCorner`
and `drCorner`). The player is held inside it, so a move into a wall slides
along it rather than leaving the field."""

COLLISION_MARGIN = 4.5
"""How far outside its own box the player still counts as hit (GameManager.cpp's
`hitTest`). The reference warns in a comment there not to tune this: larger and
the player gives up room it did not have to, smaller and it walks into bullets
it cannot see far enough ahead to avoid."""

BULLET_LEAD = 1.0
"""What a bullet's own `dx`/`dy` is multiplied by when it is extrapolated.

The reference scales them by 0.5 without saying why, and we have not measured
the game's own step against them yet, so the neutral 1.0 is the honest reading:
one bullet step per frame. Lower it if the policy turns out to be careless
around fast bullets.
"""

SCAN_RADIUS = 150.0
MAX_HAZARDS = 24
"""Where the hazards are cut off. A hard stage holds hundreds of bullets and the
search runs every frame in Python, so it looks at the nearest few. The radius
covers everything that can arrive inside `horizon`: twelve frames of the player's
own movement is about 54 px, and a bullet that is going to reach the band from
further out than this would have to cross 150 px in those frames, over 12 px per
frame, faster than anything this game fires. That bound assumes one bullet step
per frame, so it is worth re-checking if `BULLET_LEAD` is ever measured."""

BOMB_FRAMES = 3
"""How soon every candidate move has to be hit before a bomb is worth it."""

BOMB_COOLDOWN_FRAMES = 240
"""How long to leave the bomb key alone after spending one: the bomb is pressed
and then not pressed again for this many calls to `decide()`. The reference uses
240 frames for the same purpose, which is the invulnerability a bomb grants; the
snapshot carries no such flag, so a frame counter stands in for it."""

RESOURCE_RANGE_SQR = 390400.0
"""The squared distance at which a resource point stops pulling
(GameManager.cpp's `getPowerValue`). The reference writes it as 390400 rather
than as 625 squared, which is the same distance to within a pixel; it is kept as
written so the two stay comparable."""

THREAT_RANGE_SQR = 20000.0
"""The squared distance at which an enemy counts as close
(GameManager.cpp's `getThreatValue`; about 141 px)."""

CROWDED_Y = 240.0
"""Above this the enemies are still near their own firing line, which is what
makes standing under them dangerous (GameManager.cpp's `getThreatValue`)."""

INCOMING_RADIUS = 45.0
"""How far out a bullet flying this way counts against a spot, in
`attack_value`. The reference's own band is 30 px; this is a little wider because
its moves are coarser than a BFS step. It is deliberately narrow: the term is
what keeps the player from settling in a lane bullets are coming down, and a band
wide enough to reach across the stage starts steering the player around the
whole field instead."""


_ENEMY_AIM_RANGE = 400.0
"""The distance at which lining up with an enemy stops mattering
(GameManager.cpp's `getKillValue`)."""

_DIAGONAL = math.sqrt(0.5)
"""A diagonal step covers the same distance as a straight one, so each axis of
it moves by this much per frame."""

_EPSILON = 1e-9
_LASER_ARC_OFFSET = -5.0 * math.pi / 2.0
"""What the game's laser angle is offset by before the box is rotated
(GameManager.cpp's `updateEnemyLaserBoxes`)."""

DIRECTIONS: tuple[tuple[float, float, Action], ...] = (
    (0.0, 0.0, Action.NONE),
    (1.0, 0.0, Action.RIGHT),
    (_DIAGONAL, -_DIAGONAL, Action.RIGHT | Action.UP),
    (0.0, -1.0, Action.UP),
    (-_DIAGONAL, -_DIAGONAL, Action.LEFT | Action.UP),
    (-1.0, 0.0, Action.LEFT),
    (-_DIAGONAL, _DIAGONAL, Action.LEFT | Action.DOWN),
    (0.0, 1.0, Action.DOWN),
    (_DIAGONAL, _DIAGONAL, Action.RIGHT | Action.DOWN),
)
"""The eight directions the game accepts plus standing still, as (x, y, keys).
y points down, so UP is the negative one; the diagonals carry the same speed as
a straight step, split over two axes."""


def box(
    x: float,
    y: float,
    half_width: float,
    half_height: float,
    *,
    dx: float = 0.0,
    dy: float = 0.0,
) -> Box:
    """Builds a hazard box. A name for the field order, for the tests and the
    snapshot conversion below."""
    return (x, y, half_width, half_height, dx, dy)


def laser_box(laser: EnemyLaser) -> Box:
    """A laser's rectangle, turned by its angle and then wrapped in a box.

    GameManager.cpp's `updateEnemyLaserBoxes` is the reference for the shape:
    a quarter of `width` to either side of the origin and `height` running down
    from it, the four corners rotated around the origin by `radian`, and the
    smallest axis-aligned box around the result. None of us has verified what
    those fields mean inside the game, so a box that is too large is the safe
    error: it only makes the policy more careful than it had to be. A laser does
    not move here - one frame of a snapshot is all there is, and the reference
    does not extrapolate one either.
    """
    half = laser.width / 4.0
    arc = laser.radian + _LASER_ARC_OFFSET
    cos_arc = math.cos(arc)
    sin_arc = math.sin(arc)
    left = top = math.inf
    right = bottom = -math.inf
    for corner_x, corner_y in (
        (laser.x - half, laser.y),
        (laser.x + half, laser.y),
        (laser.x - half, laser.y + laser.height),
        (laser.x + half, laser.y + laser.height),
    ):
        offset_x = corner_x - laser.x
        offset_y = corner_y - laser.y
        x = laser.x + offset_x * cos_arc - offset_y * sin_arc
        y = laser.y + offset_x * sin_arc + offset_y * cos_arc
        left = min(left, x)
        right = max(right, x)
        top = min(top, y)
        bottom = max(bottom, y)
    return box((left + right) / 2.0, (top + bottom) / 2.0, (right - left) / 2.0, (bottom - top) / 2.0)


def _reaches(x: float, y: float, hazard: Box, radius: float) -> bool:
    return (hazard[0] - x) ** 2 + (hazard[1] - y) ** 2 <= radius * radius


def boxes_from(
    snapshot: Snapshot,
    *,
    lead: float = BULLET_LEAD,
    radius: float = SCAN_RADIUS,
    limit: int | None = MAX_HAZARDS,
) -> tuple[Box, ...]:
    """Everything on screen that can end the run, nearest first.

    Bullets are extrapolated with their own velocity times `lead`, enemies are
    treated as their own boxes (they can be flown into), and lasers are boxed by
    `laser_box`. `radius` and `limit` thin out the bullets and the enemies: a
    move is only safe if nothing reaches it, and nothing that far away can. A
    laser is exempt from both, because its box is long and thin - where its
    centre is says nothing about whether it covers the player - and there are
    never many of them.
    """
    x, y = snapshot.player.x, snapshot.player.y
    near = []
    for bullet in snapshot.enemy_bullets:
        near.append(
            box(
                bullet.x,
                bullet.y,
                bullet.width / 2.0,
                bullet.height / 2.0,
                dx=bullet.dx * lead,
                dy=bullet.dy * lead,
            )
        )
    for enemy in snapshot.enemies:
        near.append(box(enemy.x, enemy.y, enemy.width / 2.0, enemy.height / 2.0))
    near = [hazard for hazard in near if _reaches(x, y, hazard, radius)]
    near.sort(key=lambda hazard: (hazard[0] - x) ** 2 + (hazard[1] - y) ** 2)
    if limit is not None:
        near = near[:limit]
    return tuple(near) + tuple(laser_box(laser) for laser in snapshot.enemy_lasers)


def survival(
    x: float,
    y: float,
    step_x: float,
    step_y: float,
    boxes: Sequence[Box],
    *,
    horizon: int,
    margin: float = COLLISION_MARGIN,
) -> tuple[int, float, float]:
    """Holds one key for `horizon` frames and reports how it goes.

    The answer is the first frame a hazard sits on the player - `horizon + 1`
    when nothing does, so a caller can rank any surviving move above any fatal
    one without a special case - together with the position the move reaches by
    then, which is what the value of the move is read from.

    Stepping frame by frame is what makes the field edge honest: a move into a
    wall slides along it instead of leaving the field, and a closed-form answer -
    the two axes and the times they spend inside the hit band - would have the
    player keep going through the wall. The price is one pass per frame of
    look-ahead, so the horizon is a frame-budget question: at sixty frames the
    whole search costs about half a millisecond, which is a second of play and
    covers a bullet crossing a hundred pixels.

    A bullet is assumed to keep the velocity the snapshot gave it and to stay
    alive for the whole walk; both are approximations, and both err towards
    calling a spot dangerous.
    """
    for frame in range(1, horizon + 1):
        x = min(max(x + step_x, -HALF_WIDTH), HALF_WIDTH)
        y = min(max(y + step_y, 0.0), FIELD_HEIGHT)
        for hazard_x, hazard_y, half_width, half_height, dx, dy in boxes:
            if (
                abs(x - (hazard_x + dx * frame)) <= half_width + margin
                and abs(y - (hazard_y + dy * frame)) <= half_height + margin
            ):
                return frame, x, y
    return horizon + 1, x, y


def position_value(x: float, y: float) -> float:
    """How good a spot on the field is with nothing on it (0 to 1).

    GameManager.cpp's `getMapValue`: the value peaks just above the bottom of the
    field (y = 390) and falls off towards the enemies at the top and towards the
    very bottom edge, and it is higher in the middle column than at the sides,
    which is where both shot types concentrate their fire. Above y = 100 the drop
    is steep, because that is the band the enemies fire down through.
    """
    if y <= 100.0:
        return y * 0.9 / 100.0
    depth = (abs(390.0 - y) * (-10.0 / 290.0) + 100.0) / 100.0
    centring = (HALF_WIDTH - abs(x)) / HALF_WIDTH
    return depth * 0.95 + centring * 0.05


def aim_value(x: float, enemies: Sequence[Rect]) -> float:
    """How well `x` lines up with the nearest enemy (0 to 1).

    GameManager.cpp's `getKillValue`: the shot goes straight up, so a policy
    that wants to hurt anything has to be under it. The snapshot's enemies carry
    a box instead of the reference's velocity, so only the position is aligned.
    """
    if not enemies:
        return 0.0
    nearest = min(_ENEMY_AIM_RANGE, min(abs(enemy.x - x) for enemy in enemies))
    return (_ENEMY_AIM_RANGE - nearest) / _ENEMY_AIM_RANGE


def attack_value(x: float, y: float, boxes: Sequence[Box]) -> float:
    """How much the bullets on their way argue against standing there
    (0 to at most -2).

    GameManager.cpp's `getAttackValue`, widened and made decisive. A bullet that
    is heading straight at the spot scores -2 and one heading straight away
    scores 0; the answer is the worst bullet in the band, not their average.
    Averaging is what the reference does for its threat term, where each enemy is
    a judgement of its own, but a bullet coming for the player is the whole
    signal: averaged against a dozen bullets flying away it fades to nothing in
    exactly the heavy stages this is for.

    This is the companion to the survival walk, not a substitute for it. The
    walk answers "does a bullet get here", which is a yes or no per move, and it
    has to look far enough ahead to see the bullet coming at all; this answers
    "how much is this spot in the way", which stays smooth as a bullet closes in
    and keeps the player from settling right beside one it is not going to hit. A
    bullet that is not moving has no course to be out of: it is a wall, and the
    walk already treats it as one. The reference only looks 30 px out; this band
    is wider because the moves here are coarser than a BFS step.
    """
    worst = 0.0
    for hazard_x, hazard_y, _, _, dx, dy in boxes:
        speed = math.hypot(dx, dy)
        if speed < _EPSILON:
            continue
        offset_x = x - hazard_x
        offset_y = y - hazard_y
        distance = math.hypot(offset_x, offset_y)
        if distance < _EPSILON:
            return -2.0
        if distance > INCOMING_RADIUS:
            continue
        worst = min(worst, -((offset_x * dx + offset_y * dy) / (distance * speed) + 1.0))
    return worst


def resource_value(x: float, y: float, resources: Sequence[Point]) -> float:
    """How close the nearest resource point is (0 to 1).

    GameManager.cpp's `getPowerValue`, with a fixed range: collecting points is
    what raises power, and power is what kills things, so the pull is stronger
    than any of the other terms. The reference raises it further while it is
    invulnerable to hoover up after a bomb; there is no invulnerability flag in
    the snapshot to read, so the careful weight is the only one used.
    """
    if not resources:
        return 0.0
    nearest = min((resource.x - x) ** 2 + (resource.y - y) ** 2 for resource in resources)
    nearest = min(nearest, RESOURCE_RANGE_SQR)
    return (RESOURCE_RANGE_SQR - nearest) / RESOURCE_RANGE_SQR


def threat_value(x: float, y: float, enemies: Sequence[Rect]) -> float:
    """How dangerous the enemies themselves make a spot (0 to at most -3).

    Both terms are GameManager.cpp's `getThreatValue`, averaged over the enemies
    they apply to so that one distant enemy cannot cancel out one close one:
    standing under an enemy that is still in the top half is where a wall of
    bullets closes in, and standing close to any enemy risks being flown into.
    """
    if not enemies:
        return 0.0
    crowded = 0.0
    crowded_count = 0
    close = 0.0
    close_count = 0
    for enemy in enemies:
        if enemy.y <= CROWDED_Y:
            offset_x = x - enemy.x
            offset_y = y - enemy.y
            length = math.hypot(offset_x, offset_y)
            if length < _EPSILON:
                crowded -= 2.0
            else:
                crowded -= (-offset_y / length) + 1.0
            crowded_count += 1
        distance = (x - enemy.x) ** 2 + (y - enemy.y) ** 2
        if distance <= THREAT_RANGE_SQR:
            close -= 1.0 - distance / THREAT_RANGE_SQR
            close_count += 1
    return (crowded / crowded_count if crowded_count else 0.0) + (
        close / close_count if close_count else 0.0
    )


def moves(*, focus: bool = True) -> tuple[tuple[Action, float, float], ...]:
    """Every key combination worth trying, as (keys, x per frame, y per frame).

    Standing still is one candidate rather than two: holding focus while going
    nowhere moves nobody, and the search is per frame.

    With `focus` off, only the full speed is offered, so the policy never holds
    the focus key at all - the coarse and quick way to play.
    """
    speeds = ((FOCUS_SPEED, Action.FOCUS), (NORMAL_SPEED, Action.NONE)) if focus else ((NORMAL_SPEED, Action.NONE),)
    found: list[tuple[Action, float, float]] = []
    for unit_x, unit_y, keys in DIRECTIONS:
        if unit_x == 0.0 and unit_y == 0.0:
            found.append((Action.FOCUS if focus else Action.NONE, 0.0, 0.0))
            continue
        for speed, speed_keys in speeds:
            found.append((keys | speed_keys, unit_x * speed, unit_y * speed))
    return tuple(found)
