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
    ],
)
def test_errors_derive_from_bstock_error(error):
    assert issubclass(error, bstock.BstockError)


def test_public_api_is_explicit():
    for name in bstock.__all__:
        assert hasattr(bstock, name)
