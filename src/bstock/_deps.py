"""Import of optional dependencies, with the extra to install when one is missing."""

from __future__ import annotations

import importlib
from types import ModuleType

from bstock.exceptions import MissingDependencyError

__all__ = ["require"]


def require(module: str, extra: str) -> ModuleType:
    """Import and return ``module``, which is provided by the bstock extra ``extra``.

    Parameters
    ----------
    module
        Module name, e.g. ``"requests"`` or ``"pyproj"``.
    extra
        Name of the extra installing it, e.g. ``"connectors"`` or ``"geo"``.

    Raises
    ------
    MissingDependencyError
        If the module cannot be imported.
    """
    try:
        return importlib.import_module(module)
    except ImportError as error:
        raise MissingDependencyError(
            f"{module!r} is required for this feature; install it with "
            f"pip install 'bstock[{extra}]'"
        ) from error
