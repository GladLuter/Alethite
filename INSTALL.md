**English** · [Русский](INSTALL.ru.md) · [Українська](INSTALL.uk.md)

# Installing CacheGuard on a Windows computer

All commands are for PowerShell.

## What you need first

- **The Claude Code desktop app**, engine 2.1.295 or newer. To check the version:
  ```powershell
  Get-ChildItem "$env:USERPROFILE\.claude\projects" -Recurse -Filter *.jsonl | Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content -Tail 50 | Select-String '"version":"([^"]+)"' | Select-Object -Last 1 | ForEach-Object { $_.Matches[0].Groups[1].Value }
  ```
- **Python 3.8 or newer, and numpy.**
  - Check: `python -c "import numpy; print(numpy.__version__)"`.
  - No numpy: `python -m pip install numpy`. Install it from an ordinary PowerShell window, not from inside Claude: the `pip` of Claude Desktop (the Microsoft Store build) puts packages into its own sandbox, and the ordinary Python does not see them.
- **Ollama with an embedding model**, for the search by meaning. If there is no Ollama yet, install it from ollama.com. Then:
  ```powershell
  ollama pull embeddinggemma
  ```
  That is about 622 MB. Without the model the mod works, but the search goes by words only.
- **git.**

## Installing

1. **Get the code** (branch `master`):
   ```powershell
   git clone https://github.com/GladLuter/Claude-CacheGuard.git C:\_git\Claude-CacheGuard
   cd C:\_git\Claude-CacheGuard
   ```
2. **Find the desktop's engine.** The `claude` on PATH is a separate CLI and often older, so the desktop's own `claude.exe` is needed:
   ```powershell
   $CC = (Get-ChildItem "$env:APPDATA\Claude\claude-code\*\*\claude.exe", "$env:LOCALAPPDATA\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code\*\*\claude.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1).FullName; $CC
   ```
   If nothing is found, search wider (this is slow):
   ```powershell
   $CC = (Get-ChildItem $env:LOCALAPPDATA, $env:APPDATA -Recurse -Filter claude.exe -ErrorAction SilentlyContinue | Where-Object FullName -like '*claude-code*' | Sort-Object LastWriteTime | Select-Object -Last 1).FullName; $CC
   ```
3. **Install the mod:**
   ```powershell
   & $CC plugin marketplace add C:\_git\Claude-CacheGuard
   & $CC plugin install cacheguard@cacheguard-local
   ```
   Then restart the desktop app.
4. **Build the index** of this computer's conversations. The first time takes a few minutes:
   ```powershell
   python C:\_git\Claude-CacheGuard\plugin\scripts\cg.py index
   ```
   - `--lang ru` or `--lang uk` before `index` sets the language of the scripts' messages and errors in the terminal; without it they are in English.
   - If sync is already set up on this computer (see below) and the index is new, run `cg.py sync` there first: this computer's records and vectors come back from its folder and need not be computed again.
5. **Compute the vectors.** This takes hours: on the i3 the whole history took about 6 hours, so it is best run overnight. Once stopped, it goes on from where it was:
   ```powershell
   python C:\_git\Claude-CacheGuard\plugin\scripts\cg.py embed --max-seconds 100000
   ```
   After that, new records are embedded by themselves at the start of a session, up to 2 minutes at a time.
   - **Laptop:** keep it awake during the run. On the charger it falls asleep without input and stops the run. After waking up, simply run the command again.
   - **Built-in Intel graphics (Iris Xe):** the one-off run is about twice as fast on it, and the vectors are the same (measured 2026-10-19 on MTLAPTOP: 2.44 long pieces per second against 1.15 on the processor). Ollama sees such a card only when started with `OLLAMA_IGPU_ENABLE=1`. Stop the usual Ollama in the tray, then in a PowerShell window:
     ```powershell
     $env:OLLAMA_IGPU_ENABLE = '1'; ollama serve
     ```
     Run the computation in another window. Then close this one and start Ollama again from the Start menu. Do not keep it on: text models are slower on the built-in card.
6. **Language.** By default the mod speaks English: the interface, the memory rule, the descriptions of `recall`, and the handoff. For Russian: `/config`, the `cacheguard` row → **Language / Язык / Мова** → `ru`; for Ukrainian — `uk`.
7. **Check:** in a new chat type `/cg-health`. It should show the version, the index, the auto-memory folder, the project, the vectors "N of M" and a sync line.

## Shared memory of two computers (stage 2)

