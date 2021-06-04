from __future__ import annotations
from snapflow.core.storage import ensure_data_block_on_storage_cfg
from snapflow.core.declarative.data_block import (
    DataBlockMetadataCfg,
    StoredDataBlockMetadataCfg,
)
from snapflow.core.declarative.base import FrozenPydanticBase, PydanticBase
from snapflow.core.component import ComponentLibrary

import traceback
from collections import abc, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from io import BufferedIOBase, BytesIO, IOBase, RawIOBase
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
)

import dcp
import sqlalchemy
from commonmodel.base import AnySchema, Schema, SchemaLike, SchemaTranslation
from dcp.data_format.base import DataFormat, get_format_for_nickname
from dcp.data_format.handler import get_handler_for_name, infer_format_for_name
from dcp.storage.base import FileSystemStorageClass, MemoryStorageClass, Storage
from dcp.utils.common import rand_str, utcnow
from loguru import logger
from snapflow.core.data_block import (
    Alias,
    DataBlock,
    DataBlockMetadata,
    ManagedDataBlock,
    StoredDataBlockMetadata,
    get_datablock_id,
    get_stored_datablock_id,
)
from snapflow.core.declarative.dataspace import DataspaceCfg
from snapflow.core.declarative.execution import (
    ExecutableCfg,
    ExecutionCfg,
    ExecutionResult,
    NodeInputCfg,
)
from snapflow.core.declarative.function import DEFAULT_OUTPUT_NAME
from snapflow.core.declarative.graph import GraphCfg
from snapflow.core.environment import Environment
from snapflow.core.function import (
    DataFunction,
    DataInterfaceType,
    InputExhaustedException,
)
from snapflow.core.function_interface_manager import BoundInput, BoundInterface
from snapflow.core.metadata.api import MetadataApi
from snapflow.core.state import DataBlockLog, DataFunctionLog, Direction, get_state
from snapflow.core.typing.casting import cast_to_realized_schema
from snapflow.utils.output import cf, error_symbol, success_symbol
from sqlalchemy.sql.expression import select


if TYPE_CHECKING:
    from snapflow.core.streams import DataBlockStream, StreamBuilder
    from snapflow.core.function_interface_manager import (
        BoundInput,
        BoundInterface,
    )


