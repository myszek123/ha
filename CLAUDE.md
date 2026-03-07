# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.



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
- `automations/` - Automation scripts and workflows

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
