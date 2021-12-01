from basis.node.node import node, OutputStream, InputStream, Parameter


@node
def passthrough_node(
    source_stream=InputStream(description="in desc", schema="TestSchema",),
    optional_stream=InputStream(required=False),
    passthrough_stream=OutputStream(description="out desc", schema="TestSchema2"),
    explicit_param=Parameter(description="param desc", type="bool", default=False),
    plain_param=Parameter,
):
    pass