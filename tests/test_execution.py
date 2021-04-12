from __future__ import annotations

from typing import Optional

import pandas as pd
import pytest
from dcp.data_format.formats.memory.records import Records
from loguru import logger
from pandas import DataFrame
from snapflow.core.data_block import Alias, DataBlock, DataBlockMetadata
from snapflow.core.execution import Executable, ExecutionManager
from snapflow.core.graph import Graph
from snapflow.core.node import DataBlockLog, Direction, SnapLog
from snapflow.core.snap import Input
from snapflow.core.snap_interface import NodeInterfaceManager
from snapflow.modules import core
from sqlalchemy.sql.expression import select
from tests.utils import (
    TestSchema1,
    TestSchema4,
    make_test_env,
    make_test_run_context,
    snap_generic,
    snap_t1_sink,
    snap_t1_source,
    snap_t1_to_t2,
)

logger.enable("snapflow")

mock_dl_output = [{"f1": "2"}, {"f2": 3}]


def snap_dl_source() -> Records[TestSchema4]:
    return mock_dl_output


def snap_error() -> Records[TestSchema4]:
    raise Exception("snap FAIL")


def test_exe():
    env = make_test_env()
    g = Graph(env)
    node = g.create_node(key="node", snap=snap_t1_source)
    exe = env.get_executable(node)
    result = ExecutionManager(exe).execute()
    with env.md_api.begin():
        assert not result.output_blocks
        assert env.md_api.count(select(SnapLog)) == 1
        pl = env.md_api.execute(select(SnapLog)).scalar_one_or_none()
        assert pl.node_key == node.key
        assert pl.graph_id == g.get_metadata_obj().hash
        assert pl.node_start_state == {}
        assert pl.node_end_state == {}
        assert pl.snap_key == node.snap.key
        assert pl.snap_params == {}


def test_exe_output():
    env = make_test_env()
    env.add_module(core)
    g = Graph(env)
    # env.add_storage("python://test")
    # rt = env.runtimes[0]
    # TODO: this is error because no data copy between SAME storage engines (but DIFFERENT storage urls) currently
    # ec = env.get_run_context(g, current_runtime=rt, target_storage=env.storages[0])
    # ec = env.get_run_context(g, current_runtime=rt, target_storage=rt.as_storage())
    output_alias = "node_output"
    node = g.create_node(key="node", snap=snap_dl_source, output_alias=output_alias)
    exe = env.get_executable(node)
    result = ExecutionManager(exe).execute()
    with env.md_api.begin():
        block = result.get_output_block(env)
        assert block is not None
        assert block.as_records() == mock_dl_output
        assert block.nominal_schema is TestSchema4
        assert len(block.realized_schema.fields) == len(TestSchema4.fields)
        # Test alias was created correctly
        assert (
            env.md_api.execute(select(Alias).filter(Alias.alias == output_alias))
            .scalar_one_or_none()
            .data_block_id
            == block.data_block_id
        )
        assert env.md_api.count(select(DataBlockLog)) == 1
        dbl = env.md_api.execute(select(DataBlockLog)).scalar_one_or_none()
        assert dbl.data_block_id == block.data_block_id
        assert dbl.direction == Direction.OUTPUT


def test_non_terminating_snap():
    def never_stop(input: Optional[DataBlock] = None) -> DataFrame:
        pass

    env = make_test_env()
    g = Graph(env)
    node = g.create_node(key="node", snap=never_stop)
    exe = env.get_executable(node)
    result = ExecutionManager(exe).execute()
    assert result.get_output_block(env) is None


def test_non_terminating_snap_with_reference_input():
    @Input("input", reference=True, required=False)
    def never_stop(input: Optional[DataBlock] = None) -> DataFrame:
        # Does not use input but doesn't matter cause reference
        pass

    env = make_test_env()
    g = Graph(env)
    source = g.create_node(
        snap="core.import_dataframe",
        params={"dataframe": pd.DataFrame({"a": range(10)})},
    )
    node = g.create_node(key="node", snap=never_stop, input=source)
    exe = env.get_executable(source)
    # TODO: reference inputs need to log too? (So they know when to update)
    # with env.md_api.begin():
    #     assert env.md_api.count(select(DataBlockLog)) == 1
    result = ExecutionManager(exe).execute()
    exe = env.get_executable(node)
    result = ExecutionManager(exe).execute()
    assert result.get_output_block(env) is None
