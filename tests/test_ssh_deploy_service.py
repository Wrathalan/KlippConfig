import pytest

from app.services.ssh_deploy import SSHDeployError, SSHDeployService


class _DummyClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_run_remote_command_success(monkeypatch) -> None:
    service = SSHDeployService.__new__(SSHDeployService)
    client = _DummyClient()
    captured: dict[str, object] = {}

    def fake_connect(host, port, username, password=None, key_path=None):  # noqa: ANN001
        captured["connect"] = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "key_path": key_path,
        }
        return client

    def fake_run_command(_client, command, timeout=30.0):  # noqa: ANN001
        captured["run"] = {"command": command, "timeout": timeout}
        return "ok-output"

    monkeypatch.setattr(service, "connect", fake_connect)
    monkeypatch.setattr(service, "run_command", fake_run_command)

    output = service.run_remote_command(
        host="printer.local",
        port=22,
        username="pi",
        password="secret",
        key_path=None,
        command="  sudo systemctl restart klipper  ",
        timeout=45.0,
    )

    assert output == "ok-output"
    assert captured["connect"] == {
        "host": "printer.local",
        "port": 22,
        "username": "pi",
        "password": "secret",
        "key_path": None,
    }
    assert captured["run"] == {
        "command": "sudo systemctl restart klipper",
        "timeout": 45.0,
    }
    assert client.closed is True


def test_run_remote_command_rejects_empty_command() -> None:
    service = SSHDeployService.__new__(SSHDeployService)
    with pytest.raises(SSHDeployError, match="Remote command is empty."):
        service.run_remote_command(
            host="printer.local",
            port=22,
            username="pi",
            command="   ",
        )


def test_run_remote_command_surfaces_command_failure(monkeypatch) -> None:
    service = SSHDeployService.__new__(SSHDeployService)
    client = _DummyClient()

    def fake_connect(*_args, **_kwargs):  # noqa: ANN001
        return client

    def fake_run_command(*_args, **_kwargs):  # noqa: ANN001
        raise SSHDeployError("Remote command failed: test")

    monkeypatch.setattr(service, "connect", fake_connect)
    monkeypatch.setattr(service, "run_command", fake_run_command)

    with pytest.raises(SSHDeployError, match="Remote command failed: test"):
        service.run_remote_command(
            host="printer.local",
            port=22,
            username="pi",
            command="echo test",
        )

    assert client.closed is True
