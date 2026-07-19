import json
import logging
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from config import config

logger = logging.getLogger(__name__)


class ShadowsocksClient:
    """Проектный sslocal: не меняет системные сетевые настройки."""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen] = []
        self._config_path: Path | None = None
        self._stderr_paths: list[Path] = []
        self.telegram_port: int | None = None
        self.openai_port: int | None = None

    def start(self) -> None:
        if not config.shadowsocks_enabled:
            return

        binary = Path(config.shadowsocks_binary).expanduser()
        if not binary.is_absolute():
            binary = Path(__file__).resolve().parent / binary
        if not binary.is_file() or binary.stat().st_size == 0 or not os.access(binary, os.X_OK):
            raise RuntimeError(
                f"Shadowsocks-клиент отсутствует или не является исполняемым файлом: {binary}. "
                "Запустите scripts/install_shadowsocks.sh."
            )

        telegram_binary = Path(config.shadowsocks_telegram_binary).expanduser()
        if not telegram_binary.is_file():
            raise RuntimeError(
                f"Не найден рабочий Telegram Shadowsocks-клиент: {telegram_binary}."
            )

        self.telegram_port = self._choose_local_port()
        self.openai_port = self._find_free_port()
        while self.openai_port == self.telegram_port:
            self.openai_port = self._find_free_port()
        config.telegram_proxy_url = f"socks5h://127.0.0.1:{self.telegram_port}"
        config.llm_proxy_url = f"http://127.0.0.1:{self.openai_port}"

        runtime_config = {
            "server": config.shadowsocks_server,
            "server_port": config.shadowsocks_server_port,
            "password": config.shadowsocks_password,
            "method": config.shadowsocks_method,
        }
        handle = tempfile.NamedTemporaryFile(
            mode="w", prefix="recruiter-ss-", suffix=".json", delete=False
        )
        try:
            json.dump(runtime_config, handle)
            handle.close()
            self._config_path = Path(handle.name)
            os.chmod(self._config_path, 0o600)
            self._start_telegram_listener(telegram_binary, self.telegram_port)
            self._start_listener(binary, self.openai_port, "http")
            logger.info(
                "Shadowsocks запущен: Telegram SOCKS5h :%d, LLM HTTP/IPv4 :%d.",
                self.telegram_port,
                self.openai_port,
            )
        finally:
            # sslocal считывает конфигурацию при запуске; пароль не оставляем
            # во временном файле на всё время работы.
            if self._config_path and self._config_path.exists():
                self._config_path.unlink()
            self._config_path = None

    def _start_telegram_listener(self, binary: Path, port: int) -> None:
        """Запустить shadowsocks-libev точно как в рабочей ручной команде."""
        stderr_file = tempfile.NamedTemporaryFile(
            mode="w+", prefix="recruiter-telegram-ss-", suffix=".log", delete=False
        )
        stderr_path = Path(stderr_file.name)
        self._stderr_paths.append(stderr_path)
        process = subprocess.Popen(
            [
                str(binary),
                "-s", config.shadowsocks_server,
                "-p", str(config.shadowsocks_server_port),
                "-k", config.shadowsocks_password,
                "-m", config.shadowsocks_method,
                "-l", str(port),
                "-b", "127.0.0.1",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
        )
        stderr_file.close()
        self.processes.append(process)
        self._wait_until_ready(process, port, stderr_path, "telegram-socks5")

    @staticmethod
    def _choose_local_port() -> int:
        if config.shadowsocks_local_port > 0:
            return config.shadowsocks_local_port
        return ShadowsocksClient._find_free_port()

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _start_listener(self, binary: Path, port: int, protocol: str) -> None:
        stderr_file = tempfile.NamedTemporaryFile(
            mode="w+", prefix="recruiter-ss-", suffix=".log", delete=False
        )
        stderr_path = Path(stderr_file.name)
        self._stderr_paths.append(stderr_path)
        args = [
            str(binary), "-c", str(self._config_path),
            "-b", f"127.0.0.1:{port}",
        ]
        if protocol == "http":
            args.extend(["--protocol", "http"])
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
        )
        stderr_file.close()
        self.processes.append(process)
        self._wait_until_ready(process, port, stderr_path, protocol)

    @staticmethod
    def _wait_until_ready(
        process: subprocess.Popen, port: int, stderr_path: Path, protocol: str
    ) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                details = stderr_path.read_text(errors="replace").strip()
                suffix = f" Причина: {details[-1000:]}" if details else ""
                raise RuntimeError(
                    f"Shadowsocks ({protocol}) завершился после запуска." + suffix
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("Shadowsocks-клиент не открыл локальный порт за 10 секунд.")

    def stop(self) -> None:
        try:
            for process in self.processes:
                if process.poll() is not None:
                    continue
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if self.processes:
                logger.info("Локальные Shadowsocks-клиенты остановлены.")
        finally:
            for path in self._stderr_paths:
                if path.exists():
                    path.unlink()
            self._stderr_paths.clear()
