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
    movement_accuracy: float
    safe_movement_accuracy: float
    bomb_accuracy: float


def imitation_loss(
    model: ActorCritic,
    observations: torch.Tensor,
    teacher_actions: torch.Tensor,
    *,
    teacher_movement_probabilities: torch.Tensor | None = None,
    bomb_positive_weight: float = 4.0,
) -> ImitationLoss:
    """Soft movement and weighted binary-bomb cross-entropy."""
    weight = _require_positive_finite(bomb_positive_weight, "bomb_positive_weight")
    if teacher_actions.ndim != 2 or teacher_actions.shape[1] != 2:
        raise ValueError("teacher_actions must have shape (batch, 2)")
    output = model(observations)
    if teacher_movement_probabilities is None:
        movement = functional.cross_entropy(
            output.movement_logits, teacher_actions[:, 0]
        )
    else:
        if teacher_movement_probabilities.shape != output.movement_logits.shape:
            raise ValueError(
                "teacher_movement_probabilities must match movement logits"
            )
        if (
            not bool(torch.isfinite(teacher_movement_probabilities).all().item())
            or bool((teacher_movement_probabilities < 0.0).any().item())
            or not bool(
                torch.allclose(
                    teacher_movement_probabilities.sum(dim=1),
                    torch.ones(
                        teacher_movement_probabilities.shape[0],
                        dtype=teacher_movement_probabilities.dtype,
                        device=teacher_movement_probabilities.device,
                    ),
                    atol=1e-5,
                    rtol=1e-5,
                )
            )
        ):
            raise ValueError(
                "teacher_movement_probabilities must be probability rows"
            )
        movement = -(
            teacher_movement_probabilities
            * functional.log_softmax(output.movement_logits, dim=-1)
        ).sum(dim=-1).mean()
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
    teacher_movement_probabilities: torch.Tensor | None = None,
    bomb_positive_weight: float = 4.0,
    max_grad_norm: float = 0.5,
) -> ImitationMetrics:
    """Apply one bounded-gradient teacher update and return detached metrics."""
    gradient_limit = _require_positive_finite(max_grad_norm, "max_grad_norm")
    optimizer.zero_grad(set_to_none=True)
    losses = imitation_loss(
        model,
        observations,
        teacher_actions,
        teacher_movement_probabilities=teacher_movement_probabilities,
        bomb_positive_weight=bomb_positive_weight,
    )
    losses.total.backward()
    nn.utils.clip_grad_norm_(model.parameters(), gradient_limit)
    optimizer.step()
    with torch.no_grad():
        output = model(observations)
        movement = output.movement_logits.argmax(dim=-1)
        bomb = output.bomb_logits.argmax(dim=-1)
        if teacher_movement_probabilities is None:
            safe = movement == teacher_actions[:, 0]
        else:
            safe = teacher_movement_probabilities.gather(
                1, movement.unsqueeze(1)
            ).squeeze(1) > 0.0
    return ImitationMetrics(
        total=float(losses.total.detach().item()),
        movement=float(losses.movement.detach().item()),
        bomb=float(losses.bomb.detach().item()),
        movement_accuracy=float(
            (movement == teacher_actions[:, 0]).float().mean().item()
        ),
        safe_movement_accuracy=float(safe.float().mean().item()),
        bomb_accuracy=float((bomb == teacher_actions[:, 1]).float().mean().item()),
    )


def _require_positive_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    checked = float(value)
    if not math.isfinite(checked) or checked <= 0.0:
        raise ValueError(f"{name} must be positive and finite")
    return checked
