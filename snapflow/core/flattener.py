from typing import Dict
from snapflow.core.models.configuration import GraphCfg, update


def flatten_sub_node(cfg: GraphCfg, parent_key: str) -> GraphCfg:
    sub_node_key = parent_key + "." + cfg.key
    new_inputs = {}
    for input_name, node_key in cfg.get_inputs().items():
        new_inputs[input_name] = parent_key + "." + node_key
    return update(cfg, key=sub_node_key, inputs=new_inputs, input=None)


def update_sub_node_inputs(
    sub_node: GraphCfg, parent_key: str, input_name: str, input_node_key: str
) -> GraphCfg:
    sub_input_name = parent_key + "." + input_name
    existing_sub_inputs = sub_node.get_inputs()
    sub_input_path = sub_input_name[len(sub_node.key + ".") :] or "stdin"
    existing_sub_inputs.update({sub_input_path: input_node_key})
    sub_node = update(sub_node, inputs=existing_sub_inputs, input=None)
    return sub_node


def update_node_inputs(n: GraphCfg, stdout_lookup: Dict[str, str]) -> GraphCfg:
    inputs = n.get_inputs()
    new_inputs = {}
    for name, input_key in inputs.items():
        new_inputs[name] = stdout_lookup.get(input_key, input_key)
    if new_inputs != n.inputs:
        n = update(n, inputs=new_inputs, input=None)
    return n


def flatten_graph_config(config: GraphCfg) -> GraphCfg:
    while not config.is_flattened():
        stdout_lookup = {}
        flattened_nodes: Dict[str, GraphCfg] = {}
        for parent in config.nodes:
            if parent.nodes:
                if parent.stdout_key:
                    stdout_lookup[parent.key] = parent.key + "." + parent.stdout_key
                for sub_node in parent.nodes:
                    new_sub_node = flatten_sub_node(sub_node, parent.key)
                    flattened_nodes[new_sub_node.key] = new_sub_node
            else:
                flattened_nodes[parent.key] = parent
        for parent in config.nodes:
            if not parent.nodes:
                continue
            for input_name, input_node_key in parent.get_inputs().items():
                print("finding", parent.key, input_name)
                if input_name == "stdin":
                    assert (
                        parent.stdin_key is not None
                    ), "Must specify stdin when multiple nodes"  # TODO: or continue?
                    input_name = parent.stdin_key
                # input_name_node = input_name.split(".")[0]
                # input_name_path = ".".join(input_name.split(".")[1:]) or "stdin"
                sub_input_name = parent.key + "." + input_name
                for key, sub_node in flattened_nodes.items():
                    print("\t", sub_input_name, key)
                    if sub_input_name.startswith(key):
                        sub_node = update_sub_node_inputs(
                            sub_node, parent.key, input_name, input_node_key
                        )
                        flattened_nodes[key] = sub_node
                        break
        resolved_nodes = []
        for n in flattened_nodes.values():
            new_node = update_node_inputs(n, stdout_lookup)
            resolved_nodes.append(new_node)
        config = update(config, nodes=resolved_nodes)
    return config
