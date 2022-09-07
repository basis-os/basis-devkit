from __future__ import annotations

from patterns.cli.services.api import Endpoints, post_for_json


def trigger_node(
    graph_uid: str,
    node_id: str,
    execution_type: str,
) -> list[dict]:
    return post_for_json(
        Endpoints.trigger_node(graph_uid, node_id),
        json={
            "execution_type": execution_type,
        },
    )
