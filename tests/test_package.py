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
        (bstock.UnknownKindError, ValueError),
        (bstock.HierarchyError, ValueError),
        (bstock.ProjectClassError, TypeError),
        (bstock.MissingDependencyError, ImportError),
    ],
)
def test_specific_errors_keep_their_builtin_base(error, builtin):
    assert issubclass(error, bstock.BstockError)
    assert issubclass(error, builtin)


def test_public_api_is_explicit():
    for name in bstock.__all__:
        assert hasattr(bstock, name)


def test_unmapped_value_warning_is_a_bstock_warning():
    assert issubclass(bstock.UnmappedValueWarning, bstock.BstockWarning)
