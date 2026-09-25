"""Supervised teacher loss used by behavior cloning and DAgger."""

import math
from dataclasses import dataclass

import torch
from torch import nn
from torch.nn import functional as functional

from .model import ActorCritic


@dataclass(frozen=True, slots=True)
class ImitationLoss:
    total: torch.Tensor
    movement: torch.Tensor
    bomb: torch.Tensor


@dataclass(frozen=True, slots=True)
class ImitationMetrics:
    total: float
    movement: float
    bomb: float


def imitation_loss(
    model: ActorCritic,
    observations: torch.Tensor,
    teacher_actions: torch.Tensor,
    *,
    bomb_positive_weight: float = 8.0,
) -> ImitationLoss:
    """Cross-entropy on both heads, up-weighting the rare bomb-positive label."""
    weight = _require_positive_finite(bomb_positive_weight, "bomb_positive_weight")
    if teacher_actions.ndim != 2 or teacher_actions.shape[1] != 2:
        raise ValueError("teacher_actions must have shape (batch, 2)")
    output = model(observations)
    movement = functional.cross_entropy(
        output.movement_logits, teacher_actions[:, 0]
    )
    bomb_weights = torch.tensor(
        (1.0, weight),
        dtype=output.bomb_logits.dtype,
        device=output.bomb_logits.device,
    )
    bomb = functional.cross_entropy(
        output.bomb_logits, teacher_actions[:, 1], weight=bomb_weights
    )
    return ImitationLoss(total=movement + bomb, movement=movement, bomb=bomb)


def update_imitation(
    model: ActorCritic,
    optimizer: torch.optim.Optimizer,
    observations: torch.Tensor,
    teacher_actions: torch.Tensor,
    *,
    bomb_positive_weight: float = 8.0,
    max_grad_norm: float = 0.5,
) -> ImitationMetrics:
    """Apply one bounded-gradient teacher update and return detached metrics."""
    gradient_limit = _require_positive_finite(max_grad_norm, "max_grad_norm")
    optimizer.zero_grad(set_to_none=True)
    losses = imitation_loss(
        model,
        observations,
        teacher_actions,
        bomb_positive_weight=bomb_positive_weight,
    )
    losses.total.backward()
    nn.utils.clip_grad_norm_(model.parameters(), gradient_limit)
    optimizer.step()
    return ImitationMetrics(
        total=float(losses.total.detach().item()),
        movement=float(losses.movement.detach().item()),
        bomb=float(losses.bomb.detach().item()),
    )


def _require_positive_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    checked = float(value)
    if not math.isfinite(checked) or checked <= 0.0:
        raise ValueError(f"{name} must be positive and finite")
    return checked