class DataFunctionContext(FrozenPydanticBase):
    dataspace: DataspaceCfg
    library: ComponentLibrary
    function: DataFunction
    result: ExecutionResult
    node: GraphCfg
    executable: ExecutableCfg
    inputs: Dict[str, BoundInput]
    bound_interface: BoundInterface
    function_log: DataFunctionLog
    execution_config: ExecutionCfg
    execution_start_time: Optional[datetime] = None

    """
    TODO:
        - create aliases at appropriate time?
            if sdb is not None and sdb.data_is_written:
                self.create_alias(sdb)
    """

    @contextmanager
    def as_tmp_local_object(self, obj: Any) -> str:
        tmp_name = "_tmp_obj_" + rand_str()
        self.execution_config.get_local_storage().get_api().put(tmp_name, obj)
        yield tmp_name
        self.execution_config.get_local_storage.get_api().remove(tmp_name)

    def get_function_args(self) -> Tuple[List, Dict]:
        function_args = []
        if self.bound_interface.interface.uses_context:
            function_args.append(self)
        function_inputs = self.bound_interface.inputs_as_kwargs()
        function_kwargs = function_inputs
        function_params = self.get_params()
        assert not set(function_params) & set(function_inputs)
        function_kwargs.update(function_params)
        return (function_args, function_kwargs)

    def get_param(self, key: str, default: Any = None) -> Any:
        if default is None:
            try:
                default = self.function.get_param(key).default
            except KeyError:
                pass
        return self.node.params.get(key, default)

    def get_params(self, defaults: Dict[str, Any] = None) -> Dict[str, Any]:
        final_params = {p.name: p.default for p in self.function.params.values()}
        final_params.update(defaults or {})
        final_params.update(self.node.params)
        return final_params

    def get_state_value(self, key: str, default: Any = None) -> Any:
        assert isinstance(self.function_log.node_end_state, dict)
        return self.function_log.node_end_state.get(key, default)

    def get_state(self) -> Dict[str, Any]:
        return self.function_log.node_end_state

    def emit_state_value(self, key: str, new_value: Any):
        new_state = self.function_log.node_end_state.copy()
        new_state[key] = new_value
        self.function_log.node_end_state = new_state

    def emit_state(self, new_state: Dict):
        self.function_log.node_end_state = new_state

    def emit(
        self,
        records_obj: Any = None,
        name: str = None,
        storage: Storage = None,
        stream: str = DEFAULT_OUTPUT_NAME,
        data_format: DataFormat = None,
        schema: SchemaLike = None,
        update_state: Dict[str, Any] = None,
        replace_state: Dict[str, Any] = None,
    ):
        assert records_obj is not None or (
            name is not None and storage is not None
        ), "Emit takes either records_obj, or name and storage"
        if schema is not None:
            schema = self.library.get_schema(schema)
        if data_format is not None:
            if isinstance(data_format, str):
                data_format = get_format_for_nickname(data_format)
        self.handle_emit(
            records_obj,
            name,
            storage,
            output=stream,
            data_format=data_format,
            schema=schema,
        )
        if update_state is not None:
            for k, v in update_state.items():
                self.emit_state_value(k, v)
        if replace_state is not None:
            self.emit_state(replace_state)
        # Commit input blocks to db as well, to save progress
        self.log_processed_input_blocks()

    # def create_alias(self, sdb: StoredDataBlockMetadata) -> Optional[Alias]:
    #     self.metadata_api.flush([sdb.data_block, sdb])
    #     alias = sdb.create_alias(self.env, self.node.get_alias())
    #     self.metadata_api.flush([alias])
    #     return alias

    def create_stored_datablock(self) -> StoredDataBlockMetadataCfg:
        block = DataBlockMetadataCfg(
            id=get_datablock_id(),
            inferred_schema=None,
            nominal_schema=None,
            realized_schema=AnySchema,
            record_count=None,
            created_by_node_key=self.node.key,
        )
        sdb = StoredDataBlockMetadataCfg(  # type: ignore
            id=get_stored_datablock_id(),
            data_block=block,
            storage_url=self.execution_config.target_storage,
            data_format=None,
            data_is_written=False,
        )
        return sdb

    def get_stored_datablock_for_output(
        self, output: str
    ) -> StoredDataBlockMetadataCfg:
        db = self.result.output_blocks_emitted.get(output)
        if db is None:
            sdb = self.create_stored_datablock()
            db = sdb.data_block
            self.result.output_blocks_emitted[output] = sdb.data_block
            self.result.stored_blocks_created.setdefault(db.id, []).append(sdb)
        sdbs = self.result.stored_blocks_created[db.id]
        for sdb in sdbs:
            if sdb.storage == self.execution_config.target_storage:
                return sdb
        return sdbs[0]

    def handle_emit(
        self,
        records_obj: Any = None,
        name: str = None,
        storage: Storage = None,
        output: str = DEFAULT_OUTPUT_NAME,
        data_format: DataFormat = None,
        schema: SchemaLike = None,
    ):
        logger.debug(
            f"HANDLING EMITTED OBJECT (of type '{type(records_obj).__name__}')"
        )
        # TODO: can i return an existing DataBlock? Or do I need to create a "clone"?
        #   Answer: ok to return as is (just mark it as 'output' in DBL)
        if isinstance(records_obj, StoredDataBlockMetadata):
            # TODO is it in local storage tho? we skip conversion below...
            # This is just special case right now to support SQL function
            # Will need better solution for explicitly creating DB/SDBs inside of functions
            return records_obj
        elif isinstance(records_obj, DataBlockMetadata):
            raise NotImplementedError
        elif isinstance(records_obj, ManagedDataBlock):
            raise NotImplementedError
        nominal_output_schema = schema
        if nominal_output_schema is None:
            nominal_output_schema = (
                self.bound_interface.resolve_nominal_output_schema()
            )  # TODO: could check output to see if it is LocalRecords with a schema too?
        if nominal_output_schema is not None:
            nominal_output_schema = self.library.get_schema(nominal_output_schema)
        sdb = self.get_stored_datablock_for_output(output)
        db = sdb.data_block
        if (
            db.nominal_schema_key
            and db.nominal_schema(self.library).key != nominal_output_schema.key
        ):
            raise Exception(
                "Mismatch nominal schemas {db.nominal_schema_key} - {nominal_output_schema.key}"
            )
        db.nominal_schema_key = nominal_output_schema.key
        if records_obj is not None:
            name, storage = self.handle_python_object(records_obj)
            if nominal_output_schema is not None:
                # TODO: still unclear on when and why to do this cast
                handler = get_handler_for_name(name, storage)
                handler().cast_to_schema(name, storage, nominal_output_schema)
        assert name is not None
        assert storage is not None
        self.append_records_to_stored_datablock(name, storage, sdb)
        sdb.data_is_written = True
        return sdb

    def handle_python_object(self, obj: Any) -> Tuple[str, Storage]:
        name = "_tmp_obj_" + rand_str(10)
        if isinstance(obj, IOBase):
            # Handle file-like by writing to disk first
            file_storages = [
                s
                for s in self.execution_config.get_storages()
                if s.storage_engine.storage_class == FileSystemStorageClass
            ]
            if not file_storages:
                raise Exception(
                    "File-like object returned but no file storage provided."
                    "Add a file storage to the environment: `env.add_storage('file:///....')`"
                )
            if self.execution_config.get_target_storage() in file_storages:
                storage = self.execution_config.get_target_storage()
            else:
                storage = file_storages[0]
            mode = "w"
            if isinstance(obj, (RawIOBase, BufferedIOBase)):
                mode = "wb"
            with storage.get_api().open(name, mode) as f:
                for s in obj:
                    f.write(s)
        else:
            storage = self.execution_config.get_local_storage()
            storage.get_api().put(name, obj)
        return name, storage

    def add_schema(self, schema: Schema):
        if schema not in self.result.schemas_generated:
            self.result.schemas_generated.append(schema)

    def resolve_new_object_with_data_block(
        self, sdb: StoredDataBlockMetadataCfg, name: str, storage: Storage
    ):
        # TOO expensive to infer schema every time, so just do first time
        if sdb.data_block.realized_schema(self.library).key in (
            None,
            "Any",
            "core.Any",
        ):
            handler = get_handler_for_name(name, storage)
            inferred_schema = handler().infer_schema(name, storage)
            logger.debug(
                f"Inferred schema: {inferred_schema.key} {inferred_schema.fields_summary()}"
            )
            self.add_schema(inferred_schema)
            # Cast to nominal if no existing realized schema
            realized_schema = cast_to_realized_schema(
                inferred_schema=inferred_schema,
                nominal_schema=sdb.data_block.nominal_schema,
            )
            logger.debug(
                f"Realized schema: {realized_schema.key} {realized_schema.fields_summary()}"
            )
            self.add_schema(realized_schema)
            sdb.data_block.realized_schema_key = realized_schema.key
        #     # If already a realized schema, conform new inferred schema to existing realized
        #     realized_schema = cast_to_realized_schema(
        #         self.env,
        #         inferred_schema=inferred_schema,
        #         nominal_schema=sdb.data_block.realized_schema(self.env),
        #     )
        if sdb.data_block.nominal_schema_key:
            logger.debug(
                f"Nominal schema: {sdb.data_block.nominal_schema(self.library).key} {sdb.data_block.nominal_schema(self.library).fields_summary()}"
            )

    def append_records_to_stored_datablock(
        self, name: str, storage: Storage, sdb: StoredDataBlockMetadataCfg
    ):
        self.resolve_new_object_with_data_block(sdb, name, storage)
        if sdb.data_format is None:
            if self.execution_config.target_data_format:
                sdb.data_format = get_format_for_nickname(
                    self.execution_config.target_data_format
                )
            else:
                sdb.data_format = sdb.storage.storage_engine.get_natural_format()
            # fmt = infer_format_for_name(name, storage)
            # # if sdb.data_format and sdb.data_format != fmt:
            # #     raise Exception(f"Format mismatch {fmt} - {sdb.data_format}")
            # if fmt is None:
            #     raise Exception(f"Could not infer format {name} on {storage}")
            # sdb.data_format = fmt
        # TODO: make sure this handles no-ops (empty object, same storage)
        # TODO: copy or alias? sometimes we are just moving temp obj to new name, dont need copy
        # to_name = sdb.get_name_for_storage()
        # if storage == sdb.storage:
        #     # Same storage
        #     if name == to_name:
        #         # Nothing to do
        #         logger.debug("Output already on storage with same name, nothing to do")
        #         return
        #     else:
        #         # Same storage, just new name
        #         # TODO: should be "rename" ideally (as it is if tmp gets deleted we lose it)
        #         logger.debug("Output already on storage, creating alias")
        #         storage.get_api().create_alias(name, to_name)
        #         return
        logger.debug(
            f"Copying output from {name} {storage} to {sdb.name} {sdb.storage} ({sdb.data_format})"
        )
        result = dcp.copy(
            from_name=name,
            from_storage=storage,
            to_name=sdb.name,
            to_storage=sdb.storage,
            to_format=sdb.data_format,
            available_storages=self.execution_config.get_storages(),
            if_exists="append",
        )
        logger.debug(f"Copied {result}")
        logger.debug(f"REMOVING NAME {name}")
        storage.get_api().remove(name)

    # TODO: ensure we're doing this somewhere (hopefully as managed stream step?)
    # def log_processed_input_blocks(self):
    #     for node_input in self.bound_interface.inputs.values():
    #         if node_input.bound_stream is not None:
    #             for db in node_input.bound_stream.get_emitted_blocks():
    #                 self.input_blocks_processed[node_input.name].add(db)

    def should_continue(self) -> bool:
        """
        Long running functions should check this function periodically
        to honor time limits.
        """
        if (
            not self.execution_config.execution_timelimit_seconds
            or not self.execution_start_time
        ):
            return True
        seconds_elapsed = (utcnow() - self.execution_start_time).total_seconds()
        should = seconds_elapsed < self.execution_config.execution_timelimit_seconds
        if not should:
            logger.debug(
                f"Execution timed out after {self.execution_config.execution_timelimit_seconds} seconds"
            )
        return should

    # def as_execution_result(self) -> ExecutionResult:
    #     input_block_counts = {}
    #     for input_name, dbs in self.input_blocks_processed.items():
    #         input_block_counts[input_name] = len(dbs)
    #     output_blocks = {}
    #     for output_name, sdb in self.output_blocks_emitted.items():
    #         if not sdb.data_is_written:
    #             continue
    #         alias = sdb.get_alias(self.env)
    #         output_blocks[output_name] = {
    #             "id": sdb.data_block_id,
    #             "record_count": sdb.record_count(),
    #             "alias": alias.name if alias else None,
    #         }
    #     return ExecutionResult(
    #         inputs_bound=list(self.bound_interface.inputs_as_kwargs().keys()),
    #         non_reference_inputs_bound=[
    #             i.name for i in self.bound_interface.non_reference_bound_inputs()
    #         ],
    #         input_block_counts=input_block_counts,
    #         output_blocks=output_blocks,
    #         error=self.function_log.error.get("error")
    #         if isinstance(self.function_log.error, dict)
    #         else None,
    #         traceback=self.function_log.error.get("traceback")
    #         if isinstance(self.function_log.error, dict)
    #         else None,
    #     )