"""
Patch 102 (B-20): Tests für LLM-Fallback und Provider-Blacklist.
- Verifiziert dass die openrouter.provider_blacklist aus config.yaml geladen wird.
- Verifiziert dass LLMService bei Fehlern keine zufällige Modellauswahl macht,
  sondern auf cloud_model fällt.
"""
import inspect


def test_openrouter_blacklist_geladen():
    """openrouter.provider_blacklist muss aus config.yaml gelesen werden."""
    from zerberus.core.config import get_settings
    settings = get_settings()
    bl = settings.openrouter.provider_blacklist
    assert isinstance(bl, list)
    assert "chutes" in bl, f"chutes muss in provider_blacklist stehen, ist aber: {bl}"
    assert "targon" in bl, f"targon muss in provider_blacklist stehen, ist aber: {bl}"


def test_llm_service_nutzt_blacklist_und_cloud_default():
    """
    Verifiziert per Source-Inspektion, dass LLMService.call:
      a) `model_override or settings.legacy.models.cloud_model` als Modellauswahl nutzt
         (kein random.choice o.ä.) — Schutz gegen B-20.
      b) `provider_blacklist` aus den Settings liest und in den Provider-Block setzt.
    Kein async-Aufruf nötig — robust gegen Test-Isolation/Playwright-Loop-Konflikte.
    """
    from zerberus.core import llm as llm_mod
    src = inspect.getsource(llm_mod.LLMService.call)

    # Modellauswahl
    assert "model_override or settings.legacy.models.cloud_model" in src, (
        "Modellauswahl muss Default 'cloud_model' nutzen — keine zufällige Auswahl!"
    )
    assert "random" not in src.lower(), (
        "LLMService.call darf keine random-basierte Modellauswahl enthalten (B-20)"
    )

    # Provider-Blacklist wird aus Settings gelesen
    assert "provider_blacklist" in src, (
        "LLMService.call muss provider_blacklist aus Settings lesen"
    )
    # Blacklist landet im Provider-Block
    assert '"ignore"' in src or "'ignore'" in src, (
        "Blacklist muss als provider['ignore'] in den OpenRouter-Payload"
    )


def test_legacy_router_hat_fallback_logging():
    """Patch 102 (B-20): legacy.py muss [FALLBACK-102] Warning bei Fallback loggen."""
    from zerberus.app.routers import legacy as legacy_mod
    src = inspect.getsource(legacy_mod)
    assert "[FALLBACK-102]" in src, (
        "legacy.py muss [FALLBACK-102] Warnings für Fallback-Pfade enthalten"
    )
