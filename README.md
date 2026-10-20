**English** · [Русский](README.ru.md) · [Українська](README.uk.md)

# Claude-CacheGuard

A mod for Claude Code that makes long-lived "worker" chats cheaper without taking their memory away.

## Why

An audit of the owner's transcripts (May–October 2026, at API prices) showed where the money goes:
- 89% is spent re-sending context that already exists (cache writes and reads);
- 32% is the first message after a pause of over an hour in a big chat, when the one-hour cache has expired and the whole context is written again at double price.

So the expensive part is not what is typed, but coming back to a big, idle chat.

## What it does

**Cache guard.**
- The status line shows the size of the context and the minutes left in the cache.
- When you come back to a big chat after a pause, a band above the prompt shows what sending there costs now.
- The **New worker** button starts a clean chat: a mechanical handoff (no model calls) plus the message you already typed.

**Short memory.** Claude Code's own auto-memory. The mod adds:
- one short rule to the system prompt: write pitfalls and decisions down at once, check memory before proposing;
- a toast whenever a fact is saved.

**Long memory.** Every past conversation of every project, subagents included, in a local SQLite index (FTS5) with vectors from a local Ollama model.
- The worker calls `recall` (hybrid search by words and by meaning) and `recall_more` (read around a hit).
- Secrets are hidden before indexing.
- Nothing that got into the index is ever lost: rebuilds and rewritten transcripts only add.

**Two computers** (stage 2). A shared folder on a network drive carries the index, the vectors and the memory facts.
- Each computer writes only its own numbered batches; the merge is mechanical and repeatable.
- Without the folder everything works locally.

**Memory compression** (stage 3). What the mod gives the model (search results, handoff quotes) is squeezed mechanically. Four levels, `ultra` by default; code, paths and negations are never touched.
- `/cg-stats` measures how much memory sits in the context.

**Languages.** English (default), Russian, Ukrainian: the interface, the texts for the model and the handoff.

## Commands

| command | what it does |
|---|---|
| `/cg-health` | checks the mod, the index, the vectors, the memory, the sync and the compression |
| `/cg-band [off]` | shows the band now, or back to the thresholds |
| `/cg-index [rebuild]` | updates the index (rebuild: re-read everything, add what is missing) |
| `/cg-sync [skip <computer> <N>]` | syncs with the other computers now |
| `/cg-stats [days]` | cost measurement, written to Obsidian |

## Settings (the `settings.json` file)

The file `%USERPROFILE%\.claude\cacheguard\settings.json` is one per computer and sits beside the index. The mod creates it at the first start with all the current values, so you only open it and edit. The mod reads the file at every load, and its values win over `/config`. After an edit, type `/reload-plugins`. Backslashes in JSON are doubled.

| key | what it sets | default |
|---|---|---|
| `language` | language | `"en"` (also `"ru"`, `"uk"`) |
| `compression` | memory compression | `"ultra"` (also `"off"`, `"lite"`, `"full"`) |
| `syncDir` | sync folder | `""` (empty: no sync) |
| `bandMinTokens` | band threshold: chat size, tokens | `100000` |
| `bandIdleMinutes` | band threshold: idle time, minutes | `55` |
| `showLimits` | limits line in the band | `true` |
| `python` | Python command | `"python"` |
| `embedModel` | Ollama embedding model | `"embeddinggemma"` |

The file may hold only the keys you change. For example:

```json
{
  "language": "ru",
  "syncDir": "Z:\\cacheguard"
}
```

## Requirements

- The Claude Code desktop app (engine 2.1.295 or newer);
- Python 3.8+ with numpy;
- Ollama for the search by meaning (optional);
- git.

Installation: [INSTALL.md](INSTALL.md).

## Documents

| document | English | Русский | Українська |
|---|---|---|---|
| Installation | [INSTALL.md](INSTALL.md) | [INSTALL.ru.md](INSTALL.ru.md) | [INSTALL.uk.md](INSTALL.uk.md) |
| Stage 1 design | [design](docs/specs/2026-10-13-worker-memory-design.md) | [дизайн](docs/specs/2026-10-13-worker-memory-design.ru.md) | [дизайн](docs/specs/2026-10-13-worker-memory-design.uk.md) |
| Stage 1 plan | [plan](docs/plans/2026-10-13-stage1-plan.md) | [план](docs/plans/2026-10-13-stage1-plan.ru.md) | [план](docs/plans/2026-10-13-stage1-plan.uk.md) |
| Stage 2 spec | [spec](docs/specs/2026-10-19-stage2-shared-memory-design.md) | [спека](docs/specs/2026-10-19-stage2-shared-memory-design.ru.md) | [специфікація](docs/specs/2026-10-19-stage2-shared-memory-design.uk.md) |
| Stage 3 spec | [spec](docs/specs/2026-10-19-stage3-memory-archiver-design.md) | [спека](docs/specs/2026-10-19-stage3-memory-archiver-design.ru.md) | [специфікація](docs/specs/2026-10-19-stage3-memory-archiver-design.uk.md) |

The design documents were written in Russian. The English and Ukrainian versions are translations; where they differ, the Russian original is right.

## Privacy

- The index and the vectors hold personal conversations. They stay on the owner's computers and the owner's network folder: never in this repository, never sent anywhere else.
- The only network calls are to the local Ollama and to the shared folder.
