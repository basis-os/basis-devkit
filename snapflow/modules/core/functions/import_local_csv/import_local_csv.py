from __future__ import annotations

from typing import Optional

from dcp.data_format.formats import (
    CsvFileObjectFormat,
    DataFrameFormat,
    JsonLinesFileObjectFormat,
)
from snapflow.core.execution.execution import FunctionContext
from snapflow.core.function import Function, Input, Output, Param
from snapflow.core.streams import Stream
from snapflow.utils.typing import T


@Function(namespace="core", display_name="Import local CSV")
@Param("path", datatype="str")
@Param("schema", datatype="str", required=False)
def import_local_csv(ctx: FunctionContext):
    imported = ctx.get_state_value("imported")
    if imported:
        return
        # Static resource, if already emitted, return
    path = ctx.get_param("path")
    f = open(path)
    ctx.emit_state_value("imported", True)
    schema = ctx.get_param("schema")
    ctx.emit(f, data_format=CsvFileObjectFormat, schema=schema)
