from __future__ import annotations

import posixpath
import shlex
import stat
import tempfile
from pathlib import Path
from typing import Any

from app.domain.models import RenderedPack

try:
    import paramiko
except ModuleNotFoundError:  # pragma: no cover - handled via runtime error
    paramiko = None  # type: ignore[assignment]


class SSHDeployError(Exception):
    """Raised when SSH connectivity or deployment fails."""


class SSHDeployService:
    def __init__(self) -> None:
        if paramiko is None:
            raise SSHDeployError(
                "Missing dependency 'paramiko'. Install project dependencies and retry."
            )

    @staticmethod
    def _create_client() -> "paramiko.SSHClient":
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return client

    def connect(
        self,
        host: str,
        port: int,
        username: str,
        password: str | None = None,
        key_path: str | None = None,
        timeout: float = 10.0,
    ) -> "paramiko.SSHClient":
        client = self._create_client()
        kwargs: dict[str, Any] = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": timeout,
        }
        if key_path:
            kwargs["key_filename"] = key_path
            if password:
                kwargs["passphrase"] = password
        elif password:
            kwargs["password"] = password
        else:
            kwargs["look_for_keys"] = True
            kwargs["allow_agent"] = True

        try:
            client.connect(**kwargs)
        except Exception as exc:  # noqa: BLE001
            client.close()
            raise SSHDeployError(f"SSH connection failed: {exc}") from exc
        return client

    def test_connection(
        self,
        host: str,
        port: int,
        username: str,
        password: str | None = None,
        key_path: str | None = None,
    ) -> tuple[bool, str]:
        client = self.connect(host, port, username, password, key_path)
        try:
            output = self.run_command(client, "uname -a")
            output = output.strip() or "Connection established."
            return True, output
        finally:
            client.close()

    @staticmethod
    def run_command(client: "paramiko.SSHClient", command: str, timeout: float = 30.0) -> str:
        try:
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            out_text = stdout.read().decode("utf-8", errors="replace")
            err_text = stderr.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise SSHDeployError(f"Failed to run remote command '{command}': {exc}") from exc
        if exit_code != 0:
            detail = err_text.strip() or out_text.strip() or f"exit code {exit_code}"
            raise SSHDeployError(f"Remote command failed: {command} ({detail})")
        return out_text

    @staticmethod
    def _normalize_remote_dir(remote_dir: str) -> str:
        remote = remote_dir.strip()
        if not remote:
            raise SSHDeployError("Remote config directory is empty.")
        return remote

    def _expand_remote_path(self, client: "paramiko.SSHClient", path: str) -> str:
        raw = path.strip()
        if not raw:
            raise SSHDeployError("Remote path is empty.")
        if not raw.startswith("~"):
            return raw
        home = self.run_command(client, "printf %s \"$HOME\"").strip()
        if not home:
            raise SSHDeployError("Unable to resolve remote home directory.")
        if raw == "~":
            return home
        if raw.startswith("~/"):
            return posixpath.join(home, raw[2:])
        return raw.replace("~", home, 1)

    @staticmethod
    def _escape_single_quotes(text: str) -> str:
        return text.replace("'", "'\"'\"'")

    def ensure_remote_dir(self, client: "paramiko.SSHClient", remote_dir: str) -> None:
        remote = self._expand_remote_path(client, self._normalize_remote_dir(remote_dir))
        escaped = self._escape_single_quotes(remote)
        self.run_command(client, f"mkdir -p '{escaped}'")

    def backup_remote_configs(
        self, client: "paramiko.SSHClient", remote_dir: str, backup_root: str = "~/klippconfig_backups"
    ) -> str:
        remote = self._expand_remote_path(client, self._normalize_remote_dir(remote_dir))
        escaped_remote = self._escape_single_quotes(remote)
        escaped_backup_root = self._escape_single_quotes(backup_root)
        stamp_cmd = "date +%Y%m%d-%H%M%S"
        stamp = self.run_command(client, stamp_cmd).strip()
        target_dir = f"{backup_root}/backup-{stamp}"
        escaped_target = self._escape_single_quotes(target_dir)
        cmd = (
            f"mkdir -p '{escaped_backup_root}' '{escaped_target}' && "
            f"cp -r '{escaped_remote}'/* '{escaped_target}' 2>/dev/null || true"
        )
        self.run_command(client, cmd)
        return target_dir

    def upload_pack(
        self,
        client: "paramiko.SSHClient",
        pack: RenderedPack,
        remote_dir: str,
    ) -> list[str]:
        remote = self._expand_remote_path(client, self._normalize_remote_dir(remote_dir))
        self.ensure_remote_dir(client, remote)
        uploaded: list[str] = []
        try:
            with client.open_sftp() as sftp:
                for name, contents in pack.files.items():
                    remote_path = posixpath.join(remote, name)
                    with sftp.file(remote_path, "w") as handle:
                        handle.set_pipelined(True)
                        handle.write(contents)
                    uploaded.append(remote_path)
        except Exception as exc:  # noqa: BLE001
            raise SSHDeployError(f"SFTP upload failed: {exc}") from exc
        return uploaded

    @staticmethod
    def _escape_remote_glob_path(path: str) -> str:
        """Quote path for shell command while preserving wildcard patterns."""
        return shlex.quote(path)

    def list_remote_files(
        self,
        host: str,
        port: int,
        username: str,
        remote_dir: str,
        password: str | None = None,
        key_path: str | None = None,
        max_depth: int = 5,
    ) -> list[str]:
        if max_depth < 1:
            raise SSHDeployError("max_depth must be at least 1.")
        client = self.connect(host, port, username, password, key_path)
        try:
            expanded_dir = self._expand_remote_path(client, self._normalize_remote_dir(remote_dir))
            escaped_dir = self._escape_single_quotes(expanded_dir)
            command = f"find '{escaped_dir}' -maxdepth {int(max_depth)} -type f | sort"
            output = self.run_command(client, command)
            files = [line.strip() for line in output.splitlines() if line.strip()]
            return files
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, SSHDeployError):
                raise
            raise SSHDeployError(f"Failed to list files in '{remote_dir}': {exc}") from exc
        finally:
            client.close()

    def list_directory(
        self,
        host: str,
        port: int,
        username: str,
        remote_dir: str,
        password: str | None = None,
        key_path: str | None = None,
    ) -> dict[str, Any]:
        client = self.connect(host, port, username, password, key_path)
        try:
            expanded_dir = self._expand_remote_path(client, self._normalize_remote_dir(remote_dir))
            entries: list[dict[str, str]] = []
            with client.open_sftp() as sftp:
                for entry in sftp.listdir_attr(expanded_dir):
                    name = entry.filename
                    if name in {".", ".."}:
                        continue
                    entry_type = "dir" if stat.S_ISDIR(entry.st_mode) else "file"
                    entries.append(
                        {
                            "name": name,
                            "path": posixpath.join(expanded_dir, name),
                            "type": entry_type,
                        }
                    )
            entries.sort(key=lambda item: (0 if item["type"] == "dir" else 1, item["name"].lower()))
            return {"directory": expanded_dir, "entries": entries}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, SSHDeployError):
                raise
            raise SSHDeployError(f"Failed to list directory '{remote_dir}': {exc}") from exc
        finally:
            client.close()

    def fetch_file(
        self,
        host: str,
        port: int,
        username: str,
        remote_path: str,
        password: str | None = None,
        key_path: str | None = None,
    ) -> str:
        client = self.connect(host, port, username, password, key_path)
        try:
            expanded = self._expand_remote_path(client, remote_path)
            with client.open_sftp() as sftp:
                with sftp.file(expanded, "r") as handle:
                    data = handle.read()
                    if isinstance(data, bytes):
                        return data.decode("utf-8", errors="replace")
                    return str(data)
        except Exception as exc:  # noqa: BLE001
            raise SSHDeployError(f"Failed to fetch remote file '{remote_path}': {exc}") from exc
        finally:
            client.close()

    def write_file(
        self,
        host: str,
        port: int,
        username: str,
        remote_path: str,
        content: str,
        password: str | None = None,
        key_path: str | None = None,
    ) -> str:
        client = self.connect(host, port, username, password, key_path)
        try:
            expanded = self._expand_remote_path(client, remote_path)
            parent = posixpath.dirname(expanded) or "."
            self.ensure_remote_dir(client, parent)
            with client.open_sftp() as sftp:
                with sftp.file(expanded, "w") as handle:
                    handle.set_pipelined(True)
                    handle.write(content)
            return expanded
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, SSHDeployError):
                raise
            raise SSHDeployError(f"Failed to write remote file '{remote_path}': {exc}") from exc
        finally:
            client.close()

    def create_backup(
        self,
        host: str,
        port: int,
        username: str,
        remote_dir: str,
        password: str | None = None,
        key_path: str | None = None,
        backup_root: str = "~/klippconfig_backups",
    ) -> str:
        client = self.connect(host, port, username, password, key_path)
        try:
            return self.backup_remote_configs(client, remote_dir, backup_root=backup_root)
        finally:
            client.close()

    def list_backups(
        self,
        host: str,
        port: int,
        username: str,
        password: str | None = None,
        key_path: str | None = None,
        backup_root: str = "~/klippconfig_backups",
    ) -> list[str]:
        client = self.connect(host, port, username, password, key_path)
        try:
            expanded_root = self._expand_remote_path(client, backup_root)
            escaped_root = self._escape_single_quotes(expanded_root)
            command = f"ls -1dt '{escaped_root}'/backup-* 2>/dev/null || true"
            output = self.run_command(client, command)
            backups = [line.strip() for line in output.splitlines() if line.strip()]
            return backups
        finally:
            client.close()

    def restore_backup(
        self,
        host: str,
        port: int,
        username: str,
        remote_dir: str,
        backup_path: str,
        password: str | None = None,
        key_path: str | None = None,
        clear_before_restore: bool = True,
    ) -> None:
        if not backup_path.strip():
            raise SSHDeployError("Backup path is empty.")

        client = self.connect(host, port, username, password, key_path)
        try:
            expanded_remote = self._expand_remote_path(client, self._normalize_remote_dir(remote_dir))
            expanded_backup = self._expand_remote_path(client, backup_path)

            escaped_remote = self._escape_single_quotes(expanded_remote)
            escaped_backup = self._escape_single_quotes(expanded_backup)

            commands: list[str] = [
                f"test -d '{escaped_backup}'",
                f"mkdir -p '{escaped_remote}'",
            ]
            if clear_before_restore:
                commands.append(
                    f"find '{escaped_remote}' -mindepth 1 -maxdepth 1 -exec rm -rf {{}} +"
                )
            commands.append(f"cp -a '{escaped_backup}'/. '{escaped_remote}'/")
            self.run_command(client, " && ".join(commands))
        finally:
            client.close()

    def _download_remote_tree(
        self,
        sftp: "paramiko.SFTPClient",
        remote_dir: str,
        local_dir: Path,
    ) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        for entry in sftp.listdir_attr(remote_dir):
            name = entry.filename
            if name in {".", ".."}:
                continue
            remote_path = posixpath.join(remote_dir, name)
            local_path = local_dir / name
            if stat.S_ISDIR(entry.st_mode):
                self._download_remote_tree(sftp, remote_path, local_path)
            else:
                sftp.get(remote_path, str(local_path))

    def download_backup(
        self,
        host: str,
        port: int,
        username: str,
        backup_path: str,
        local_destination: str,
        password: str | None = None,
        key_path: str | None = None,
    ) -> str:
        if not backup_path.strip():
            raise SSHDeployError("Backup path is empty.")
        if not local_destination.strip():
            raise SSHDeployError("Local destination is empty.")

        target_dir = Path(local_destination).expanduser()
        client = self.connect(host, port, username, password, key_path)
        try:
            expanded_backup = self._expand_remote_path(client, backup_path)
            escaped_backup = self._escape_single_quotes(expanded_backup)
            self.run_command(client, f"test -d '{escaped_backup}'")
            with client.open_sftp() as sftp:
                self._download_remote_tree(sftp, expanded_backup, target_dir)
            return str(target_dir)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, SSHDeployError):
                raise
            raise SSHDeployError(f"Failed to download backup '{backup_path}': {exc}") from exc
        finally:
            client.close()

    def deploy_pack(
        self,
        host: str,
        port: int,
        username: str,
        pack: RenderedPack,
        remote_dir: str,
        password: str | None = None,
        key_path: str | None = None,
        backup_before_upload: bool = True,
        restart_klipper: bool = False,
        klipper_restart_command: str = "sudo systemctl restart klipper",
    ) -> dict[str, Any]:
        client = self.connect(host, port, username, password, key_path)
        result: dict[str, Any] = {"uploaded": [], "backup_path": None, "restart_output": None}
        try:
            if backup_before_upload:
                result["backup_path"] = self.backup_remote_configs(client, remote_dir)
            result["uploaded"] = self.upload_pack(client, pack, remote_dir)
            if restart_klipper:
                result["restart_output"] = self.run_command(client, klipper_restart_command).strip()
            return result
        finally:
            client.close()

    def deploy_pack_via_temp_zip(
        self,
        host: str,
        port: int,
        username: str,
        pack: RenderedPack,
        remote_dir: str,
        password: str | None = None,
        key_path: str | None = None,
        backup_before_upload: bool = True,
        restart_klipper: bool = False,
        klipper_restart_command: str = "sudo systemctl restart klipper",
    ) -> dict[str, Any]:
        # Placeholder for future zip-based transport optimization.
        with tempfile.TemporaryDirectory():
            return self.deploy_pack(
                host=host,
                port=port,
                username=username,
                pack=pack,
                remote_dir=remote_dir,
                password=password,
                key_path=key_path,
                backup_before_upload=backup_before_upload,
                restart_klipper=restart_klipper,
                klipper_restart_command=klipper_restart_command,
            )


