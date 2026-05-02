"""
Zentrale Konfiguration mit Pydantic und YAML.
Patch 61: ProfileConfig mit temperature-Feld (Per-User Temperatur-Override).
Patch 156: settings_writer / invalidates_settings — strukturelle Cache-Invalidierung
fuer alle config.yaml-Writer (siehe lessons.md).
"""
import asyncio
from contextlib import contextmanager
from functools import wraps
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from typing import Optional, Dict, List, Any
import yaml
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(".env"), override=False)

# -------------------------------------------------------------------
# Teile der Konfiguration
# -------------------------------------------------------------------
class DialectPattern(BaseModel):
    trigger: str
    response: str

class DialectConfig(BaseModel):
    marker: str
    patterns: List[DialectPattern]

class WhisperCorrection(BaseModel):
    old: str
    new: str

class RepetitionFilterConfig(BaseModel):
    """Patch 102 (B-01): Phrasen-Wiederholungsfilter für Whisper-Endlosschleifen."""
    enabled: bool = True
    min_phrase_len: int = 2
    max_phrase_len: int = 6
    max_repeats: int = 2

class WhisperCleanerConfig(BaseModel):
    corrections: List[WhisperCorrection]
    strip_trailing: List[str] = []
    repetition_filter: Optional[RepetitionFilterConfig] = None  # Patch 102 (B-01)


class WhisperConfig(BaseModel):
    """Patch 160: Transport-Konfiguration fuer Whisper-Docker-Calls.

    Getrennt von WhisperCleanerConfig (das ist post-processing). Hier liegt
    das httpx-Timeout-Budget und der Short-Audio-Guard-Schwellwert. Defaults
    sind bewusst ins Modell eingebaut, weil config.yaml gitignored ist —
    sonst wuerden sie nach `git clone` fehlen (analog OpenRouterConfig).
    """
    request_timeout_seconds: float = 120.0  # Gesamt-Read-Timeout fuer den Whisper-Call
    connect_timeout_seconds: float = 10.0   # Connect bleibt kurz — Docker down = schnell melden
    min_audio_bytes: int = 4096             # < 4 KB ~ < 0.25s Audio → Guard → leere Transkription
    timeout_retries: int = 1                # Anzahl Retries NACH dem ersten Versuch (0 = kein Retry)
    retry_backoff_seconds: float = 2.0      # Wartezeit zwischen Erstversuch und Retry

class QuietHoursConfig(BaseModel):
    enabled: bool = False
    start: str = "22:00"
    end: str = "06:00"
    timezone: str = "Europe/Berlin"
    exclude_paths: List[str] = []

class RateLimitingConfig(BaseModel):
    enabled: bool = False
    default: str = "100/minute"
    limits: Dict[str, str] = {}

class DatabaseConfig(BaseModel):
    url: str = "sqlite+aiosqlite:///./bunker_memory.db"
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    module_table_prefix: str = "module_"

class EventBusConfig(BaseModel):
    type: str = "memory"
    redis_url: Optional[str] = None

class LegacyModelsConfig(BaseModel):
    cloud_model: str
    local_model: str

class LegacyUrlsConfig(BaseModel):
    whisper_url: str
    cloud_api_url: str
    local_url: str

class LegacySettingsConfig(BaseModel):
    threshold_length: int = 10
    ai_temperature: float = 0.7

class PacemakerConfig(BaseModel):
    active: bool = True
    interval_seconds: int = 240
    keep_alive_minutes: int = 25

class LegacyConfig(BaseModel):
    models: LegacyModelsConfig
    urls: LegacyUrlsConfig
    settings: LegacySettingsConfig
    pacemaker: PacemakerConfig

class ModuleConfig(BaseModel):
    enabled: bool = True
    class Config:
        extra = "allow"

class AuthConfig(BaseModel):
    token_secret: str = "CHANGE_ME"
    token_expire_minutes: int = 525600  # Patch 103: 365 Tage (eigener Server, kein Risiko)
    static_api_key: str = ""  # Patch 59: X-API-Key Header als Alternative zu Bearer

class OpenRouterConfig(BaseModel):
    """
    Patch 63: OpenRouter Provider-Blacklist.
    Patch 102 (B-20): Default-Blacklist explizit gesetzt, da config.yaml gitignored
    ist — sonst greift die Blacklist nach `git clone` nicht.
    Kann via config.yaml unter `openrouter.provider_blacklist` überschrieben werden.
    """
    provider_blacklist: List[str] = ["chutes", "targon"]


