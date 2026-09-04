"""
core/rag/memory.py
Session-based conversation memory with sliding window.
Enables the AI Assistant to maintain context across consecutive messages.
"""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class ChatMessage:
    role:      str  # "user" | "assistant" | "system"
    content:   str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "role":      self.role,
            "content":   self.content,
            "timestamp": self.timestamp.isoformat(),
        }


class ChatMemory:
    """
    In-memory conversation buffer for a single session.
    Keeps only the most recent N exchanges to avoid context bloat.
    """

    def __init__(self, max_messages: int = 10):
        self._messages: list[ChatMessage] = []
        self._max_messages = max_messages

    def add_user_message(self, content: str) -> None:
        self._messages.append(ChatMessage(role="user", content=content))
        self._truncate()

    def add_assistant_message(self, content: str) -> None:
        self._messages.append(ChatMessage(role="assistant", content=content))
        self._truncate()

    def get_history(self) -> list[ChatMessage]:
        return self._messages

    def format_for_prompt(self) -> str:
        """Format history as a text block for LLM prompt context."""
        if not self._messages:
            return ""
        
        lines = ["Conversation history so far:"]
        for msg in self._messages:
            role_label = "Operator" if msg.role == "user" else "Assistant"
            lines.append(f"{role_label}: {msg.content}")
        
        return "\n".join(lines)

    def _truncate(self) -> None:
        if len(self._messages) > self._max_messages:
            # Always keep an even number of messages (pairs) if possible, 
            # but standard sliding window is fine too.
            self._messages = self._messages[-self._max_messages:]

    def clear(self) -> None:
        self._messages = []

    def serialize(self) -> list[dict]:
        return [m.to_dict() for m in self._messages]

    @classmethod
    def deserialize(cls, data: list[dict]) -> ChatMemory:
        memory = cls()
        for d in data:
            msg = ChatMessage(
                role=d["role"],
                content=d["content"],
                timestamp=datetime.fromisoformat(d["timestamp"])
            )
            memory._messages.append(msg)
        return memory

    def save_to_disk(self, state_dir: str, session_id: str) -> None:
        """Persist session history to state/ai_sessions/<session_id>.json"""
        session_path = Path(state_dir) / "ai_sessions" / f"{session_id}.json"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "session_id":   session_id,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "messages":     self.serialize()
        }
        with open(session_path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load_from_disk(cls, state_dir: str, session_id: str) -> ChatMemory:
        """Load session history from disk if exists."""
        session_path = Path(state_dir) / "ai_sessions" / f"{session_id}.json"
        if not session_path.exists():
            return cls()
        
        try:
            with open(session_path, "r") as f:
                data = json.load(f)
            return cls.deserialize(data.get("messages", []))
        except Exception:
            return cls()
