# xAI TTS — vendored integration

Upstream: [therealakahn/ha-xai-tts](https://github.com/therealakahn/ha-xai-tts) (MIT-style community integration, not in HACS).

## Local patch

`tts.py` adds `auto` and `pl` to `SUPPORTED_LANGUAGES`. Upstream omits them but the xAI API accepts both; without this patch `tts.speak` with `language: pl` or `auto` fails in Home Assistant.

## Upgrade procedure

1. Compare upstream `custom_components/xai_tts/` with this copy.
2. Re-apply the `SUPPORTED_LANGUAGES` patch in `tts.py`.
3. Deploy to HA and restart the container.
4. Re-test `script.say_pokoj_rodzinny`.

## API key

Never commit API keys. Configure via **Settings → Devices & Services → xAI Text-to-Speech** on the live HA instance.