class HitlConfig(BaseModel):
    """Patch 167 — Defaults fuer das HitL-Subsystem (Phase C, Block 3).

    ``timeout_seconds`` greift ueber den Auto-Reject-Sweep: Pending-Tasks, die
    aelter als dieser Wert sind, werden auf ``expired`` gesetzt und der User
    bekommt eine "⏰ zu langsam"-Nachricht.

    ``sweep_interval_seconds`` ist die Frequenz, mit der der Sweep-Task laeuft.
    Defaults sind hier (statt in config.yaml) gesetzt, weil ``config.yaml``
    gitignored ist — sonst wuerde der Schutz nach ``git clone`` fehlen.
    """
    timeout_seconds: int = 300        # 5 Minuten Default
    sweep_interval_seconds: int = 30  # Sweep-Frequenz


class ProjectsConfig(BaseModel):
    """Patch 196 — Defaults fuer Projekt-Datei-Uploads (Phase 5a #4).

    Defaults sind hier (statt nur in config.yaml) gesetzt, weil
    ``config.yaml`` gitignored ist — sonst wuerden Storage-Limit und
    Extension-Blacklist nach ``git clone`` fehlen und der Upload haette
    keinen Schutz mehr.

    ``data_dir`` ist die Storage-Wurzel; die tatsaechlichen Pfade kommen
    aus ``projects_repo.storage_path_for(slug, sha, base_dir)`` und sind
    sha-prefix-fragmentiert (verhindert Hotspot-Ordner).

    Die Extension-Blacklist verhindert versehentliches Hochladen von
    ausfuehrbaren Dateien — Schutz vor Malware-Mitlieferung, nicht
    Code-Sandbox-Ersatz. Code-Execution laeuft separat ueber die
    Docker-Sandbox (P171).
    """
    data_dir: str = "data"
    max_upload_bytes: int = 50 * 1024 * 1024  # 50 MB
    blocked_extensions: List[str] = [
        ".exe", ".bat", ".cmd", ".com", ".msi", ".dll", ".scr",
        ".sh", ".ps1", ".vbs", ".jar",
    ]
    # Patch 198 (Phase 5a #2): Beim Anlegen Skelett-Files generieren
    # (ZERBERUS_<SLUG>.md + README.md). Kann fuer Migrations-Tests oder
    # Bulk-Imports abgeschaltet werden.
    auto_template: bool = True
    # Patch 199 (Phase 5a #3): Projekt-spezifischer RAG-Index. Pro Projekt
    # ein eigener FAISS-aehnlicher Numpy-Store unter
    # ``<data_dir>/projects/<slug>/_rag/``. Wird beim Upload + bei der
    # Template-Materialisierung gefuettert; der Chat-Endpoint zieht Top-K
    # Chunks beim aktiven Projekt. Tests ohne ``sentence-transformers``
    # koennen das Flag abschalten.
    rag_enabled: bool = True
    rag_top_k: int = 5
    rag_max_file_bytes: int = 5 * 1024 * 1024  # 5 MB — drueber: skip beim Indexen
    # Patch 203a (Phase 5a #5, Vorbereitung): Workspace-Layout
    # ``<data_dir>/projects/<slug>/_workspace/`` mit ``relative_path``-
    # gespiegelten Hardlinks/Copies des SHA-Storage. Vorbereitung fuer die
    # Code-Execution-Pipeline (P203b/c) — Sandbox braucht echten
    # Mount-Pfad. Tests koennen das Flag abschalten, wenn der
    # Hardlink-Pfad in CI-Sandboxen Probleme macht.
    workspace_enabled: bool = True


class SandboxConfig(BaseModel):
    """Patch 171 — Defaults fuer die Docker-Sandbox (Phase D, Block 1).

    Die Sandbox ist OPTIONAL und standardmaessig DEAKTIVIERT — muss
    bewusst via ``modules.sandbox.enabled: true`` aktiviert werden, weil
    sie Code-Execution ermoeglicht. Ohne Docker auf dem Host bleibt der
    Pfad sowieso inaktiv (Startup-Healthcheck in main.py).

    Defaults sind hier (statt nur in config.yaml) gesetzt, weil
    ``config.yaml`` gitignored ist — sonst wuerden die Werte nach
    ``git clone`` fehlen.
    """
    enabled: bool = False
    timeout_seconds: int = 30
    max_output_chars: int = 10000
    memory_limit: str = "256m"
    cpu_limit: float = 0.5
    pids_limit: int = 64
    tmpfs_size: str = "64m"
    python_image: str = "python:3.12-slim"
    node_image: str = "node:20-slim"
    allowed_languages: List[str] = ["python", "javascript"]


