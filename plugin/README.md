# Alethite

Alethite is a plugin for the Claude Code desktop app on Windows. It shows what the next message into a big idle chat will cost once the prompt cache has expired, and offers a fresh chat that gets a handoff of the old one. It keeps a local, searchable memory of past chats and lessons from past errors, and can share that memory between your computers through your own network folder.

It needs Python 3.8 or later. Ollama is optional, for the vector search.

Install in Claude Code 2.1.275 or later: `/plugin install alethite --marketplace GladLuter/Alethite`

Everything stays on your computers. The only network calls go to your local Ollama, to your sync folder and, for `/al-stats`, to your Obsidian MCP server. A Task Scheduler task keeps the index fresh when no chat is open.

## What it runs, reads and sends

- **Programs.** The plugin runs one program: `python` with its own `scripts/cg.py`. The scripts read Claude Code's transcripts on this computer to build the index, search it, save lessons, write the handoff and count the figures for `/al-stats`. They start `git` to read a project's state for the handoff and `schtasks` to set up the background task.
- **Commands.** New worker and After reset run `/clear` in the chat, and `/model` when you picked a model other than the current one. Then they send the handoff and your draft as your message.
- **The handoff.** It holds the old chat's id, the path of its short memory, where it stopped (its last messages, shortened) and the git state of the repositories it worked in. It goes to Claude like any message you send.
- **What it adds to prompts.** Two short rules in the system prompt (how to use the memory, which subagent types to pick) and, before your message goes out, the lessons that fit it, marked `[al-lessons]`.
- **Obsidian.** Only `/al-stats` calls your Obsidian MCP server, with three tools: `list_vault_files`, `get_note_properties` and `create_vault_file`. It writes one note of figures per computer and day (tokens, prices, how full the limits are, the size of the memory) and a summary note. No chat text goes there.
- **Files.** It writes in `%USERPROFILE%\.claude\alethite` (its settings file `settings.json`, created on the first run, the index and the handoffs), in your sync folder if you set one, and in the folder you move the index to with `/al-index-dir`. With `/al-cleanup` on, it deletes the transcript of an old chat after a New worker handoff, once the index holds it. It does not change Claude Code's settings or any instructions file.
- **Keys.** It reads no API key or token. If you turn encryption on, its own key stays in `settings.json` under Windows DPAPI and is never sent.

Setup, settings and commands: the [main README](https://github.com/GladLuter/Alethite#readme) and [INSTALL](https://github.com/GladLuter/Alethite/blob/master/INSTALL.md).

License: MIT.
