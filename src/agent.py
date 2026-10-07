"""Google ADK narrative-editor agent + a small synchronous wrapper (DeckStudio) used by the
Streamlit app and the CLI.

Why ADK instead of a single generate_content() call:
  * Session state  - the narrative, facts and revision log live in the ADK session, so every
                     chat turn edits the SAME draft and sees all previous refinements.
  * Memory         - conversation history is kept per session; style preferences are stored in
                     the user-scoped key "user:preferences" and carried into every new session.
  * Tools          - the model edits the deck only through validated function tools.
  * Persistence    - set DECK_SESSION_DB (e.g. sqlite+aiosqlite:///deck_sessions.db) to keep
                     sessions and preferences across app restarts.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import threading
import time
import uuid

from .agent_tools import ALL_TOOLS

APP_NAME = "novaretail_deck_studio"
DEFAULT_MODEL = os.getenv("DECK_AGENT_MODEL", "gemini-3.8-flash")

INSTRUCTION = """You are a senior strategy consultant and executive-communications editor helping a
user refine a 5-slide NovaRetail Quarterly Business Review deck.

Slides: 1 = KPIs + executive summary, 2 = revenue performance, 3 = customer experience,
4 = operational risk, 5 = recommended actions + closing line.

How you work:
1. ALWAYS call get_deck_context first to read the facts, the CURRENT draft and the user's saved preferences.
2. Change ONLY the sections the user asks about. If the request is deck-wide (e.g. "make it more
   executive-friendly"), update every affected section.
3. Make edits ONLY through the tools (update_text_section, update_bullets, update_actions).
   Never just print new text without saving it.
4. Use ONLY numbers that appear in the facts or the current draft. Never invent figures, targets or
   dates. KPI values are locked; you may only edit KPI labels.
5. Headlines must be insight-led (what it means, not just what happened). Bullets: 3-4 per slide,
   max 160 characters, each with a supporting number. Actions start with a strong verb and each is
   tied to an insight from slides 2-4.
6. Apply every saved user preference to every edit.
7. If the user states a LASTING preference ("always...", "I prefer...", "from now on...", "keep it..."),
   call remember_preference. Do not save one-off requests as preferences.
8. If a tool returns status "rejected", fix the listed issues and call it again (max 2 retries).
9. Finish with a brief reply (2-4 lines) saying which sections you changed and why. Do not repeat
   the full text; the user sees it in the editor.
10. If the user only asks a question, answer it from the facts without editing.
"""


def build_agent(model: str = DEFAULT_MODEL):
    from google.adk.agents import LlmAgent
    from google.genai import types
    return LlmAgent(
        name="deck_narrative_editor",
        model=model,
        description="Refines NovaRetail QBR deck narrative using validated, data-grounded edits.",
        instruction=INSTRUCTION,
        tools=ALL_TOOLS,
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
    )


async def _maybe(x):
    return await x if inspect.isawaitable(x) else x


class _LoopThread:
    """One long-lived event loop in a background thread, so ADK's async APIs can be used from
    synchronous code (Streamlit reruns, CLI) without 'event loop is closed' issues."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run(self, coro, timeout: float = 180):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)


class DeckStudio:
    """Synchronous facade over an ADK Runner + session service."""

    def __init__(self, model: str = DEFAULT_MODEL, db_url: str | None = None):
        _load_env()
        from google.adk.runners import Runner
        self._lt = _LoopThread()
        self.model = model
        self.session_service, self.storage = self._lt.run(self._make_service(db_url or os.getenv("DECK_SESSION_DB")))
        self.runner = Runner(agent=build_agent(model), app_name=APP_NAME, session_service=self.session_service)

    @staticmethod
    async def _make_service(db_url):
        from google.adk.sessions import InMemorySessionService
        if db_url:
            try:
                from google.adk.sessions import DatabaseSessionService
                return DatabaseSessionService(db_url=db_url), f"database ({db_url})"
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] DatabaseSessionService unavailable ({exc}); using in-memory sessions.")
        return InMemorySessionService(), "in-memory"

    # ------------------------------------------------------------------ sessions
    def start_session(self, user_id: str, state: dict) -> str:
        sid = f"deck-{uuid.uuid4().hex[:8]}"
        state = {**state, "revision_log": [{"time": time.strftime("%H:%M:%S"), "by": "system",
                                             "section": "all", "note": "session started from data-driven draft"}]}
        self._lt.run(_maybe(self.session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=sid, state=state)))
        return sid

    def get_state(self, user_id: str, session_id: str) -> dict:
        s = self._lt.run(_maybe(self.session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id)))
        return dict(s.state) if s else {}

    def push_state(self, user_id: str, session_id: str, delta: dict, note: str = "manual edit") -> None:
        """Write user-side changes (manual edits, uploaded narrative.json) into the ADK session via a
        state_delta event, so the agent always works on what the user currently sees."""
        from google.adk.events import Event, EventActions

        async def _go():
            s = await _maybe(self.session_service.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id))
            log = list(s.state.get("revision_log", []))
            log.append({"time": time.strftime("%H:%M:%S"), "by": "user", "section": "editor", "note": note})
            ev = Event(invocation_id=f"edit-{uuid.uuid4().hex[:8]}", author="user",
                       actions=EventActions(state_delta={**delta, "revision_log": log[-50:]}), timestamp=time.time())
            await _maybe(self.session_service.append_event(session=s, event=ev))
        self._lt.run(_go())

    # ------------------------------------------------------------------ chat
    def chat(self, user_id: str, session_id: str, message: str) -> dict:
        from google.genai import types

        async def _go():
            reply, calls = "", []
            content = types.Content(role="user", parts=[types.Part(text=message)])
            async for ev in self.runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
                for fc in ev.get_function_calls() or []:
                    calls.append(fc.name)
                if ev.is_final_response() and ev.content and ev.content.parts:
                    reply = "".join(p.text or "" for p in ev.content.parts if getattr(p, "text", None))
            return reply, calls
        reply, calls = self._lt.run(_go())
        st = self.get_state(user_id, session_id)
        return {"reply": reply or "(no text reply)", "tool_calls": calls, "narrative": st.get("narrative"),
                "preferences": st.get("user:preferences", []), "revision_log": st.get("revision_log", [])}


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def adk_configured() -> tuple[bool, str]:
    _load_env()
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true") and os.getenv("GOOGLE_CLOUD_PROJECT"):
        return True, f"Vertex AI · project {os.getenv('GOOGLE_CLOUD_PROJECT')}"
    if os.getenv("GOOGLE_API_KEY"):
        return True, "Gemini API key"
    return False, "Not configured - set GOOGLE_API_KEY or Vertex AI variables in .env"