Each computer puts its new conversations, vectors and auto-memory facts into a shared folder and takes the other's from there.
- It writes only into its own subfolder (`<folder>\<computer name>\`), so the index is not damaged even when both computers work at the same time.
- Without the network folder everything works as before; the sync catches up when the folder is reachable again.

1. **Create the folder** on the network drive, once, for example `\\NAS\share\cacheguard`. The mod does not create it itself: if the drive is not connected, it shows an error instead of writing to the local disk.
2. **On each computer**, set it: `/config` → the `cacheguard` row → **Sync folder**.
3. **The first exchange.** The whole history is tens of megabytes. The mod syncs for at most 60 seconds at a time and goes on at the next run, but the first time is easier from a terminal — first on the i3, then on the laptop:
   ```powershell
   python C:\_git\Claude-CacheGuard\plugin\scripts\cg.py sync --sync-dir \\NAS\share\cacheguard --max-seconds 3600
   ```
   Then once more on the i3, to take in the laptop's history. The vectors travel with the records: the other computer's need not be computed again if both use the same embedding model.
4. **From then on by itself:** at the start of a session, after the index and the vectors. By hand: `/cg-sync`; the answer says how much was pushed and taken in, and any errors.
5. **Check:** `recall` on the laptop finds the morning's conversation from the i3, and `/cg-health` shows both computers and the numbers of their last batches.

What else to know:
- **Short memory** is synced for the project of the current chat.
  - Facts created on this computer go to the shared folder.
  - The other computer's facts are copied into the auto-memory folder when they are not there, and a pointer line is added to `MEMORY.md`.
  - A file of your own with the same name is never overwritten: the other version is put beside it as `name--COMPUTER.md`. An imported fact you deleted does not come back.
- **A project** is recognised by its path without the home folder: `C:\Users\AIJ\work\x` and `C:\Users\gladl\work\x` are one project. Paths outside the profile (`C:\_git\…`) match anyway.
- **A broken batch** (a dropped network, a disk failure) stops taking in only from its computer; the error is shown in `/cg-health` at every sync. If the batch cannot be restored: `/cg-sync skip <computer> <number>`. The skip is shown in `/cg-health`.
- **Deleting a record** (say, a secret the filter missed) is only possible explicitly, and the deletion reaches the other computer:
  ```powershell
  python C:\_git\Claude-CacheGuard\plugin\scripts\cg.py forget --conv <conversation id> --pos <pos>
  ```
  `conv` and `pos` come from the line of a `recall` hit. Nothing else ever leaves the index: neither on a rebuild nor when Claude Code rewrites or deletes transcripts.

## Memory compression (stage 3)

The mod compresses what it puts into the model's context itself: the results of `recall` and `recall_more` and the quotes of the conversation in a handoff. The compression is mechanical, with no model calls. Code, paths, links, numbers, names and negations are never touched.

- **Level:** `/config` → the `cacheguard` row → **Memory compression**.
  - `off` — no compression;
  - `lite` — spaces and markup;
  - `full` — plus function words;
  - `ultra` — plus punctuation and capitals (the default).
- **To see what a level does** on any text:
  ```powershell
  python C:\_git\Claude-CacheGuard\plugin\scripts\cg.py compress --level ultra --file note.txt
  ```
- **How much memory is in the context:** `/cg-stats` writes a "Memory in context" section into its note: the memory rule, `MEMORY.md`, the results of `recall` and their cost, the handoffs. `/cg-health` shows the compression level and the size of `MEMORY.md`.
- If the worker understands the compressed excerpts worse, set `full` or `lite`.

## Good to know

- **The mod is read straight from the repository folder.**
  - Keep the folder on the `master` branch. If it used to be on `stage1`, run `git checkout master; git pull` once.
  - After a `git pull`, type `/reload-plugins` in Claude Code: no need to reinstall.
- **The index and the vectors** live in `%USERPROFILE%\.claude\cacheguard\index.db` and never get into the repository. Each computer keeps its own index; the shared sync folder only carries batches between them.
- **`/cg-index rebuild`** re-reads every transcript and adds only what is missing: the vectors stay and nothing is deleted.
- **Settings:** `/config`, the `cacheguard` rows: the language, the sync folder, the memory compression, the band thresholds, the limits line, the Python command (if it is `py`, not `python`) and the embedding model.
- **The band above the prompt** is one area shared by all mods. The CacheGuard band is drawn in a frame of its own, with its title, and the bands of other mods stay under it.
- **`/cg-stats`** writes measurements to Obsidian through the `obsidian` MCP server, which must be connected on this computer. If Obsidian is unavailable, the measurement waits in a queue and goes out at the next run.
