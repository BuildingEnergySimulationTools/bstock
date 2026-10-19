import importlib
import inspect
import pkgutil

import pytest

import bstock


def test_version_is_exposed():
    assert isinstance(bstock.__version__, str)
    assert bstock.__version__ != "0.0.0"


@pytest.mark.parametrize(
    "error",
    [
        bstock.DuplicateIdError,
        bstock.UnknownIdError,
        bstock.InvalidIdError,
        bstock.EntityInUseError,
        bstock.ConnectorError,
    ],
)
def test_errors_derive_from_bstock_error(error):
    assert issubclass(error, bstock.BstockError)


@pytest.mark.parametrize(
    ("error", "builtin"),
    [
        (bstock.InvalidTagError, ValueError),
        (bstock.InvalidArgumentError, ValueError),
        (bstock.UnknownKindError, bstock.InvalidArgumentError),
        (bstock.HierarchyError, ValueError),
        (bstock.ClassMismatchError, TypeError),
        (bstock.MissingDependencyError, ImportError),
    ],
)
def test_specific_errors_keep_their_builtin_base(error, builtin):
    assert issubclass(error, bstock.BstockError)
    assert issubclass(error, builtin)


def test_public_api_is_explicit():
    for name in bstock.__all__:
        assert hasattr(bstock, name)


PUBLIC_SUBMODULES = {
    "bstock.cache",
    "bstock.io",
    "bstock.export",
    "bstock.connectors",
    "bstock.connectors.rnb",
    "bstock.connectors.bdnb",
    "bstock.connectors.dpe",
    "bstock.connectors.cadastre",
}
OTHER_MODULES = sorted(
    info.name
    for info in pkgutil.walk_packages(bstock.__path__, "bstock.")
    if info.name not in PUBLIC_SUBMODULES and not info.name.rsplit(".", 1)[-1].startswith("_")
)


@pytest.mark.parametrize("module_name", OTHER_MODULES)
def test_other_modules_only_list_names_exported_by_bstock(module_name):
    module = importlib.import_module(module_name)
    assert set(getattr(module, "__all__", [])) <= set(bstock.__all__)


@pytest.mark.parametrize("module_name", sorted(PUBLIC_SUBMODULES))
def test_public_submodules_define_their_api(module_name):
    module = importlib.import_module(module_name)
    for name in module.__all__:
        assert hasattr(module, name)


@pytest.mark.parametrize("module_name", sorted(PUBLIC_SUBMODULES))
def test_public_submodules_export_every_unprefixed_function_and_class(module_name):
    # dpe.enrich was once missing from __all__; helpers must start with an underscore
    module = importlib.import_module(module_name)
    defined = {
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and (inspect.isfunction(value) or inspect.isclass(value))
        and value.__module__ == module_name
    }
    assert defined <= set(module.__all__)


def test_unmapped_value_warning_is_a_bstock_warning():
    assert issubclass(bstock.UnmappedValueWarning, bstock.BstockWarning)
