import base64
import os
import shutil
from pathlib import Path

import requests_mock
from basis.cli.api import DEFAULT_BASE_URL
from basis.cli.helpers import compress_directory
from tests.cli.base import IS_CI, get_test_command, set_tmp_dir


def test_clone():
    if not IS_CI:
        dr = set_tmp_dir(create_basis_config=True)
        proj_path = Path(dr) / "proj"
        # Create graph
        get_test_command("generate").execute(f"graph {proj_path}", inputs="\n")
        zipf = compress_directory(proj_path)
        shutil.rmtree(proj_path)
        assert not os.path.exists(proj_path / "graph.yml")
        b64_zipf = base64.b64encode(zipf.read())
        command_tester = get_test_command("clone")
        with requests_mock.Mocker() as m:
            m.post(
                DEFAULT_BASE_URL + "graph-versions/download",
                json={"zip": b64_zipf.decode()},
            )
            command_tester.execute(f"mock_name")
        assert os.path.exists(proj_path / "graph.yml")