from __future__ import annotations
from basis.core.declarative.environment import EnvironmentCfg

import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Union

import requests
from basis.core.component import ComponentLibrary, global_library
from basis.core.block import Block, as_managed
from basis.core.declarative.base import FrozenPydanticBase, PydanticBase
from basis.core.declarative.function import (
    DEFAULT_OUTPUT_NAME,
    FunctionSourceFileCfg,
)
from basis.core.declarative.graph import GraphCfg
from basis.core.declarative.interface import BoundInterfaceCfg
from basis.core.environment import Environment
from basis.core.function_interface_manager import bind_inputs
from basis.core.persistence.pydantic import (
    BlockMetadataCfg,
    BlockWithStoredBlocksCfg,
    FunctionLogCfg,
    StoredBlockMetadataCfg,
)
from commonmodel.base import Schema
from dcp.storage.base import Storage
from dcp.utils.common import to_json
from sqlalchemy.sql.expression import select


class ResultHandler(FrozenPydanticBase):
    type: str = "MetadataExecutionResultHandler"
    cfg: Dict = {}


class ExecutionCfg(FrozenPydanticBase):
    environment: EnvironmentCfg
    target_storage: str
    local_storage: Optional[str] = None
    target_data_format: Optional[str] = None
    storages: List[str] = []
    run_until_inputs_exhausted: bool = True
    # TODO: this is a "soft" limit, could imagine a "hard" one too
    execution_timelimit_seconds: Optional[int] = None
    result_handler: ResultHandler = ResultHandler()

    def get_target_storage(self) -> Storage:
        return Storage(self.target_storage)

    def get_local_storage(self) -> Optional[Storage]:
        if self.local_storage is None:
            return None
        return Storage(self.local_storage)

    def get_storages(self) -> List[Storage]:
        return [Storage(s) for s in self.storages]


class PythonException(FrozenPydanticBase):
    error: str
    traceback: str

    @classmethod
    def from_exception(cls, e: Exception):
        tback = traceback.format_exc()
        error = str(e) or type(e).__name__  # MUST evaluate true if there's an error!
        # Traceback can be v large (like in max recursion), so we truncate to 5k chars
        tback = tback[:5000]
        return PythonException(error=error, traceback=tback)


class StreamState(PydanticBase):
    start_block_id: Optional[str] = None
    latest_block_id: Optional[str] = None
    block_count: int = 0

    def mark_latest_record_consumed(self, record: Record):
        return self.mark_progress(record.block)

    def mark_emitted(self, block: Block):
        return self.mark_progress(block)

    def mark_progress(self, block: Block):
        if self.start_block_id is None:
            self.start_block_id = block.id
        self.latest_block_id = block.id


class ExecutionResult(PydanticBase):
    node_key: str
    node_version: str  # TODO: hash of relevant node config (code, params, etc?)
    # function_key: str = None # TODO: unique hash of code and path?
    # params
    stream_statuses: Dict[str, StreamState] = {}
    runtime: str
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    timed_out: bool = False
    schemas_generated: List[Schema] = []
    function_error: Optional[PythonException] = None
    framework_error: Optional[PythonException] = None  # TODO: do we ever use this?
    # records_emitted: Dict[str, List[Dict]] = {}
    # tables_emitted: Dict[str, List[Any]] = {}
    # errors_emitted: Dict[str, List[Any]] = {}
    # states_emitted: Dict[str, List[Dict]] = {}
    # latest_table_emitted: Dict[str, str] = {}
    # latest_record_emitted: Dict[str, str] = {}
    # latest_record_consumed: Dict[str, str] = {}
    # current_state: Dict = {}


