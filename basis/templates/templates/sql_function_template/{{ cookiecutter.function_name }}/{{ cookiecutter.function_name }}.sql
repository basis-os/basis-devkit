from __future__ import annotations

from basis import Function, FunctionContext, Block


@Function(namespace="{{ cookiecutter.namespace }}")
# @Input("input", stream=True)
# @Param("param1", datatype="int")
def {{ cookiecutter.function_name }}(ctx: FunctionContext, input: Block):
    df = input.as_dataframe() # Or .as_records()
    return df