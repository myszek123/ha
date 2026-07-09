# Scripts

Reusable Home Assistant scripts. Live HA uses a single `scripts.yaml`; merge entries from here when deploying.

## Deploy to CT 101 (HA)

```bash
# Copy script block into /opt/ha/config/scripts.yaml under the matching key, e.g.:
#   say_pokoj_rodzinny:
#     alias: ...
# Or rsync and merge manually.

ssh root@192.168.1.201
# edit /opt/ha/config/scripts.yaml — paste YAML from say-pokoj-rodzinny.yml
# prefix top-level key: say_pokoj_rodzinny:

# Reload without full restart:
curl -X POST -H "Authorization: Bearer $HA_TOKEN" \
  http://192.168.1.201:8123/api/services/script/reload
```

## Scripts

| File | Entity | Purpose |
|------|--------|---------|
| `say-pokoj-rodzinny.yml` | `script.say_pokoj_rodzinny` | Wake Yamaha soundbar (or LG TV), speak via Grok TTS |

### `say_pokoj_rodzinny`

- **Soundbar:** `media_player.pokoj_rodzinny` (Yamaha YAS-408, MusicCast network standby)
- **TV:** `media_player.lg_webos_tv_f532` (must be powered on)
- **TTS:** `tts.xai_tts`, voice `ara`, language `pl`

**Actions dev tool (YAML mode):**

```yaml
action: script.say_pokoj_rodzinny
data:
  message: "Test z Home Assistanta"
  target: soundbar
```

**Claude/Grok skill:** `~/.claude/skills/saymeviaha/run.sh "<message>"` (not stored in this repo).