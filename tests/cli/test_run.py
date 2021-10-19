import requests_mock
from basis.cli.api import DEFAULT_BASE_URL
from tests.cli.base import IS_CI, get_test_command, set_tmp_dir


def test_info():
    if not IS_CI:
        dr = set_tmp_dir(create_basis_config=True)
        command_tester = get_test_command("run")
        with requests_mock.Mocker() as m:
            m.post(
                DEFAULT_BASE_URL + "nodes/run",
                json={"name": "name"},
            )
            command_tester.execute(f"node name")
            assert "name" in command_tester.io.fetch_output()