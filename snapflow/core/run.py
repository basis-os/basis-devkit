from __future__ import annotations
from snapflow.core.execution import ExecutionManager
from snapflow.core.component import ComponentLibrary, global_library
from snapflow.core.function_interface_manager import get_bound_interface
from snapflow.core.declarative.context import DataFunctionContext

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


from dcp.utils.common import rand_str, utcnow
from loguru import logger
from snapflow.core.persisted.data_block import (
    Alias,
    DataBlockMetadata,
    StoredDataBlockMetadata,
    get_datablock_id,
    get_stored_datablock_id,
)
from snapflow.core.declarative.dataspace import DataspaceCfg
from snapflow.core.declarative.execution import (
    ExecutableCfg,
    ExecutionCfg,
    ExecutionResult,
)
from snapflow.core.declarative.function import DEFAULT_OUTPUT_NAME
from snapflow.core.declarative.graph import GraphCfg
from snapflow.core.environment import Environment
from snapflow.core.function import (
    DataFunction,
    DataInterfaceType,
    InputExhaustedException,
)
from snapflow.core.metadata.api import MetadataApi
from snapflow.core.persisted.state import (
    DataBlockLog,
    DataFunctionLog,
    Direction,
    get_state,
)
from snapflow.utils.output import cf, error_symbol, success_symbol
from sqlalchemy.sql.expression import select


def run_dataspace(ds: DataspaceCfg):
    env = Environment(ds)
    env.run_graph(ds.graph)


class ImproperlyStoredDataBlockException(Exception):
    pass


# TODO: use this
def validate_data_blocks(env: Environment):
    # TODO: More checks?
    env.md_api.flush()
    for obj in env.md_api.active_session.identity_map.values():
        if isinstance(obj, DataBlockMetadata):
            urls = set([sdb.storage_url for sdb in obj.stored_data_blocks])
            if all(u.startswith("python") for u in urls):
                fmts = set([sdb.data_format for sdb in obj.stored_data_blocks])
                if all(not f.is_storable() for f in fmts):
                    raise ImproperlyStoredDataBlockException(
                        f"DataBlock {obj} is not properly stored (no storeable format(s): {fmts})"
                    )


