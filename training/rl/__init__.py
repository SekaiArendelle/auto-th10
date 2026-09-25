"""The memory-backed reinforcement-learning layer: the protocols and the adapter."""

from .actions import ACTION_SCHEMA_VERSION, ActionSpec, ModelAction, decode_action, encode_action
from .checkpoint import (
    CHECKPOINT_FORMAT_VERSION,
    CheckpointError,
    LoadedCheckpoint,
    load_checkpoint,
    save_checkpoint,
)
from .dagger import (
    DaggerBuffer,
    DaggerIteration,
    DaggerRollout,
    DaggerSample,
    ImitationBatch,
    Learner,
    collect_dagger_rollout,
    run_dagger_iteration,
)
from .features import FEATURE_SCHEMA_VERSION, FeatureSpec, MemoryFeatureEncoder
from .gym_env import MemoryGymEnv
from .imitation import ImitationLoss, ImitationMetrics, imitation_loss, update_imitation
from .model import (
    ActionEvaluation,
    ActionSample,
    ActorCritic,
    ActorCriticOutput,
    ModelSpec,
)
from .rewards import RewardBreakdown, RewardSpec, memory_reward
from .teacher import EvasiveTeacher, Teacher

__all__ = [
    "ACTION_SCHEMA_VERSION",
    "CHECKPOINT_FORMAT_VERSION",
    "FEATURE_SCHEMA_VERSION",
    "ActionSpec",
    "ActionEvaluation",
    "ActionSample",
    "ActorCritic",
    "ActorCriticOutput",
    "CheckpointError",
    "DaggerBuffer",
    "DaggerIteration",
    "DaggerRollout",
    "DaggerSample",
    "FeatureSpec",
    "EvasiveTeacher",
    "MemoryFeatureEncoder",
    "MemoryGymEnv",
    "ModelAction",
    "ModelSpec",
    "ImitationLoss",
    "ImitationMetrics",
    "ImitationBatch",
    "Learner",
    "LoadedCheckpoint",
    "RewardBreakdown",
    "RewardSpec",
    "Teacher",
    "decode_action",
    "collect_dagger_rollout",
    "encode_action",
    "imitation_loss",
    "load_checkpoint",
    "memory_reward",
    "run_dagger_iteration",
    "save_checkpoint",
    "update_imitation",
]
