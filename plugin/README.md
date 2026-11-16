# Alethite

Alethite is a plugin for the Claude Code desktop app on Windows. It shows what the next message into a big idle chat will cost once the prompt cache has expired, and offers a fresh chat that gets a handoff of the old one. It keeps a local, searchable memory of past chats and lessons from past errors, and can share that memory between your computers through your own network folder.

It needs Python 3.8 or later. Ollama is optional, for the vector search.

Install in Claude Code 2.1.275 or later: `/plugin install alethite --marketplace GladLuter/Alethite`

Everything stays on your computers. The only network calls go to your local Ollama, to your sync folder and, for `/al-stats`, to your Obsidian MCP server. A Task Scheduler task keeps the index fresh when no chat is open.

Setup, settings and commands: the [main README](https://github.com/GladLuter/Alethite#readme) and [INSTALL](https://github.com/GladLuter/Alethite/blob/master/INSTALL.md).

License: MIT.