def prepare_executable(
    env: Environment, cfg: ExecutionCfg, node: GraphCfg, graph: GraphCfg
) -> ExecutableCfg:
    with env.md_api.begin():
        md = env.md_api
        try:
            bound_interface = get_bound_interface(env, cfg, node, graph)
        except InputExhaustedException as e:
            logger.debug(f"Inputs exhausted {e}")
            raise e
            # return ExecutionResult.empty()
        node_state_obj = get_state(env, node.key)
        if node_state_obj is None:
            node_state = {}
        else:
            node_state = node_state_obj.state or {}

        function_log = DataFunctionLog(  # type: ignore
            node_key=node.key,
            node_start_state=node_state.copy(),  # {k: v for k, v in node_state.items()},
            node_end_state=node_state,
            function_key=node.function,
            function_params=node.params,
            # runtime_url=self.current_runtime.url,
            started_at=utcnow(),
        )
        node_state_obj.latest_log = function_log
        md.add(function_log)
        md.add(node_state_obj)
        md.flush([function_log, node_state_obj])

        return ExecutableCfg(
            node_key=node.key,
            graph=graph,
            execution_config=cfg,
            bound_interface=bound_interface,
            function_log=function_log,
        )
        # Validate local memory objects: Did we leave any non-storeables hanging?
        # validate_data_blocks(self.env)

    # # TODO: goes somewhere else
    #     except Exception as e:
    #         # Don't worry about exhaustion exceptions
    #         if not isinstance(e, InputExhaustedException):
    #             logger.debug(f"Error running node:\n{traceback.format_exc()}")
    #             function_log.set_error(e)
    #             function_log.persist_state(self.env)
    #             function_log.completed_at = utcnow()
    #             # TODO: should clean this up so transaction surrounds things that you DO
    #             #       want to rollback, obviously
    #             # md.commit()  # MUST commit here since the re-raised exception will issue a rollback
    #             if (
    #                 self.exe.execution_config.dataspace.snapflow.abort_on_function_error
    #             ):  # TODO: from call or env
    #                 raise e
    #     finally:
    #         function_ctx.finish_execution()
    #         # Persist state on success OR error:
    #         function_log.persist_state(self.env)
    #         function_log.completed_at = utcnow()

    #     with self.start_function_run(self.node, bound_interface) as function_ctx:
    #         # function = executable.compiled_function.function
    #         # local_vars = locals()
    #         # if hasattr(function, "_locals"):
    #         #     local_vars.update(function._locals)
    #         # exec(function.get_source_code(), globals(), local_vars)
    #         # output_obj = local_vars[function.function_callable.__name__](
    #         function_args, function_kwargs = function_ctx.get_function_args()
    #         output_obj = function_ctx.function.function_callable(
    #             *function_args, **function_kwargs,
    #         )
    #         if output_obj is not None:
    #             self.emit_output_object(output_obj, function_ctx)
    #     result = function_ctx.as_execution_result()
    #     # TODO: update node state block counts?
    # logger.debug(f"EXECUTION RESULT {result}")
    # return result

    # def emit_output_object(
    #     self, output_obj: DataInterfaceType, function_ctx: DataFunctionContext,
    # ):
    #     assert output_obj is not None
    #     if isinstance(output_obj, abc.Generator):
    #         output_iterator = output_obj
    #     else:
    #         output_iterator = [output_obj]
    #     i = 0
    #     for output_obj in output_iterator:
    #         logger.debug(output_obj)
    #         i += 1
    #         function_ctx.emit(output_obj)

    # def log_execution_result(self, result: ExecutionResult):
    #     self.logger.log("Inputs: ")
    #     if result.input_block_counts:
    #         self.logger.log("\n")
    #         with self.logger.indent():
    #             for input_name, cnt in result.input_block_counts.items():
    #                 self.logger.log(f"{input_name}: {cnt} block(s) processed\n")
    #     else:
    #         if not result.non_reference_inputs_bound:
    #             self.logger.log_token("n/a\n")
    #         else:
    #             self.logger.log_token("None\n")
    #     self.logger.log("Outputs: ")
    #     if result.output_blocks:
    #         self.logger.log("\n")
    #         with self.logger.indent():
    #             for output_name, block_summary in result.output_blocks.items():
    #                 self.logger.log(f"{output_name}:")
    #                 cnt = block_summary["record_count"]
    #                 alias = block_summary["alias"]
    #                 if cnt is not None:
    #                     self.logger.log_token(f" {cnt} records")
    #                 self.logger.log_token(
    #                     f" {alias} " + cf.dimmed(f"({block_summary['id']})\n")  # type: ignore
    #                 )
    #     else:
    #         self.logger.log_token("None\n")


def run(
    env: Environment, exe: ExecutableCfg, to_exhaustion: bool = True
) -> Optional[ExecutionResult]:
    # TODO: support other runtimes
    cum_result = ExecutionResult()
    em = ExecutionManager(exe)
    result = em.execute()
    # while True:
    #     try:
    #         result = em.execute()
    #     except InputExhaustedException:
    #         return cum_result
    #     cum_result.add_result(result)
    #     if (
    #         not to_exhaustion or not result.non_reference_inputs_bound
    #     ):  # TODO: We just run no-input DFs (sources) once no matter what
    #         # (they are responsible for creating their own generators)
    #         break
    #     if cum_result.error:
    #         break
    # return cum_result


def get_latest_output(env: Environment, node: GraphCfg) -> Optional[DataBlock]:
    from snapflow.core.persisted.data_block import DataBlockMetadata

    with env.metadata_api.begin():
        block: DataBlockMetadata = (
            env.md_api.execute(
                select(DataBlockMetadata)
                .join(DataBlockLog)
                .join(DataFunctionLog)
                .filter(
                    DataBlockLog.direction == Direction.OUTPUT,
                    DataFunctionLog.node_key == node.key,
                )
                .order_by(DataBlockLog.created_at.desc())
            )
            .scalars()
            .first()
        )
        if block is None:
            return None
    return block.as_managed_data_block(env)
