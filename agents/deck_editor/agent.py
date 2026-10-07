"""Entry point for the ADK developer UI:   adk web agents
Lets you chat with the deck-editor agent, see every tool call and inspect session state.
The bundled dataset is loaded into session state automatically on the first tool call."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root -> import src.*

from src.agent import build_agent  # noqa: E402

root_agent = build_agent()
