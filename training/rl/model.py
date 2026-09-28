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
    hidden_sizes: tuple[int, ...] = (256, 128)
    architecture: str = "mlp"
    global_size: int = 0
    entity_layout: tuple[tuple[int, int], ...] = ()
    entity_size: int = 64
    attention_heads: int = 4
    attention_queries: int = 2

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
        if self.architecture not in ("mlp", "entity_attention"):
            raise ValueError("architecture must be 'mlp' or 'entity_attention'")
        if self.architecture == "entity_attention":
            if self.global_size < 1:
                raise ValueError("global_size must be positive")
            if not self.entity_layout:
                raise ValueError("entity_layout must not be empty")
            if self.global_size + sum(
                count * width for count, width in self.entity_layout
            ) != self.observation_size:
                raise ValueError("entity layout does not match observation_size")
        for name in ("entity_size", "attention_heads", "attention_queries"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 1:
                raise ValueError(f"{name} must be positive")
        if self.entity_size % self.attention_heads:
            raise ValueError("entity_size must be divisible by attention_heads")
        for entry in self.entity_layout:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise TypeError("entity layout entries must be (count, width) tuples")
            count, width = entry
            if any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in entry
            ):
                raise TypeError("entity layout entries must contain integers")
            if count < 0 or width < 1:
                raise ValueError("entity layout entries must be non-negative widths")


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
    """A dense baseline or shared entity encoder with separate actor and critic."""

    def __init__(
        self,
        spec: ModelSpec,
        *,
        action_spec: ActionSpec = ActionSpec(),
    ) -> None:
        super().__init__()
        self.spec = spec
        self.action_spec = action_spec
        if spec.architecture == "mlp":
            self._build_mlp()
        else:
            self._build_entity_attention()

    def _build_mlp(self) -> None:
        layers, output_size = _mlp_layers(
            self.spec.observation_size, self.spec.hidden_sizes
        )
        self.trunk = nn.Sequential(*layers) if layers else nn.Identity()
        self.movement_head = nn.Linear(
            output_size, self.action_spec.movement_choices
        )
        self.bomb_head = nn.Linear(output_size, self.action_spec.bomb_choices)
        self.value_head = nn.Linear(output_size, 1)

    def _build_entity_attention(self) -> None:
        spec = self.spec
        self.global_encoder = nn.Sequential(
            nn.Linear(spec.global_size, spec.entity_size),
            nn.Tanh(),
            nn.Linear(spec.entity_size, spec.entity_size),
            nn.Tanh(),
        )
        self.entity_encoders = nn.ModuleList(
            nn.Sequential(
                nn.Linear(width, spec.entity_size),
                nn.Tanh(),
                nn.Linear(spec.entity_size, spec.entity_size),
                nn.Tanh(),
            )
            for _, width in spec.entity_layout
        )
        self.entity_attention = nn.ModuleList(
            nn.MultiheadAttention(
                spec.entity_size,
                spec.attention_heads,
                batch_first=True,
            )
            for _ in spec.entity_layout
        )
        self.attention_queries = nn.ParameterList(
            nn.Parameter(torch.empty(spec.attention_queries, spec.entity_size))
            for _ in spec.entity_layout
        )
        self.null_entities = nn.ParameterList(
            nn.Parameter(torch.empty(1, spec.entity_size))
            for _ in spec.entity_layout
        )
        for query, null in zip(
            self.attention_queries, self.null_entities, strict=True
        ):
            nn.init.normal_(query, std=0.02)
            nn.init.normal_(null, std=0.02)
        fusion_size = spec.entity_size * (
            1 + len(spec.entity_layout) * spec.attention_queries
        )
        actor_layers, actor_size = _mlp_layers(fusion_size, spec.hidden_sizes)
        critic_layers, critic_size = _mlp_layers(fusion_size, spec.hidden_sizes)
        self.actor = nn.Sequential(*actor_layers) if actor_layers else nn.Identity()
        self.critic = (
            nn.Sequential(*critic_layers) if critic_layers else nn.Identity()
        )
        self.movement_head = nn.Linear(
            actor_size, self.action_spec.movement_choices
        )
        self.bomb_head = nn.Linear(actor_size, self.action_spec.bomb_choices)
        self.value_head = nn.Linear(critic_size, 1)

    def forward(self, observations: torch.Tensor) -> ActorCriticOutput:
        if observations.shape[-1] != self.spec.observation_size:
            raise ValueError(
                f"expected observation size {self.spec.observation_size}, "
                f"got {observations.shape[-1]}"
            )
        if self.spec.architecture == "mlp":
            actor_hidden = self.trunk(observations)
            critic_hidden = actor_hidden
        else:
            shared = self._encode_entities(observations)
            actor_hidden = self.actor(shared)
            critic_hidden = self.critic(shared)
        return ActorCriticOutput(
            movement_logits=self.movement_head(actor_hidden),
            bomb_logits=self.bomb_head(actor_hidden),
            value=self.value_head(critic_hidden).squeeze(-1),
        )

    def _encode_entities(self, observations: torch.Tensor) -> torch.Tensor:
        original_shape = observations.shape[:-1]
        flat = observations.reshape(-1, self.spec.observation_size)
        pooled = [self.global_encoder(flat[:, : self.spec.global_size])]
        offset = self.spec.global_size
        for index, (count, width) in enumerate(self.spec.entity_layout):
            end = offset + count * width
            raw = flat[:, offset:end].reshape(flat.shape[0], count, width)
            encoded = self.entity_encoders[index](raw)
            valid = raw[..., -1] > 0.5
            null = self.null_entities[index].expand(flat.shape[0], -1, -1)
            encoded = torch.cat((encoded, null), dim=1)
            padding = torch.cat(
                (
                    ~valid,
                    torch.zeros(
                        (flat.shape[0], 1), dtype=torch.bool, device=flat.device
                    ),
                ),
                dim=1,
            )
            queries = self.attention_queries[index].expand(flat.shape[0], -1, -1)
            attended, _ = self.entity_attention[index](
                queries,
                encoded,
                encoded,
                key_padding_mask=padding,
                need_weights=False,
            )
            pooled.append(attended.flatten(start_dim=1))
            offset = end
        shared = torch.cat(pooled, dim=-1)
        return shared.reshape(*original_shape, shared.shape[-1])

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


def _mlp_layers(
    input_size: int, hidden_sizes: tuple[int, ...]
) -> tuple[list[nn.Module], int]:
    layers: list[nn.Module] = []
    output_size = input_size
    for hidden_size in hidden_sizes:
        layers.extend((nn.Linear(output_size, hidden_size), nn.Tanh()))
        output_size = hidden_size
    return layers, output_size
