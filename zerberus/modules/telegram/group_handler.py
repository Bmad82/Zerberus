"""
Patch 123 – Huginn Gruppen-Intelligenz.

Entscheidet pro Gruppen-Message ob Huginn antworten soll.
Nicht jeden Message beantworten - Huginn soll sich einschalten wenn er WIRKLICH
was beizutragen hat, direkt angesprochen wird oder korrigieren muss.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("zerberus.huginn.group")

SMART_INTERJECTION_PROMPT = (
    "Du bist Huginn, ein KI-Rabe in einer Telegram-Gruppe.\n"
    "Hier sind die letzten Nachrichten der Gruppe:\n\n"
    "{recent_messages}\n\n"
    "Hast du einen WIRKLICH nuetzlichen, klugen oder korrigierenden Beitrag?\n"
    "Laber NICHT einfach mit. Nur wenn du echten Mehrwert hast (Faktcheck, "
    "technische Korrektur, wichtige Info).\n\n"
    "Antworte entweder mit 'SKIP' oder mit deinem Beitrag (max 3 Saetze)."
)


@dataclass
class GroupState:
    """Pro-Gruppen State: Cooldown-Tracking + Message-History."""
    last_interjection_ts: float = 0.0
    recent_messages: list[Dict[str, Any]] = field(default_factory=list)


class GroupManager:
    """Verwaltet den Zustand aller Gruppen in denen Huginn ist."""

    MAX_HISTORY = 20

    def __init__(self, cooldown_seconds: int = 300):
        self.cooldown = cooldown_seconds
        self._groups: Dict[int, GroupState] = {}

    def record_message(self, chat_id: int, username: str, text: str) -> None:
        state = self._groups.setdefault(chat_id, GroupState())
        state.recent_messages.append({"username": username, "text": text, "ts": time.time()})
        if len(state.recent_messages) > self.MAX_HISTORY:
            state.recent_messages = state.recent_messages[-self.MAX_HISTORY:]

    def mark_interjection(self, chat_id: int) -> None:
        state = self._groups.setdefault(chat_id, GroupState())
        state.last_interjection_ts = time.time()

    def cooldown_active(self, chat_id: int) -> bool:
        state = self._groups.get(chat_id)
        if not state:
            return False
        return (time.time() - state.last_interjection_ts) < self.cooldown

    def recent_messages_text(self, chat_id: int, limit: int = 10) -> str:
        state = self._groups.get(chat_id)
        if not state:
            return ""
        lines = []
        for msg in state.recent_messages[-limit:]:
            lines.append(f"{msg['username']}: {msg['text']}")
        return "\n".join(lines)


def should_respond_in_group(
    info: Dict[str, Any],
    behavior: Dict[str, Any],
    group_manager: GroupManager,
    bot_user_id: Optional[int] = None,
    bot_name: str = "Huginn",
    bot_username: str = "HuginnBot",
) -> Dict[str, Any]:
    """Entscheidet ob Huginn in einer Gruppe auf diese Message antworten soll.

    Returns:
        {
            "respond": bool,
            "reason": str,                # direct_name | mention | reply | autonomous | skip
            "needs_llm_decision": bool,   # True = autonomer Einwurf muss vom LLM validiert werden
        }
    """
    text = (info.get("text") or "").strip()
    chat_id = info.get("chat_id")
    if not text:
        return {"respond": False, "reason": "empty", "needs_llm_decision": False}

    respond_to_name = bool(behavior.get("respond_to_name", True))
    respond_to_mention = bool(behavior.get("respond_to_mention", True))
    respond_to_direct_reply = bool(behavior.get("respond_to_direct_reply", True))
    autonomous = bool(behavior.get("autonomous_interjection", True))
    trigger_mode = str(behavior.get("interjection_trigger", "smart"))

    lower = text.lower()

    # 1. Direkte @-Mention
    if respond_to_mention and f"@{bot_username.lower()}" in lower:
        return {"respond": True, "reason": "mention", "needs_llm_decision": False}

    # 2. Name im Text
    if respond_to_name and bot_name.lower() in lower:
        return {"respond": True, "reason": "direct_name", "needs_llm_decision": False}

    # 3. Reply auf eine Huginn-Message
    if respond_to_direct_reply and bot_user_id is not None:
        reply = info.get("reply_to_message") or {}
        reply_from_id = (reply.get("from") or {}).get("id")
        if reply_from_id == bot_user_id:
            return {"respond": True, "reason": "reply", "needs_llm_decision": False}

    # 4. Autonomer Einwurf (nur wenn aktiviert + Cooldown vorbei)
    if autonomous and trigger_mode == "smart":
        if chat_id is not None and not group_manager.cooldown_active(chat_id):
            return {"respond": True, "reason": "autonomous", "needs_llm_decision": True}

    return {"respond": False, "reason": "skip", "needs_llm_decision": False}


def build_smart_interjection_prompt(recent_messages_text: str) -> str:
    """Erzeugt den LLM-Prompt der 'SKIP' oder einen Beitrag zurueckliefert."""
    return SMART_INTERJECTION_PROMPT.format(recent_messages=recent_messages_text or "(leer)")


def is_skip_response(llm_response: str) -> bool:
    """True wenn das LLM 'SKIP' zurueckgegeben hat."""
    if not llm_response:
        return True
    cleaned = llm_response.strip().strip('"').strip("'").upper()
    return cleaned == "SKIP" or cleaned.startswith("SKIP")
