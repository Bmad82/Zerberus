"""
Zentrale Konfiguration mit Pydantic und YAML.
Patch 61: ProfileConfig mit temperature-Feld (Per-User Temperatur-Override).
"""
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
    modules: Dict[str, Any] = {}
    profiles: Dict[str, Any] = {}  # Patch 61: ProfileConfig-Einträge (raw Dict, da nala.py direkt yaml liest)
    openrouter: OpenRouterConfig = OpenRouterConfig()  # Patch 63: Provider-Blacklist
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