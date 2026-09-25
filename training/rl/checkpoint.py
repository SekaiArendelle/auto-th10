"""Versioned model checkpoints shared by training and evaluation."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import math
import os
import pathlib
from numbers import Real

import torch

from .actions import ActionSpec
from .features import FeatureSpec
from .model import ActorCritic, ModelSpec

CHECKPOINT_FORMAT_VERSION = 1


class CheckpointError(ValueError):
    """A checkpoint cannot be interpreted by the current training schemas."""


@dataclass(frozen=True, slots=True)
class LoadedCheckpoint:
    """A reconstructed model and the training state stored beside it."""

    iteration: int
    beta: float
    feature_spec: FeatureSpec
    action_spec: ActionSpec
    model_spec: ModelSpec
    model: ActorCritic
    optimizer_state_dict: dict[str, object]


def save_checkpoint(
    path: pathlib.Path,
    *,
    iteration: int,
    beta: float,
    feature_spec: FeatureSpec,
    action_spec: ActionSpec,
    model_spec: ModelSpec,
    model: ActorCritic,
    optimizer: torch.optim.Optimizer,
) -> None:
    """Atomically save weights plus the schemas needed to interpret them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    payload = {
        "format_version": CHECKPOINT_FORMAT_VERSION,
        "iteration": iteration,
        "beta": beta,
        "feature_spec": asdict(feature_spec),
        "action_spec": asdict(action_spec),
        "model_spec": asdict(model_spec),
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
    }
    torch.save(payload, temporary)
    os.replace(temporary, path)


def load_checkpoint(
    path: pathlib.Path,
    *,
    device: torch.device | str = "cpu",
) -> LoadedCheckpoint:
    """Load one trusted weights-only checkpoint and validate all schema metadata."""
    try:
        raw = torch.load(path, map_location=device, weights_only=True)
    except OSError:
        raise
    except Exception as error:
        raise CheckpointError(f"could not read checkpoint {path}: {error}") from error

    payload = _require_mapping(raw, "checkpoint")
    version = _require_integer(payload.get("format_version"), "format_version")
    if version != CHECKPOINT_FORMAT_VERSION:
        raise CheckpointError(
            f"unsupported checkpoint format {version}; "
            f"expected {CHECKPOINT_FORMAT_VERSION}"
        )
    iteration = _require_integer(payload.get("iteration"), "iteration")
    if iteration < 0:
        raise CheckpointError("iteration must not be negative")
    beta = _require_probability(payload.get("beta"), "beta")
    feature_spec = _load_feature_spec(payload.get("feature_spec"))
    action_spec = _load_action_spec(payload.get("action_spec"))
    model_spec = _load_model_spec(payload.get("model_spec"))
    if model_spec.observation_size != feature_spec.size:
        raise CheckpointError(
            "model observation size does not match the checkpoint feature schema"
        )

    model_state = _require_mapping(
        payload.get("model_state_dict"), "model_state_dict"
    )
    optimizer_state = _require_mapping(
        payload.get("optimizer_state_dict"), "optimizer_state_dict"
    )
    model = ActorCritic(model_spec, action_spec=action_spec).to(device)
    _validate_model_state(model_state, model)
    try:
        model.load_state_dict(dict(model_state), strict=True)
    except (RuntimeError, TypeError) as error:
        raise CheckpointError(f"model weights do not match their metadata: {error}") from error
    model.eval()
    return LoadedCheckpoint(
        iteration=iteration,
        beta=beta,
        feature_spec=feature_spec,
        action_spec=action_spec,
        model_spec=model_spec,
        model=model,
        optimizer_state_dict=dict(optimizer_state),
    )


def _validate_model_state(
    state: Mapping[object, object], model: ActorCritic
) -> None:
    expected = model.state_dict()
    for name, value in state.items():
        if not isinstance(name, str) or not isinstance(value, torch.Tensor):
            raise CheckpointError("model_state_dict must map names to tensors")
        if name not in expected:
            continue
        reference = expected[name]
        if value.shape != reference.shape:
            raise CheckpointError(
                f"model weight {name!r} has shape {tuple(value.shape)}; "
                f"expected {tuple(reference.shape)}"
            )
        if value.layout != reference.layout:
            raise CheckpointError(
                f"model weight {name!r} has layout {value.layout}; "
                f"expected {reference.layout}"
            )
        if value.dtype != reference.dtype:
            raise CheckpointError(
                f"model weight {name!r} has dtype {value.dtype}; "
                f"expected {reference.dtype}"
            )
        if (value.is_floating_point() or value.is_complex()) and not bool(
            torch.isfinite(value).all().item()
        ):
            raise CheckpointError(f"model weight {name!r} contains non-finite values")


def _load_feature_spec(value: object) -> FeatureSpec:
    data = _require_mapping(value, "feature_spec")
    try:
        return FeatureSpec(**dict(data))
    except (TypeError, ValueError) as error:
        raise CheckpointError(f"invalid feature_spec: {error}") from error


def _load_action_spec(value: object) -> ActionSpec:
    data = _require_mapping(value, "action_spec")
    try:
        return ActionSpec(**dict(data))
    except (TypeError, ValueError) as error:
        raise CheckpointError(f"invalid action_spec: {error}") from error


def _load_model_spec(value: object) -> ModelSpec:
    data = dict(_require_mapping(value, "model_spec"))
    if "hidden_sizes" in data:
        try:
            data["hidden_sizes"] = tuple(data["hidden_sizes"])
        except TypeError as error:
            raise CheckpointError("invalid model_spec: hidden_sizes is not iterable") from error
    try:
        return ModelSpec(**data)
    except (TypeError, ValueError) as error:
        raise CheckpointError(f"invalid model_spec: {error}") from error


def _require_mapping(value: object, name: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise CheckpointError(f"{name} must be a mapping")
    return value


def _require_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckpointError(f"{name} must be an integer")
    return value


def _require_probability(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise CheckpointError(f"{name} must be between 0 and 1")
    return float(value)
