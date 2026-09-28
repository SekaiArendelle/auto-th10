"""Teacher annotations over the learned policy's action vocabulary.

DAgger asks an expert what it would do in states reached by the learned policy.
The query and the executed action are deliberately separate: a stateful teacher
must advance from what the learner actually did, not from advice it ignored.
"""

from dataclasses import dataclass
from typing import Protocol

from auto_th10 import Action, Observation

from ..policy import BOMB_POWER_COST, EvasivePolicy
from .actions import ActionSpec, ModelAction, decode_action, encode_action
from .features import FeatureSpec, perceived_observation

PREFERRED_MOVEMENT_MASS = 0.5


@dataclass(frozen=True, slots=True)
class TeacherAnnotation:
    """One preferred action plus a distribution over equally safe movements."""

    action: ModelAction
    movement_probabilities: tuple[float, ...]

    @property
    def movement(self) -> int:
        return self.action.movement

    @property
    def bomb(self) -> bool | int:
        return self.action.bomb


class Teacher(Protocol):
    """Annotate learner states and then observe the learner's actual decision."""

    def annotate(self, observation: Observation) -> TeacherAnnotation:
        """Return the model-action label for one observation."""

    def feedback(
        self, action: ModelAction | TeacherAnnotation, *, frames: int
    ) -> None:
        """Commit the model action that was successfully applied after the label."""

    def advance(self, *, frames: int) -> None:
        """Advance elapsed game time that did not contain a model decision."""

    def reset(self) -> None:
        """Forget state before starting an independent trajectory."""


class EvasiveTeacher:
    """Expose ``EvasivePolicy`` as a DAgger-safe movement and bomb teacher.

    Exactly one feedback call completes each annotation. The pairing makes an
    ignored bomb recommendation remain available on the next dangerous frame,
    while a bomb the learner chose on its own starts the teacher's cooldown.
    Rollout boundaries within one game are not independent trajectories and must
    not call reset().
    """

    def __init__(
        self,
        policy: EvasivePolicy | None = None,
        *,
        feature_spec: FeatureSpec = FeatureSpec(),
    ) -> None:
        self.policy = EvasivePolicy() if policy is None else policy
        self.feature_spec = feature_spec
        self._waiting_for_feedback = False
        self._bomb_available = False

    def annotate(self, observation: Observation) -> TeacherAnnotation:
        if self._waiting_for_feedback:
            raise RuntimeError("feedback() must complete the previous annotation")
        perceived = perceived_observation(observation, self.feature_spec)
        recommended, alternatives = self.policy.recommend_with_alternatives(perceived)
        action = encode_action(recommended)
        movements = {encode_action(candidate).movement for candidate in alternatives}
        if len(movements) == 1:
            probabilities = tuple(
                1.0 if movement == action.movement else 0.0
                for movement in range(ActionSpec().movement_choices)
            )
        else:
            alternative_probability = (1.0 - PREFERRED_MOVEMENT_MASS) / (
                len(movements) - 1
            )
            probabilities = tuple(
                PREFERRED_MOVEMENT_MASS
                if movement == action.movement
                else alternative_probability
                if movement in movements
                else 0.0
                for movement in range(ActionSpec().movement_choices)
            )
        self._bomb_available = perceived.snapshot.power >= BOMB_POWER_COST
        self._waiting_for_feedback = True
        return TeacherAnnotation(action=action, movement_probabilities=probabilities)

    def feedback(
        self, action: ModelAction | TeacherAnnotation, *, frames: int
    ) -> None:
        if not self._waiting_for_feedback:
            raise RuntimeError("annotate() must be called before feedback()")
        model_action = (
            action.action if isinstance(action, TeacherAnnotation) else action
        )
        native = decode_action(model_action)
        if native & Action.BOMB and not self._bomb_available:
            native &= ~Action.BOMB
        self.policy.commit(native, frames=frames)
        self._waiting_for_feedback = False

    def advance(self, *, frames: int) -> None:
        if self._waiting_for_feedback:
            raise RuntimeError("feedback() must complete the previous annotation")
        self.policy.elapse(frames=frames)

    def reset(self) -> None:
        self.policy.reset()
        self._waiting_for_feedback = False
        self._bomb_available = False
