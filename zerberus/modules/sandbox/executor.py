"""
Sandbox Executor – Patch 52.
Führt Code in einem isolierten Docker-Container aus.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10


async def execute_in_sandbox(code: str, language: str = "python") -> dict:
    """
    Führt Code in einem Docker-Container aus (network none, 128m RAM, 0.5 CPU).

    Returns:
        {"stdout": str, "stderr": str, "exit_code": int, "timed_out": bool}
    """
    try:
        from zerberus.main import _DOCKER_OK
    except ImportError:
        _DOCKER_OK = False

    if not _DOCKER_OK:
        return {
            "stdout": "",
            "stderr": "Docker nicht verfügbar",
            "exit_code": -1,
            "timed_out": False,
        }

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", "128m",
            "--cpus", "0.5",
            "python:3.11-slim",
            "python", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=_TIMEOUT_SECONDS,
            )
            return {
                "stdout": stdout_bytes.decode("utf-8", errors="replace"),
                "stderr": stderr_bytes.decode("utf-8", errors="replace"),
                "exit_code": proc.returncode,
                "timed_out": False,
            }

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            logger.warning("[SANDBOX] Timeout nach 10 Sekunden – Container beendet")
            return {
                "stdout": "",
                "stderr": "Timeout nach 10 Sekunden",
                "exit_code": -1,
                "timed_out": True,
            }

    except Exception as e:
        logger.warning(f"[SANDBOX] execute_in_sandbox fehlgeschlagen: {e}")
        return {
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
            "timed_out": False,
        }
