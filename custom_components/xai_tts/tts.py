"""xAI TTS platform for Home Assistant."""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

import aiohttp

from homeassistant.components.tts import (
    TextToSpeechEntity,
    TTSAudioRequest,
    TTSAudioResponse,
    Voice,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

XAI_TTS_URL = "https://api.x.ai/v1/tts"

VOICES = [
    Voice(voice_id="eve", name="Eve (Energetic, upbeat)"),
    Voice(voice_id="ara", name="Ara (Warm, friendly)"),
    Voice(voice_id="rex", name="Rex (Confident, clear)"),
    Voice(voice_id="sal", name="Sal (Smooth, balanced)"),
    Voice(voice_id="leo", name="Leo (Authoritative, strong)"),
]

CONF_API_KEY = "api_key"
CONF_VOICE = "voice"
CONF_LANGUAGE = "language"

SUPPORTED_LANGUAGES = [
    "auto", "en", "pl", "ar-EG", "ar-SA", "ar-AE", "bn", "zh", "fr", "de",
    "hi", "id", "it", "ja", "ko", "pt-BR", "pt-PT", "ru",
    "es-MX", "es-ES", "tr", "vi",
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xAI TTS from a config entry."""
    async_add_entities([XAITTSEntity(hass, config_entry)])


class XAITTSEntity(TextToSpeechEntity):
    """xAI TTS entity."""

    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize xAI TTS entity."""
        self.hass = hass
        self._config_entry = config_entry
        self._attr_unique_id = config_entry.entry_id
        self._attr_name = "xAI TTS"
        self.entity_id = "tts.xai_tts"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.entry_id)},
            name="xAI TTS",
            manufacturer="xAI",
        )

    @property
    def default_language(self) -> str:
        """Return the default language."""
        return self._config_entry.data.get(CONF_LANGUAGE, "en")

    @property
    def supported_languages(self) -> list[str]:
        """Return list of supported languages."""
        return SUPPORTED_LANGUAGES

    @property
    def supported_options(self) -> list[str]:
        """Return list of supported options."""
        return ["voice", "preferred_format"]

    @property
    def default_options(self) -> dict[str, Any]:
        """Return default options."""
        return {
            "voice": self._config_entry.data.get(CONF_VOICE, "eve"),
            "preferred_format": "mp3",
        }

    @callback
    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        """Return list of supported voices."""
        return VOICES

    async def async_stream_tts_audio(
        self, request: TTSAudioRequest
    ) -> TTSAudioResponse:
        """Stream TTS audio from xAI."""
        message = "".join([chunk async for chunk in request.message_gen])
        language = request.language or self.default_language
        voice_id = request.options.get(
            "voice", self._config_entry.data.get(CONF_VOICE, "eve")
        )

        api_key = self._config_entry.data[CONF_API_KEY]
        payload = {
            "text": message,
            "voice_id": voice_id,
            "language": language,
            "output_format": {
                "codec": "mp3",
                "sample_rate": 24000,
                "bit_rate": 128000,
            },
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async def audio_gen() -> AsyncGenerator[bytes, None]:
            session = async_get_clientsession(self.hass)
            try:
                async with session.post(
                    XAI_TTS_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        _LOGGER.error(
                            "xAI TTS error %s: %s", response.status, error_text
                        )
                        return
                    async for chunk in response.content.iter_chunked(8192):
                        if chunk:
                            yield chunk
            except aiohttp.ClientError as err:
                _LOGGER.error("xAI TTS aiohttp error: %s", err)
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("xAI TTS unexpected error: %s", err)

        return TTSAudioResponse(extension="mp3", data_gen=audio_gen())
