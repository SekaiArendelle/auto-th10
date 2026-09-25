import unittest

from auto_th10 import Observation
from fakes import make_bullet, make_snapshot, observe
from training.policy import EvasivePolicy
from training.rl import EvasiveTeacher, ModelAction, Teacher

PLAYER = (0.0, 400.0)


class EvasiveTeacherTests(unittest.TestCase):
    def dangerous_observation(self, *, lives: int = 2) -> Observation:
        return observe(
            make_snapshot(
                player=PLAYER,
                lives=lives,
                enemy_bullets=(make_bullet(*PLAYER),),
            )
        )

    def test_it_implements_the_reusable_teacher_protocol(self) -> None:
        teacher: Teacher = EvasiveTeacher()

        label = teacher.annotate(observe(make_snapshot(player=PLAYER)))

        self.assertEqual(label, ModelAction(movement=0, bomb=False))
        teacher.feedback(label, frames=1)

    def test_it_projects_the_evasive_bomb_onto_the_model_heads(self) -> None:
        teacher = EvasiveTeacher()

        label = teacher.annotate(self.dangerous_observation())

        self.assertTrue(label.bomb)
        teacher.feedback(label, frames=1)

    def test_an_ignored_bomb_remains_the_label_on_the_next_frame(self) -> None:
        teacher = EvasiveTeacher(EvasivePolicy(bomb_cooldown_frames=5))
        observation = self.dangerous_observation()

        first = teacher.annotate(observation)
        teacher.feedback(
            ModelAction(movement=first.movement, bomb=False), frames=1
        )
        second = teacher.annotate(observation)

        self.assertTrue(first.bomb)
        self.assertTrue(second.bomb)

    def test_an_executed_bomb_starts_the_teacher_cooldown(self) -> None:
        teacher = EvasiveTeacher(EvasivePolicy(bomb_cooldown_frames=5))
        observation = self.dangerous_observation()

        first = teacher.annotate(observation)
        teacher.feedback(first, frames=1)
        second = teacher.annotate(observation)

        self.assertTrue(first.bomb)
        self.assertFalse(second.bomb)

    def test_a_learner_bomb_starts_cooldown_even_when_not_recommended(self) -> None:
        teacher = EvasiveTeacher(EvasivePolicy(bomb_cooldown_frames=5))
        clear = observe(make_snapshot(player=PLAYER))

        label = teacher.annotate(clear)
        teacher.feedback(ModelAction(movement=label.movement, bomb=True), frames=1)
        dangerous = teacher.annotate(self.dangerous_observation())

        self.assertFalse(label.bomb)
        self.assertFalse(dangerous.bomb)

    def test_annotation_and_feedback_must_be_paired(self) -> None:
        teacher = EvasiveTeacher()
        observation = observe(make_snapshot(player=PLAYER))

        with self.assertRaisesRegex(RuntimeError, "annotate"):
            teacher.feedback(ModelAction(0), frames=1)
        teacher.annotate(observation)
        with self.assertRaisesRegex(RuntimeError, "feedback"):
            teacher.annotate(observation)

    def test_reset_discards_pending_state_and_bomb_cooldown(self) -> None:
        teacher = EvasiveTeacher(EvasivePolicy(bomb_cooldown_frames=5))
        observation = self.dangerous_observation()
        label = teacher.annotate(observation)
        teacher.feedback(label, frames=1)
        teacher.reset()

        reset_label = teacher.annotate(observation)

        self.assertTrue(reset_label.bomb)

    def test_feedback_advances_cooldown_by_actual_game_frames(self) -> None:
        teacher = EvasiveTeacher(EvasivePolicy(bomb_cooldown_frames=5))
        observation = self.dangerous_observation()
        bomb = teacher.annotate(observation)
        teacher.feedback(bomb, frames=4)

        first = teacher.annotate(observation)
        teacher.feedback(ModelAction(first.movement, False), frames=1)
        second = teacher.annotate(observation)
        teacher.feedback(ModelAction(second.movement, False), frames=1)
        ready = teacher.annotate(observation)

        self.assertFalse(first.bomb)
        self.assertFalse(second.bomb)
        self.assertTrue(ready.bomb)

    def test_feedback_rejects_invalid_frame_counts_without_losing_pairing(self) -> None:
        teacher = EvasiveTeacher()
        label = teacher.annotate(self.dangerous_observation())

        with self.assertRaises(ValueError):
            teacher.feedback(label, frames=-1)

        teacher.feedback(label, frames=1)


if __name__ == "__main__":
    unittest.main()
