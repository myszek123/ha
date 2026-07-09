"""Config flow for xAI TTS integration."""
from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from . import DOMAIN

CONF_API_KEY = "api_key"
CONF_VOICE = "voice"
CONF_LANGUAGE = "language"

VOICE_OPTIONS = {
    "eve": "Eve (Energetic, upbeat)",
    "ara": "Ara (Warm, friendly)",
    "rex": "Rex (Confident, clear)",
    "sal": "Sal (Smooth, balanced)",
    "leo": "Leo (Authoritative, strong)",
}

LANGUAGE_OPTIONS = {
    "en": "English",
    "auto": "Auto-detect",
    "zh": "Chinese (Simplified)",
    "fr": "French",
    "de": "German",
    "hi": "Hindi",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "pt-BR": "Portuguese (Brazil)",
    "pt-PT": "Portuguese (Portugal)",
    "ru": "Russian",
    "es-MX": "Spanish (Mexico)",
    "es-ES": "Spanish (Spain)",
    "tr": "Turkish",
    "vi": "Vietnamese",
}

XAI_TTS_URL = "https://api.x.ai/v1/tts"


async def _validate_api_key(api_key: str) -> str | None:
    """Validate the API key by making a minimal test request. Returns error key or None."""
    payload = {
        "text": "test",
        "voice_id": "eve",
        "language": "en",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                XAI_TTS_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status == 401:
                    return "invalid_auth"
                if response.status not in (200, 400):
                    # 400 could mean bad params but key is valid; anything else is a problem
                    return "cannot_connect"
    except aiohttp.ClientError:
        return "cannot_connect"
    return None


class XAITTSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for xAI TTS."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await _validate_api_key(user_input[CONF_API_KEY])
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id("xai_tts")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="xAI Text-to-Speech",
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): str,
                vol.Optional(CONF_VOICE, default="eve"): vol.In(VOICE_OPTIONS),
                vol.Optional(CONF_LANGUAGE, default="en"): vol.In(LANGUAGE_OPTIONS),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> XAITTSOptionsFlow:
        """Return the options flow."""
        return XAITTSOptionsFlow(config_entry)


class XAITTSOptionsFlow(config_entries.OptionsFlow):
    """Handle options for xAI TTS."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_voice = self._config_entry.data.get(CONF_VOICE, "eve")
        current_language = self._config_entry.data.get(CONF_LANGUAGE, "en")

        schema = vol.Schema(
            {
                vol.Optional(CONF_VOICE, default=current_voice): vol.In(VOICE_OPTIONS),
                vol.Optional(CONF_LANGUAGE, default=current_language): vol.In(LANGUAGE_OPTIONS),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
