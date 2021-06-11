from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Iterator, Optional

import pandas as pd
import pytest
from commonmodel.base import create_quick_schema
from dcp.data_format.formats.memory.dataframe import DataFrameFormat
from dcp.data_format.formats.memory.records import Records, RecordsFormat
from dcp.storage.database.engines.postgres import PostgresDatabaseStorageApi
from dcp.storage.database.utils import get_tmp_sqlite_db_url
from loguru import logger
from pandas._testing import assert_almost_equal
from snapflow import DataBlock, DataFunctionContext, datafunction
from snapflow.core.declarative.dataspace import DataspaceCfg
from snapflow.core.declarative.graph import GraphCfg
from snapflow.core.environment import Environment
from snapflow.core.sql.sql_function import sql_function_factory
from snapflow.modules import core
from sqlalchemy import select
from tests.test_e2e import Customer
from tests.utils import get_stdout_block

IS_CI = os.environ.get("CI")

# logger.enable("snapflow")


@datafunction
def funky_source(
    ctx: DataFunctionContext, batches: int, fail: bool = False
) -> Records[Customer]:
    # Gives different schema on each call
    runs = ctx.get_state_value("run_number", 0)
    records = [
        {"name": f"name{n}", "joined": datetime(2000, 1, n + 1), "Meta data": None,}
        for n in range(10)
    ]
    if runs == 1:
        # New field
        records = [
            {
                "name": f"name{n}",
                "joined": datetime(2000, 1, n + 1),
                "Meta data": {"idx": n},
                "new_field": "suprise!",
            }
            for n in range(10)
        ]
    if runs == 2:
        # Different / bad datatype
        records = [
            {
                "name": f"name{n}",
                "joined": None,
                "Meta data": {"idx": n},
                "new_field": "suprise!",
            }
            for n in range(10)
        ]
    if runs == 3:
        # null field
        records = [
            {
                "name": None,
                "joined": datetime(2000, 1, n + 1),
                "Meta data": {"idx": n},
                "new_field": "suprise!",
            }
            for n in range(10)
        ]
    if runs > 3:
        # missing field
        records = [
            {"joined": datetime(2000, 1, n + 1), "Meta data": {"idx": n},}
            for n in range(10)
        ]
    ctx.emit_state_value("run_number", runs + 1)
    return records


@contextmanager
def get_env(key="_test", use_sqlite=False):
    if use_sqlite or IS_CI:
        db_url = get_tmp_sqlite_db_url()
        env = Environment(
            DataspaceCfg(
                key=key, metadata_storage=db_url, storages=[get_tmp_sqlite_db_url()]
            )
        )
        env.add_module(core)
        env.add_schema(Customer)
        yield env
    else:
        with PostgresDatabaseStorageApi.temp_local_database() as db_url:
            env = Environment(
                DataspaceCfg(
                    key=key, metadata_storage=get_tmp_sqlite_db_url(), storages=[db_url]
                )
            )
            env.add_module(core)
            env.add_schema(Customer)
            yield env


def test_source():
    with get_env() as env:
        s = env._local_python_storage
        source = GraphCfg(key="source", function=funky_source.key)
        g = GraphCfg(nodes=[source])
        # Run first time
        results = env.run_node("source", graph=g, target_storage=s)
        block = get_stdout_block(results)
        assert block.nominal_schema_key == "Customer"
        assert len(env.get_schema(block.realized_schema_key).fields) == 3
        records = block.as_records()
        assert len(records) == 10
        assert len(records[0]) == 3
        # Run again
        results = env.run_node("source", graph=g, target_storage=s)
        block = get_stdout_block(results)
        assert block.nominal_schema_key == "Customer"
        assert len(env.get_schema(block.realized_schema_key).fields) == 4


def test_accumulate():
    source = GraphCfg(key="source", function=funky_source.key)
    accumulate = GraphCfg(key="accumulate", function="core.accumulator", input="source")
    accumulate_sql = GraphCfg(
        key="accumulate", function="core.accumulator_sql", input="source"
    )

    def run_accumulate(env, g, s):
        results = env.produce("accumulate", graph=g, target_storage=s)
        block = get_stdout_block(results)
        records = block.as_records()
        assert len(records) == 10
        assert len(records[0]) == 3
        # Run second time
        results = env.produce("accumulate", graph=g, target_storage=s)
        block = get_stdout_block(results)
        records = block.as_records()
        assert len(records) == 20
        assert len(records[0]) == 4
        # Run third time
        results = env.produce("accumulate", graph=g, target_storage=s)
        block = get_stdout_block(results)
        records = block.as_records()
        assert len(records) == 30
        assert len(records[0]) == 4
        # Run fourth time
        results = env.produce("accumulate", graph=g, target_storage=s)
        block = get_stdout_block(results)
        records = block.as_records()
        assert len(records) == 40
        assert len(records[0]) == 4

    # Test python version
    with get_env() as env:
        s = env._local_python_storage
        run_accumulate(env, GraphCfg(nodes=[source, accumulate]), s)
    # Test database version (sqlite if CI, postgres if local dev)
    with get_env() as env:
        dbs = env.get_storages()[0]
        run_accumulate(env, GraphCfg(nodes=[source, accumulate_sql]), dbs)
    if not IS_CI:
        # If local, also test sqlite!
        with get_env(use_sqlite=True) as env:
            dbs = env.get_storages()[0]
            run_accumulate(env, GraphCfg(nodes=[source, accumulate_sql]), dbs)

