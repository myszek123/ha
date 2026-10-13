# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠ Top-priority rule: away = hands off

**When the car is not at home, nothing in Home Assistant may affect the car in
any shape or form.** Away, the driver charges however they like (any SoC, time,
duration). It applies to the whole integration and every automation, and it
beats every other feature. Definition, allowed/forbidden lists, enforcement and
checklist: [`docs/AWAY-HANDS-OFF.md`](docs/AWAY-HANDS-OFF.md).

Before changing `custom_components/myszolot/` or `automations/`:
- car writes in the integration go **only** through
  `MyszolotCoordinator._async_call_car_service()` (it checks the gate);
- automation actions on car entities or the integration's controls sit under
  the canonical home condition (see the doc);
- `python3 -m pytest -q` must pass, including `tests/test_away_hands_off.py`;
  a new path that writes to the car gets an away test and a home control test;
- the PR says how the rule is kept.



This project contains Home Assistant automations for learning Claude Code.

## Workflow
1. Use yaml-writer subagent to draft automations
2. Use ha-validator subagent to check them
3. Keep each automation in its own file

## Subagent Routing
- Writing or editing YAML → yaml-writer
- Checking/linting → ha-validator
- Both tasks in one request → run sequentially




## Project Structure

This repository is organized for storing Claude Code agents and automations:

- `.claude/agents/` - Custom agents that can be invoked with the Agent tool
- `.claude/commands/` - Custom commands and shortcuts for Claude Code workflows
- `automations/` - Automation YAML (one file per automation)
- `scripts/` - Reusable HA scripts (merge into live `scripts.yaml` on deploy)
- `custom_components/myszolot/` - EV charging HACS integration
- `custom_components/xai_tts/` - Vendored Grok TTS integration (see UPSTREAM.md)

## Getting Started

Since this is a minimal project structure, here's how to extend it:

### Adding Custom Agents

Create agent files in `.claude/agents/` with clear names and documentation about their purpose. Agents should be self-contained and well-documented so future Claude instances understand their capabilities.

### Adding Commands

Custom commands can be stored in `.claude/commands/` to provide repeatable workflows for common tasks in this project.

### Adding Automations

Scripts and automation workflows can be placed in the `automations/` directory. Document their purpose and how to invoke them.

## Notes for Future Work

- When adding significant new functionality, update this CLAUDE.md with relevant architectural decisions and common development tasks
- Document any project-specific conventions or dependencies that aren't obvious from the code structure
- Keep automation scripts self-documenting with clear comments about their purpose
