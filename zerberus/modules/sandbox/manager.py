"""Patch 171 — Docker-Sandbox-Manager (Phase D, Block 1+3+4).

Ausfuehrung von LLM-generiertem Code in einem ephemeren Docker-Container.
Der bestehende ``executor.py`` (P52) bleibt fuer den HTTP-Endpoint
(/sandbox/execute) bestehen — dieser Manager ist der neue, haerter
isolierte Pfad fuer den Huginn-Pipeline-Flow.

Sicherheitsregeln (siehe Patch-Spec K2/G1/G7):
- Container hat KEIN Netzwerk (``--network none``)
- Read-only Root-FS, schreibbares ``/tmp`` als ``tmpfs``
- RAM-/CPU-/PID-Limits, ``--security-opt no-new-privileges``
- Kein Volume-Mount vom Host
- Container wird IMMER entfernt — auch bei Fehler/Timeout
- Code-Blockliste als zusaetzliche Schicht (nicht primaerer Schutz)

Die Blockliste fungiert als Belt+Suspenders: die Docker-Limits sind der
primaere Schutz. Die Pattern fangen nur die offensichtlichen Faelle.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from zerberus.core.config import SandboxConfig

logger = logging.getLogger(__name__)


# Patch 171 (Block 3): Code-Blockliste. Pro Sprache eine Liste — Treffer
# ueberspringt die Execution. KEIN Sicherheitsschutz, nur Komfort-Layer.
BLOCKED_PATTERNS_PYTHON: List[str] = [
    r"\bimport\s+os\b",
    r"\bimport\s+subprocess\b",
    r"\bimport\s+socket\b",
    r"\bfrom\s+os\b",
    r"\bfrom\s+subprocess\b",
    r"\bfrom\s+socket\b",
    r"\b__import__\s*\(",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bopen\s*\([^)]*['\"]w",
]

BLOCKED_PATTERNS_JAVASCRIPT: List[str] = [
    r"\brequire\s*\(\s*['\"]child_process['\"]",
    r"\brequire\s*\(\s*['\"]fs['\"]",
    r"\brequire\s*\(\s*['\"]net['\"]",
    r"\brequire\s*\(\s*['\"]http['\"]",
    r"\brequire\s*\(\s*['\"]https['\"]",
    r"\beval\s*\(",
    r"\bFunction\s*\(",
]

_PATTERNS_BY_LANGUAGE = {
    "python": BLOCKED_PATTERNS_PYTHON,
    "javascript": BLOCKED_PATTERNS_JAVASCRIPT,
}


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    execution_time_ms: int
    truncated: bool = False
    error: Optional[str] = None


def find_blocked_pattern(code: str, language: str) -> Optional[str]:
    """Liefert den ersten Blockliste-Treffer oder ``None``."""
    patterns = _PATTERNS_BY_LANGUAGE.get(language.lower(), [])
    for pat in patterns:
        if re.search(pat, code):
            return pat
    return None


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n…[truncated]", True


def _docker_available() -> bool:
    """Pruefen ob ``docker`` im PATH ist UND der Daemon antwortet."""
    if not shutil.which("docker"):
        return False
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=3,
        )
        return result.returncode == 0
    except Exception:
        return False


def _image_present(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


class SandboxManager:
    """Ephemerer Docker-Container fuer LLM-Code-Ausfuehrung."""

    def __init__(self, config: SandboxConfig):
        self.config = config

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def healthcheck(self) -> dict:
        """Synchroner Pre-Flight-Check. Wird vom main-lifespan aufgerufen.

        Returns:
            ``{"ok": bool, "reason": str, "docker": bool, "images": dict}``
        """
        if not self.config.enabled:
            return {"ok": False, "reason": "disabled", "docker": False, "images": {}}
        docker_ok = _docker_available()
        if not docker_ok:
            return {"ok": False, "reason": "docker_unavailable", "docker": False, "images": {}}
        images = {}
        if "python" in self.config.allowed_languages:
            images[self.config.python_image] = _image_present(self.config.python_image)
        if "javascript" in self.config.allowed_languages:
            images[self.config.node_image] = _image_present(self.config.node_image)
        any_missing = any(present is False for present in images.values())
        return {
            "ok": not any_missing,
            "reason": "image_missing" if any_missing else "ready",
            "docker": True,
            "images": images,
        }

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(
        self,
        code: str,
        language: str,
        timeout: Optional[int] = None,
        *,
        workspace_mount: Optional[Path] = None,
        mount_writable: bool = False,
    ) -> Optional[SandboxResult]:
        """Fuehrt ``code`` in einer ephemeren Docker-Sandbox aus.

        Args:
            workspace_mount: Optionaler Host-Pfad, der als ``/workspace``
                in den Container gemountet wird (Patch 203c). Default
                ``None`` → kein Mount, Pfad bleibt unveraendert wie P171.
                Der Pfad MUSS existieren und ein Verzeichnis sein.
            mount_writable: Wenn ``True``, wird der Mount als read-write
                eingehaengt; sonst (Default) als read-only (``:ro``).
                Read-only ist die sichere Default — der Sandbox-Code kann
                Files lesen, aber das Workspace nicht von innen veraendern.

        Returns:
            ``None`` wenn Sandbox deaktiviert/disabled ist (Caller sollte
            dann den Datei-Fallback nehmen).
            ``SandboxResult`` mit gefuelltem ``error``-Feld bei Container-
            Fehlern (Code-Fehler landen in ``stderr`` + ``exit_code``).
        """
        if not self.config.enabled:
            logger.info("[SANDBOX-171] Sandbox disabled — execute() returns None")
            return None

        lang = language.strip().lower()
        if lang not in {l.lower() for l in self.config.allowed_languages}:
            logger.warning("[SANDBOX-171] Sprache nicht erlaubt: %s", lang)
            return SandboxResult(
                stdout="",
                stderr="",
                exit_code=-1,
                execution_time_ms=0,
                error=f"Sprache nicht erlaubt: {lang}",
            )

        blocked = find_blocked_pattern(code, lang)
        if blocked:
            logger.warning(
                "[SANDBOX-171] Blocked pattern: %s in %s code", blocked, lang,
            )
            return SandboxResult(
                stdout="",
                stderr="",
                exit_code=-1,
                execution_time_ms=0,
                error=f"Blocked pattern: {blocked}",
            )

        # Patch 203c: Mount-Validation. Ein nicht-existenter oder kein
        # Verzeichnis-Pfad fuehrt zu einem fehlerhaften ``docker run``-
        # Aufruf — wir fangen das frueh ab.
        if workspace_mount is not None:
            if not workspace_mount.exists():
                logger.warning(
                    "[SANDBOX-203c] workspace_mount existiert nicht: %s", workspace_mount,
                )
                return SandboxResult(
                    stdout="", stderr="", exit_code=-1, execution_time_ms=0,
                    error=f"workspace_mount existiert nicht: {workspace_mount}",
                )
            if not workspace_mount.is_dir():
                logger.warning(
                    "[SANDBOX-203c] workspace_mount ist kein Verzeichnis: %s", workspace_mount,
                )
                return SandboxResult(
                    stdout="", stderr="", exit_code=-1, execution_time_ms=0,
                    error=f"workspace_mount ist kein Verzeichnis: {workspace_mount}",
                )

        image, run_args = self._image_and_command(lang, code)
        if image is None:
            return SandboxResult(
                stdout="",
                stderr="",
                exit_code=-1,
                execution_time_ms=0,
                error=f"Kein Image fuer Sprache {lang} konfiguriert",
            )

        effective_timeout = int(timeout if timeout is not None else self.config.timeout_seconds)
        container_name = f"zerberus-sandbox-{uuid.uuid4().hex[:12]}"
        return await self._run_in_container(
            container_name=container_name,
            image=image,
            run_args=run_args,
            language=lang,
            line_count=len(code.splitlines()),
            timeout_seconds=effective_timeout,
            workspace_mount=workspace_mount,
            mount_writable=mount_writable,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _image_and_command(self, language: str, code: str) -> tuple[Optional[str], list[str]]:
        if language == "python":
            return self.config.python_image, ["python", "-c", code]
        if language == "javascript":
            return self.config.node_image, ["node", "-e", code]
        return None, []

    async def _run_in_container(
        self,
        container_name: str,
        image: str,
        run_args: list[str],
        language: str,
        line_count: int,
        timeout_seconds: int,
        workspace_mount: Optional[Path] = None,
        mount_writable: bool = False,
    ) -> SandboxResult:
        docker_args = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--network", "none",
            "--memory", str(self.config.memory_limit),
            "--cpus", str(self.config.cpu_limit),
            "--read-only",
            "--tmpfs", f"/tmp:size={self.config.tmpfs_size},exec",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.config.pids_limit),
        ]

        # Patch 203c: optionaler Workspace-Mount. ``-v <abs>:/workspace[:ro]``
        # + ``--workdir /workspace`` damit relative Pfade in den Files
        # naturgemaess innerhalb des Workspaces aufgeloest werden.
        if workspace_mount is not None:
            host_abs = str(workspace_mount.resolve(strict=False))
            mount_spec = f"{host_abs}:/workspace"
            if not mount_writable:
                mount_spec += ":ro"
            docker_args.extend(["-v", mount_spec, "--workdir", "/workspace"])
            logger.info(
                "[SANDBOX-203c] mount=%s writable=%s",
                mount_spec, mount_writable,
            )

        docker_args.extend([image, *run_args])

        start = time.monotonic()
        timed_out = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds,
                )
                exit_code = proc.returncode if proc.returncode is not None else -1
            except asyncio.TimeoutError:
                timed_out = True
                # docker stop schickt SIGTERM, dann SIGKILL — wir nehmen rm -f
                # (synchron via subprocess), weil das den Cleanup garantiert.
                await self._force_remove_container(container_name)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except Exception:
                    pass
                exit_code = -1
                stdout_bytes = b""
                stderr_bytes = b""
        except FileNotFoundError:
            return SandboxResult(
                stdout="", stderr="", exit_code=-1,
                execution_time_ms=int((time.monotonic() - start) * 1000),
                error="docker-CLI nicht gefunden",
            )
        except Exception as e:
            logger.warning("[SANDBOX-171] subprocess-Fehler: %s", e)
            await self._force_remove_container(container_name)
            return SandboxResult(
                stdout="", stderr="", exit_code=-1,
                execution_time_ms=int((time.monotonic() - start) * 1000),
                error=str(e),
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if timed_out:
            logger.warning(
                "[SANDBOX-171] Timeout nach %ds, Container killed (%s)",
                timeout_seconds, container_name,
            )
            return SandboxResult(
                stdout="",
                stderr="",
                exit_code=-1,
                execution_time_ms=elapsed_ms,
                error=f"Timeout nach {timeout_seconds}s",
            )

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        max_chars = int(self.config.max_output_chars)
        stdout_out, stdout_trunc = _truncate(stdout_text, max_chars)
        stderr_out, stderr_trunc = _truncate(stderr_text, max_chars)
        truncated = stdout_trunc or stderr_trunc
        if truncated:
            logger.warning(
                "[SANDBOX-171] Output truncated (stdout=%d, stderr=%d > %d)",
                len(stdout_text), len(stderr_text), max_chars,
            )

        logger.info(
            "[SANDBOX-171] Executed %s (%d lines) in %dms, exit=%d",
            language, line_count, elapsed_ms, exit_code,
        )

        return SandboxResult(
            stdout=stdout_out,
            stderr=stderr_out,
            exit_code=exit_code,
            execution_time_ms=elapsed_ms,
            truncated=truncated,
        )

    async def _force_remove_container(self, container_name: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception as e:
            logger.warning("[SANDBOX-171] rm -f failed for %s: %s", container_name, e)

    # ------------------------------------------------------------------
    # Cleanup (alle laufenden Sandboxen)
    # ------------------------------------------------------------------

    async def cleanup(self) -> int:
        """Entfernt alle Sandbox-Container (zerberus-sandbox-*).

        Wird vom Shutdown-Hook und Tests genutzt. Liefert die Anzahl der
        entfernten Container.
        """
        if not shutil.which("docker"):
            return 0
        try:
            list_proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "-aq",
                "--filter", "name=zerberus-sandbox-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(list_proc.communicate(), timeout=5)
        except Exception:
            return 0
        ids = [line for line in out.decode().split() if line]
        if not ids:
            return 0
        try:
            rm_proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", *ids,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(rm_proc.wait(), timeout=10)
        except Exception:
            return 0
        return len(ids)


# ──────────────────────────────────────────────────────────────────────
# Singleton (lazy)
# ──────────────────────────────────────────────────────────────────────


_singleton: Optional[SandboxManager] = None


def get_sandbox_manager() -> SandboxManager:
    """Lazy-Singleton — liest bei Erst-Aufruf die aktuelle Settings.

    Die Pipeline ruft das pro Request auf; Tests koennen den Singleton
    via ``reset_sandbox_manager()`` zuruecksetzen.
    """
    global _singleton
    if _singleton is None:
        from zerberus.core.config import get_settings
        settings = get_settings()
        mod_cfg = (settings.modules.get("sandbox") or {}) if settings.modules else {}
        cfg = SandboxConfig(**mod_cfg)
        _singleton = SandboxManager(cfg)
    return _singleton


def reset_sandbox_manager() -> None:
    global _singleton
    _singleton = None