class ExecutionResultOld(PydanticBase):
    input_blocks_consumed: Dict[str, List[BlockMetadataCfg]] = {}
    output_blocks_emitted: Dict[str, List[BlockMetadataCfg]] = {}
    stored_blocks_created: Dict[
        str, List[StoredBlockMetadataCfg]
    ] = {}  # Keyed on data block ID
    schemas_generated: List[Schema] = []
    function_error: Optional[PythonException] = None
    framework_error: Optional[PythonException] = None  # TODO: do we ever use this?
    timed_out: bool = False

    def has_error(self) -> bool:
        return self.function_error is not None or self.framework_error is not None

    def latest_stdout_block_emitted(self) -> Optional[BlockMetadataCfg]:
        blocks = self.output_blocks_emitted.get(DEFAULT_OUTPUT_NAME, [])
        return blocks[-1] if blocks else None

    def stdout(self) -> Optional[Block]:
        dbc = self.latest_stdout_block_emitted()
        dbws = BlockWithStoredBlocksCfg(
            **dbc.dict(), stored_blocks=self.stored_blocks_created.get(dbc.id, [])
        )
        return as_managed(dbws)

    def finalize(self, compute_record_counts: bool = True) -> ExecutionResult:
        if compute_record_counts:
            self.compute_record_counts()
        return ExecutionResult(
            input_blocks_consumed=self.input_blocks_consumed,
            output_blocks_emitted={
                n: [b for b in blocks if b.data_is_written]
                for n, blocks in self.output_blocks_emitted.items()
            },
            stored_blocks_created={
                bid: [b for b in blocks if b.data_is_written]
                for bid, blocks in self.stored_blocks_created.items()
            },
            schemas_generated=self.schemas_generated,
            function_error=self.function_error,
            framework_error=self.framework_error,
            timed_out=self.timed_out,
        )

    def compute_record_counts(self):
        for blocks in self.output_blocks_emitted.values():
            for db in blocks:
                if not db.data_is_written:
                    continue
                sdbs = self.stored_blocks_created[db.id]
                assert sdbs
                sdb = sdbs[0]  # TODO: pick most efficient sdb for count?
                db.record_count = (
                    Storage(sdb.storage_url).get_api().record_count(sdb.name)
                )

    def total_record_count_for_output(
        self, stream_name: str = DEFAULT_OUTPUT_NAME
    ) -> Optional[int]:
        cnt = 0
        for db in self.output_blocks_emitted.get(stream_name, []):
            if not db.data_is_written:
                continue
            if db.record_count is None:
                return None
            cnt += db.record_count
        return cnt


@dataclass
class MetadataExecutionResultHandler:
    env: Environment

    def __call__(self, exe: ExecutableCfg, result: ExecutionResult):
        from basis.core.execution.run import save_result

        with self.env.md_api.begin():
            save_result(self.env, exe, result)


# Used for local python runtime
global_metadata_result_handler: Optional[MetadataExecutionResultHandler] = None


def get_global_metadata_result_handler() -> Optional[MetadataExecutionResultHandler]:
    return global_metadata_result_handler


def set_global_metadata_result_handler(handler: MetadataExecutionResultHandler):
    global global_metadata_result_handler
    global_metadata_result_handler = handler


@dataclass
class DebugMetadataExecutionResultHandler:
    def __call__(self, exe: ExecutableCfg, result: ExecutionResult):
        print(result.dict())


@dataclass
class RemoteCallbackMetadataExecutionResultHandler:
    callback_url: str
    headers: Optional[Dict] = None

    def __call__(self, exe: ExecutableCfg, result: ExecutionResult):
        headers = {"Content-Type": "application/json"}
        headers.update(self.headers or {})
        data = {"executable": exe.dict(), "result": result.dict()}
        requests.post(self.callback_url, data=to_json(data), headers=headers)


class ExecutableCfg(FrozenPydanticBase):
    node_key: str
    node_set: List[NodeCfg]
    execution_config: ExecutionCfg
    bound_interface: BoundInterfaceCfg
    # result: ExecutionResult
    library_cfg: Optional[ComponentLibraryCfg] = None
    # source_file_functions: List[FunctionSourceFileCfg] = []