#!/bin/bash
# Установка стартового набора скиллов и агентов для Claude Code
set -e

cd "$(dirname "$0")"

mkdir -p ~/.claude/skills ~/.claude/agents
cp -R skills/* ~/.claude/skills/
cp -R agents/* ~/.claude/agents/
cp ROUTING.md ~/.claude/

echo "✅ Готово!"
echo
echo "Скиллы → ~/.claude/skills/"
echo "  тексты:  kp-copy, copywriting-ru, post-ru, social-ru, hooks-ru, story-ru,"
echo "           ad-creative-ru, persuasion-ru, red-pen, humanize-ru, voice-ru"
echo "  визуал:  art-direction, video-editing, macos-icons"
echo "  работа:  commit, install-mcp"
echo
echo "Агенты → ~/.claude/agents/"
echo "  marketer, research, reviewer, code-reviewer, workflow-auditor,"
echo "  anthropic-docs, designer, design-critic"
echo
echo "Карта связки → ~/.claude/ROUTING.md"
echo "  Подключите её, добавив в ~/.claude/CLAUDE.md строку:  @~/.claude/ROUTING.md"
echo
echo "Учебник → guide/claude-code-uchebnik-ru.html (откройте в браузере)"
echo
echo "Перезапустите Claude Code, чтобы изменения подхватились."
