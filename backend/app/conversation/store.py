import secrets
import threading
import time
from app.conversation.state import ConversationState


class SessionStore:
    def __init__(self, ttl_seconds: int = 1800, max_sessions: int = 1000):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._sessions: dict[str, ConversationState] = {}
        self._lock = threading.Lock()

    def _cleanup_expired(self, now: float) -> None:
        expired = [sid for sid, s in self._sessions.items() if now - s.updated_at > self.ttl_seconds]
        for sid in expired:
            del self._sessions[sid]

    def get_or_create(self, session_id: str | None) -> ConversationState:
        now = time.time()
        with self._lock:
            self._cleanup_expired(now)
            if session_id and session_id in self._sessions:
                s = self._sessions[session_id]
                s.updated_at = now
                return s

            new_id = session_id or f"sess_{secrets.token_urlsafe(16)}"
            state = ConversationState(session_id=new_id, updated_at=now)
            self._sessions[new_id] = state
            return state

    def save(self, state: ConversationState) -> None:
        now = time.time()
        state.updated_at = now
        with self._lock:
            self._cleanup_expired(now)
            self._sessions[state.session_id] = state

    def reset(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id] = ConversationState(session_id=session_id, updated_at=time.time())
