"""The kernel's surface towards the platform (ADR-0022, ADR-0028, ADR-0039).

Modules and their workflow definitions are data under `definitions/`, read at boot and never
imported: a definition's code runs in the sandbox. `tests/test_module_boundary.py` holds both
directions.
"""

from app.kernel.modules import SURFACES, Module
from app.kernel.schema import plain
from app.kernel.workflows import Computed, Draft, Workflow

__all__ = ["Computed", "Draft", "Module", "SURFACES", "Workflow", "plain"]