class PipelineConfig(BaseModel):
    """Patch 177 — Pipeline-Cutover-Feature-Flag.

    Wenn ``use_message_bus=True``, delegiert ``process_update`` an
    ``handle_telegram_update`` (Phase-E-Stack: Adapter + Pipeline).
    Default ``False`` heisst: Legacy-Pfad (``_legacy_process_update``)
    bleibt aktiv. Chris kann live umschalten via config.yaml + Reload.

    Defaults hier statt in config.yaml, weil ``config.yaml`` gitignored
    ist — sonst fehlen die Werte nach ``git clone``.
    """
    use_message_bus: bool = False


class MemoryExtractionConfig(BaseModel):
    """Patch 115: Background Memory Extraction Defaults.
    config.yaml-Override unter `modules.memory.*`.
    """
    extraction_enabled: bool = True
    extraction_model: Optional[str] = None  # None → legacy.models.cloud_model
    extraction_timeout: float = 45.0
    max_batch_words: int = 2000
    similarity_threshold: float = 0.9
    categories: List[str] = ["personal", "technical", "preference", "relationship", "event"]


class ProfileConfig(BaseModel):
    """Patch 61: Profil-Konfiguration mit optionalem Temperatur-Override."""
    display_name: str = ""
    password_hash: str = ""
    system_prompt_file: str = "system_prompt.json"
    theme_color: str = "#ec407a"
    permission_level: str = "guest"
    allowed_model: Optional[str] = None
    temperature: Optional[float] = None  # null = globale Einstellung aus legacy.settings.ai_temperature

    class Config:
        extra = "allow"

class Settings(BaseSettings):
    environment: str = "development"
    log_level: str = "INFO"
    port: int = 5000
    database: DatabaseConfig = DatabaseConfig()
    event_bus: EventBusConfig = EventBusConfig()
    quiet_hours: QuietHoursConfig = QuietHoursConfig()
    rate_limiting: RateLimitingConfig = RateLimitingConfig()
    auth: AuthConfig = AuthConfig()
    legacy: Optional[LegacyConfig] = None
    dialects: Dict[str, DialectConfig] = {}
    whisper_cleaner: WhisperCleanerConfig
    whisper: WhisperConfig = WhisperConfig()  # Patch 160: Transport-Config (Timeout, Short-Audio-Guard, Retry)
    modules: Dict[str, Any] = {}
    profiles: Dict[str, Any] = {}  # Patch 61: ProfileConfig-Einträge (raw Dict, da nala.py direkt yaml liest)
    openrouter: OpenRouterConfig = OpenRouterConfig()  # Patch 63: Provider-Blacklist
    projects: ProjectsConfig = ProjectsConfig()  # Patch 196: Datei-Upload-Limits + Extension-Blacklist
    features: Dict[str, Any] = {"decision_boxes": True, "whisper_watchdog": True, "hallucination_guard": True}  # Patch 118a/119/120 Feature-Flags (config.yaml gitignored → Default explizit)

    class Config:
        env_file = ".env"
        extra = "allow"

_settings: Optional[Settings] = None

def load_settings() -> Settings:
    """Lädt Settings aus config.yaml"""
    config_path = Path("config.yaml")
    if not config_path.exists():
        raise FileNotFoundError("config.yaml nicht gefunden!")
    
    with open(config_path, "r", encoding="utf-8") as f:
        config_dict = yaml.safe_load(f)
    
    return Settings(**config_dict)

def get_settings() -> Settings:
    """Singleton für Settings"""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings

def reload_settings():
    """Lädt Settings neu"""
    global _settings
    _settings = load_settings()
    return _settings


@contextmanager
def settings_writer():
    """Patch 156: Kontextmanager fuer alle config.yaml-Writer.

    Verwendung:
        with settings_writer():
            with open("config.yaml", "w") as f:
                yaml.dump(cfg, f)

    Nach dem with-Block wird der Settings-Cache automatisch invalidiert,
    damit der naechste get_settings()-Aufruf die neuen Werte sieht.
    """
    try:
        yield
    finally:
        reload_settings()


def invalidates_settings(func):
    """Patch 156: Decorator fuer Funktionen, die config.yaml schreiben.

    Sorgt dafuer, dass nach dem Funktionsaufruf der Settings-Singleton
    neu aus der YAML geladen wird. Funktioniert sowohl fuer sync- als
    auch async-Funktionen (z.B. FastAPI-Handler).
    """
    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def _awrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            finally:
                reload_settings()
        return _awrapper

    @wraps(func)
    def _wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            reload_settings()
    return _wrapper