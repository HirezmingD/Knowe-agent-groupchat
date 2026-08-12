# [v1.0.13][R2] Shared Agent Identity Contract: platform, display name, role and self-reference.
"""One identity contract for Coordinator, Zinnia, Workers and legacy agents."""

from __future__ import annotations

from dataclasses import dataclass
from .i18n_backend import msg


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    platform_name: str
    agent_id: str
    display_name: str
    role_name: str
    user_self_reference: str

    def system_block(self) -> str:
        """Render the high-priority, model-facing identity block."""

        return (
            msg("agent_identity.py.001") +
            msg("agent_identity.py.002", **{"self.display_name": self.display_name}) +
            msg("agent_identity.py.003", **{"self.role_name": self.role_name}) +
            msg("agent_identity.py.004", **{"self.user_self_reference": self.user_self_reference}) +
            msg("agent_identity.py.005", **{"self.agent_id": self.agent_id})
        )


def coordinator_identity() -> AgentIdentity:
    return AgentIdentity(
        platform_name="Knowe",
        agent_id="coordinator",
        display_name=msg("agent_identity.py.006"),
        role_name=msg("agent_identity.py.007"),
        user_self_reference=msg("agent_identity.py.008"),
    )


def zinnia_identity() -> AgentIdentity:
    return AgentIdentity(
        platform_name="Knowe",
        agent_id="zinnia",
        display_name=msg("agent_identity.py.009"),
        role_name=msg("agent_identity.py.010"),
        user_self_reference=msg("agent_identity.py.011"),
    )


def worker_identity(*, agent_id: str, display_name: str, role_name: str) -> AgentIdentity:
    display = display_name.strip() or agent_id.strip() or msg("agent_identity.py.012")
    role = role_name.strip() or msg("agent_identity.py.013")
    return AgentIdentity(
        platform_name="Knowe",
        agent_id=agent_id.strip() or "worker",
        display_name=display,
        role_name=role,
        user_self_reference=msg("agent_identity.py.014", display=display, role=role),
    )


def identity_for(
    agent_id: str,
    *,
    display_name: str = "",
    role_name: str = "",
) -> AgentIdentity:
    normalized = agent_id.strip().casefold()
    if normalized == "coordinator":
        return coordinator_identity()
    if normalized == "zinnia":
        return zinnia_identity()
    return worker_identity(
        agent_id=agent_id,
        display_name=display_name,
        role_name=role_name,
    )
