import importlib
import sys

import pytest


def test_bigdata_package_blocked_in_commercial():
    for mod in list(sys.modules):
        if mod.startswith("api.bigdata"):
            del sys.modules[mod]
    with pytest.raises(ImportError):
        importlib.import_module("api.bigdata")
