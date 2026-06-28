"""Identity seams (minimal by design — reuse OIDC, don't reinvent).

Three concepts the runtime needs to know on every action:
  - Tenant: which customer/org the run belongs to (multi-tenancy boundary).
  - Actor: who the agent is acting *as* (the agent's own identity + the principal).
  - Scope: the permissions/consent that bound what the actor may do.

This is a placeholder contract — real verification (OIDC token validation, consent
lookup) is wired in app.api.deps and a future identity provider.
"""

from __future__ import annotations

from pydantic import BaseModel


class Principal(BaseModel):
    """The authenticated caller context attached to a request/run."""

    tenant_id: str
    actor_id: str            # the agent / service identity
    scopes: list[str] = []   # granted permissions / consent flags