"""Application settings, read from environment variables and `.env`.

Every field can be set by an environment variable of the same name in
upper case, e.g. `rate_limit_per_minute` by `RATE_LIMIT_PER_MINUTE`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),  # `model` is a field name
    )

    # --- OpenAI --------------------------------------------------------------
    openai_api_key: str | None = None
    # Any OpenAI-compatible endpoint (Azure, a gateway, a local server).
    # Leave unset for api.openai.com.
    openai_base_url: str | None = None
    model: str = "gpt-4o"
    max_tokens: int = 4096
    temperature: float = 0.7
    # Reasoning models only (o-series, gpt-5 family): "low" | "medium" | "high".
    # When set, temperature is not sent - those models reject it.
    reasoning_effort: str | None = None

    # --- Assistant -----------------------------------------------------------
    # Replaces the built-in prompt in app/assistant/prompts.py.
    system_prompt: str | None = None
    # Lets a request replace the system prompt via `system_prompt` in the body.
    # Off by default: on a public chatbot that field is a one-line jailbreak.
    allow_system_prompt_override: bool = False
    # Past messages replayed to the model. A turn that searches uses ~4
    # (user, tool call, tool result, reply).
    history_window: int = 24
    # Model <-> tool round trips per message before the model must answer.
    max_tool_rounds: int = 3
    # Conversations kept in memory; the least recently used are dropped first.
    max_sessions: int = 10_000

    # --- DigiNiwas properties API --------------------------------------------
    properties_api_base_url: str = "https://backend-diginiwas.onrender.com"
    # Generous: a sleeping Render instance can take ~50s to wake up.
    properties_api_timeout: float = 60.0
    # Listings fetched per search, so the most cards shown per reply.
    max_property_results: int = 6
    # Link for the card's "View Property" button, e.g.
    # "https://diginiwas.com/property/{id}". Unset -> `url` is null.
    property_url_template: str | None = None

    # --- Property insights ---------------------------------------------------
    # How far out to look for comparable listings.
    comparable_radius_km: float = 3.0
    # A comparable's size may differ from the subject's by this much.
    comparable_area_tolerance: float = 0.25
    # Closest matches kept per snapshot.
    max_comparables: int = 30
    # Which web search locality sources and quoted trends come from.
    # "serper" needs only SERPER_API_KEY and returns Google's own results;
    # "brave" needs only BRAVE_SEARCH_API_KEY; "google" needs the two below.
    search_provider: Literal["google", "brave", "serper"] = "serper"
    serper_api_key: str | None = None
    brave_search_api_key: str | None = None
    # Google Programmable Search, for locality source links. Both are needed,
    # or the snapshot simply comes back without sources. The API key must have
    # the Custom Search API enabled; the engine id is a search engine's `cx`.
    google_search_api_key: str | None = None
    google_search_engine_id: str | None = None
    locality_sources_limit: int = 3
    # Quote a locality trend from a web page found by search, when one states
    # it plainly. The figure is someone else's claim rather than a
    # measurement, so it ships with its quote, its source and "Low"
    # confidence. Takes effect only once the search provider is configured;
    # set to false to hide the trend while keeping locality sources.
    web_trend_enabled: bool = True
    # When fewer than 3 comparable rentals exist, quote a locality's average
    # rent from a web page for the rental yield, labelled with its source and
    # scope. Takes effect only once the search provider is configured.
    web_rent_enabled: bool = True
    # Months a year a rental is assumed to sit empty, for net rental yield.
    rental_vacancy_months: float = 1.0

    # --- Server --------------------------------------------------------------
    cors_origins: list[str] = ["*"]
    # Chat messages allowed per client IP per minute; 0 turns limiting off.
    # Every message costs OpenAI tokens, so keep this on for a public server.
    rate_limit_per_minute: int = 20
    log_level: str = "INFO"

    @property
    def resolved_api_key(self) -> str | None:
        """The OpenAI key to use, or None if it is unset or blank."""
        return (self.openai_api_key or "").strip() or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
