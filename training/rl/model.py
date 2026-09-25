"""Shared actor-critic network for imitation learning and PPO."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical

from .actions import ActionSpec, ModelAction


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Checkpoint-visible network shape."""

    observation_size: int
    hidden_sizes: tuple[int, ...] = (256, 256)

    def __post_init__(self) -> None:
        if isinstance(self.observation_size, bool) or not isinstance(
            self.observation_size, int
        ):
            raise TypeError("observation_size must be an integer")
        if self.observation_size < 1:
            raise ValueError("observation_size must be positive")
        for size in self.hidden_sizes:
            if isinstance(size, bool) or not isinstance(size, int):
                raise TypeError("hidden_sizes must contain integers")
            if size < 1:
                raise ValueError("hidden_sizes must be positive")


@dataclass(frozen=True, slots=True)
class ActorCriticOutput:
    movement_logits: torch.Tensor
    bomb_logits: torch.Tensor
    value: torch.Tensor


@dataclass(frozen=True, slots=True)
class ActionEvaluation:
    log_prob: torch.Tensor
    entropy: torch.Tensor
    value: torch.Tensor


@dataclass(frozen=True, slots=True)
class ActionSample:
    action: ModelAction
    log_prob: float
    value: float


class ActorCritic(nn.Module):
    """An MLP trunk with independent movement, bomb and value heads."""

    def __init__(
        self,
        spec: ModelSpec,
        *,
        action_spec: ActionSpec = ActionSpec(),
    ) -> None:
        super().__init__()
        self.spec = spec
        self.action_spec = action_spec
        layers: list[nn.Module] = []
        input_size = spec.observation_size
        for hidden_size in spec.hidden_sizes:
            layers.extend((nn.Linear(input_size, hidden_size), nn.Tanh()))
            input_size = hidden_size
        self.trunk = nn.Sequential(*layers) if layers else nn.Identity()
        self.movement_head = nn.Linear(input_size, action_spec.movement_choices)
        self.bomb_head = nn.Linear(input_size, action_spec.bomb_choices)
        self.value_head = nn.Linear(input_size, 1)

    def forward(self, observations: torch.Tensor) -> ActorCriticOutput:
        if observations.shape[-1] != self.spec.observation_size:
            raise ValueError(
                f"expected observation size {self.spec.observation_size}, "
                f"got {observations.shape[-1]}"
            )
        hidden = self.trunk(observations)
        return ActorCriticOutput(
            movement_logits=self.movement_head(hidden),
            bomb_logits=self.bomb_head(hidden),
            value=self.value_head(hidden).squeeze(-1),
        )

    def evaluate_actions(
        self, observations: torch.Tensor, actions: torch.Tensor
    ) -> ActionEvaluation:
        if actions.shape[-1] != 2:
            raise ValueError("actions must have movement and bomb columns")
        output = self(observations)
        movement = Categorical(logits=output.movement_logits)
        bomb = Categorical(logits=output.bomb_logits)
        return ActionEvaluation(
            log_prob=movement.log_prob(actions[..., 0])
            + bomb.log_prob(actions[..., 1]),
            entropy=movement.entropy() + bomb.entropy(),
            value=output.value,
        )

    @torch.no_grad()
    def act(
        self,
        observation: np.ndarray | Sequence[float] | torch.Tensor,
        *,
        deterministic: bool = False,
    ) -> ActionSample:
        device = next(self.parameters()).device
        tensor = torch.as_tensor(observation, dtype=torch.float32, device=device)
        if tensor.ndim != 1:
            raise ValueError("act() expects one observation")
        output = self(tensor)
        movement_distribution = Categorical(logits=output.movement_logits)
        bomb_distribution = Categorical(logits=output.bomb_logits)
        if deterministic:
            movement = output.movement_logits.argmax()
            bomb = output.bomb_logits.argmax()
        else:
            movement = movement_distribution.sample()
            bomb = bomb_distribution.sample()
        log_prob = movement_distribution.log_prob(movement) + bomb_distribution.log_prob(
            bomb
        )
        return ActionSample(
            action=ModelAction(movement=int(movement.item()), bomb=int(bomb.item())),
            log_prob=float(log_prob.item()),
            value=float(output.value.item()),
        )
