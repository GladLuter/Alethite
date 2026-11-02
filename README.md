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
- one short rule to the system prompt: write decisions down at once, save an understood error as a lesson, check memory before proposing;
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

**Lessons** (stage 4). An understood error is saved as a lesson in the index, not in the auto-memory that goes into every request.
- The mod brings a lesson back mechanically and only where it fits: beside an owner's message close in meaning (hidden context, at most 2, each once a chat), and as one stop before a command that holds its trigger.
- No lessons, or none fits — zero tokens. `/cg-health` shows what they cost.

**Searching by an error's traces** (stage 4). Tool errors go into the index; `recall` searches several wordings at once (the error text, the action, the cause).

**A background job** (stage 4, Windows). A Task Scheduler task keeps the index, the vectors (on mains power) and the sync up to date with no chat open.

**Encryption** (stage 5, Windows, optional). The index on disk and everything a computer puts in the sync folder are encrypted. The key sits in `settings.json`, protected by Windows.
- `/cg-encrypt on` turns it on. The recovery code is shown once, in a window, and belongs in a password manager. A second computer joins with `join`.
- The ciphers are chosen in `settings.json`, one for the index (`indexCipher`) and one for the sync folder (`shareCipher`). Left alone, both stay SQLCipher.
  - Index: SQLCipher (needs the `sqlcipher3` package) or EFS, the file encryption of Windows itself (needs Windows Pro and an NTFS drive, no packages).
  - Sync folder: SQLCipher or AES-GCM, which is built into Windows, no packages.
- Smart App Control may refuse to load `sqlcipher3`, so under it only EFS and AES-GCM work. The `cryptography` package is no longer needed.
- With EFS, a copy of the index on a USB stick or over the network comes out unencrypted, and the files open only under the same Windows account.
- Claude Code's own transcripts, each project's auto-memory files and the handoffs stay plain.
- Until you run `/cg-encrypt on`, nothing changes for you.

**Languages.** English (default), Russian, Ukrainian: the interface, the texts for the model and the handoff.

## Commands

| command | what it does |
|---|---|
| `/cg-health` | checks the mod, the index, the vectors, the memory, the sync and the compression |
| `/cg-band [off]` | shows the band now, or back to the thresholds |
| `/cg-index [rebuild]` | updates the index (rebuild: re-read everything, add what is missing) |
| `/cg-sync [skip <computer> <N>]` | syncs with the other computers now |
| `/cg-stats [days]` | cost measurement, written to Obsidian |
| `/cg-lessons [forget <pos>]` | the lessons of this project; delete one |
| `/cg-job [on [minutes] \| off]` | the background job (Windows): on, off, its state |
| `/cg-encrypt [on \| join \| off \| status]` | encryption of the index and the sync folder (Windows): on, join a second computer, off, its state |
| `/cg-index-dir [folder]` | moves the index to another folder on a local disk (Windows), without a folder a window asks for one |

## Settings (the `settings.json` file)

The file `%USERPROFILE%\.claude\cacheguard\settings.json` is one per computer and stays in this folder even when the index has moved. The mod creates it at the first start with all the current values, so you only open it and edit. The mod reads the file at every load, and its values win over `/config`. After an edit, type `/reload-plugins`. Backslashes in JSON are doubled.

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
| `lessons` | bring lessons back to messages and commands | `true` |
| `lessonsMinScore` | how close a message must be (0–1) | `0.45` |
| `indexCipher` | how the index is encrypted (`/cg-encrypt on` writes it itself) | `"sqlcipher"` (also `"efs"`) |
| `shareCipher` | how the sync folder is encrypted (`/cg-encrypt on` writes it itself). `"cryptography"` means the AES-GCM of Windows, no package needed | `"sqlcipher"` (also `"cryptography"`) |
| `indexDir` | the index folder (written by `/cg-index-dir`, do not edit by hand) | no key (the index is in the standard folder) |

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
- the `sqlcipher3` package for the SQLCipher ciphers (optional, Windows only);
- Windows Pro and an NTFS drive for the EFS cipher (optional);
- git.

Installation: [INSTALL.md](INSTALL.md).

## Documents

| document | English | Русский | Українська |
|---|---|---|---|
| Installation | [INSTALL.md](INSTALL.md) | [INSTALL.ru.md](INSTALL.ru.md) | [INSTALL.uk.md](INSTALL.uk.md) |

## Privacy

- The index and the vectors hold personal conversations. They stay on the owner's computers and the owner's network folder: never in this repository, never sent anywhere else.
- The only network calls are to the local Ollama and to the shared folder.
