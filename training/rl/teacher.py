"""Teacher annotations over the learned policy's action vocabulary.

DAgger asks an expert what it would do in states reached by the learned policy.
The query and the executed action are deliberately separate: a stateful teacher
must advance from what the learner actually did, not from advice it ignored.
"""

from typing import Protocol

from auto_th10 import Observation

from ..policy import EvasivePolicy
from .actions import ModelAction, decode_action, encode_action


class Teacher(Protocol):
    """Annotate learner states and then observe the learner's actual decision."""

    def annotate(self, observation: Observation) -> ModelAction:
        """Return the model-action label for one observation."""

    def feedback(self, action: ModelAction, *, frames: int) -> None:
        """Commit the model action that was successfully applied after the label."""

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

    def __init__(self, policy: EvasivePolicy | None = None) -> None:
        self.policy = EvasivePolicy() if policy is None else policy
        self._waiting_for_feedback = False

    def annotate(self, observation: Observation) -> ModelAction:
        if self._waiting_for_feedback:
            raise RuntimeError("feedback() must complete the previous annotation")
        action = encode_action(self.policy.recommend(observation))
        self._waiting_for_feedback = True
        return action

    def feedback(self, action: ModelAction, *, frames: int) -> None:
        if not self._waiting_for_feedback:
            raise RuntimeError("annotate() must be called before feedback()")
        native = decode_action(action)
        self.policy.commit(native, frames=frames)
        self._waiting_for_feedback = False

    def reset(self) -> None:
        self.policy.reset()
        self._waiting_for_feedback = False
