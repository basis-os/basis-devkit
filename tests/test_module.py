from __future__ import annotations

import logging

from loguru import logger
from snapflow.core.environment import Environment
from snapflow.core.module import DEFAULT_LOCAL_MODULE, SnapflowModule
from snapflow.core.function import Function
from snapflow.modules.core import module as core

logger.enable("snapflow")


def test_module_init():
    from . import _test_module

    assert isinstance(_test_module.module, SnapflowModule)
    assert len(_test_module.all_schemas) >= 1
    assert len(_test_module.all_functions) >= 2


def test_core_module():
    # These are times two because we have an entry for both `name` and `namespace.name`
    assert len(core.functions) == 8
    assert len(core.schemas) == 2

    core.run_tests()


def test_default_module():
    DEFAULT_LOCAL_MODULE.library.functions = {}

    @Function
    def s1():
        pass

    assert len(DEFAULT_LOCAL_MODULE.library.functions) == 1
    assert DEFAULT_LOCAL_MODULE.get_function("s1") is s1

    env = Environment()
    env.add_function(s1)
    assert env.get_function("s1") is s1


if __name__ == "__main__":
    core.run_tests()
