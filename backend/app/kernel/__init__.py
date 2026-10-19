"""The kernel's surface towards modules (ADR-0022, ADR-0028).

A module imports from `app.kernel` and nowhere else in `app`; the kernel never imports a module by
name, it loads the ones listed in `app.kernel.modules.INSTALLED`. `tests/test_module_boundary.py`
holds both directions.
"""

from app.kernel.modules import SURFACES, Module

__all__ = ["Module", "SURFACES"]
