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
6. **Language.** By default the mod speaks English: the interface, the memory rule, the descriptions of `recall`, and the handoff. For Russian, open the settings file `%USERPROFILE%\.claude\cacheguard\settings.json` (the mod created it at the first start), set `"language": "ru"` in it and type `/reload-plugins`. For Ukrainian, set `"uk"`.
7. **Check:** in a new chat type `/cg-health`. It should show the version, the index, the auto-memory folder, the project, the vectors "N of M" and a sync line.

## Shared memory of two computers (stage 2)

Each computer puts its new conversations, vectors and auto-memory facts into a shared folder and takes the other's from there.
- It writes only into its own subfolder (`<folder>\<computer name>\`), so the index is not damaged even when both computers work at the same time.
- Without the network folder everything works as before; the sync catches up when the folder is reachable again.

1. **Create the folder** on the network drive, once, for example `\\NAS\share\cacheguard`. The mod does not create it itself: if the drive is not connected, it shows an error instead of writing to the local disk.
2. **On each computer**, set it in the settings file `%USERPROFILE%\.claude\cacheguard\settings.json`: `"syncDir": "\\\\NAS\\share\\cacheguard"`, then `/reload-plugins`. Backslashes in JSON are doubled.
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

- **Level:** the `"compression"` key in the settings file `%USERPROFILE%\.claude\cacheguard\settings.json`, then `/reload-plugins`.
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

## Lessons, the background job, searching for errors (stage 4)

After `git pull` and `/reload-plugins`:

1. **Rebuild the index once**, so the tool errors of old conversations get into it:
   ```powershell
   python C:\_git\Claude-CacheGuard\plugin\scripts\cg.py index --rebuild
   ```
   A rebuild only adds what is missing. The vectors of the new records are computed by the background job or the next chat start.
2. **Turn the background job on** on each computer: in a chat, `/cg-job on` (or `/cg-job on 15` — every 15 minutes).
   - The task "CacheGuard" appears in the Task Scheduler: no window, only while you are logged on, on a battery the vectors wait for the mains.
   - It takes its settings from the same `settings.json` file as the mod.
   - `/cg-job` shows the state and the last run, `/cg-job off` removes the task.
   - If the task does not run, check that Python is installed from python.org (`pythonw.exe` must lie beside `python.exe`).
3. **Lessons** work by themselves: the worker saves an understood error with `lesson_save`. `/cg-lessons` lists the project's lessons, `/cg-lessons forget <pos>` deletes one.
   - A lesson comes beside a message close in meaning and stops once a command that holds its trigger.
   - If lessons come where they do not belong, raise `lessonsMinScore` in `settings.json` to `0.55`; if they never come, lower it to `0.4`. To turn them off: `"lessons": false`.

## Encryption (stage 5)

Optional, and Windows only. The index (with its backup `index.before-stage2.db`) and everything a computer puts in the sync folder are encrypted. In the folder anyone can read the computer's name, the batch numbers, their count and sizes, and the label of a generation (`gen-0001\generation.json`) also names the cipher, the key's fingerprint and the date. The fingerprint does not give the key away.
- The key sits in `settings.json`, protected by Windows (DPAPI). On another computer or under another Windows account the file gives no key.
- Without the `sqlcipher3` package and without `/cg-encrypt on`, nothing changes for you.

1. **Install the package** on each computer from an ordinary PowerShell window, not from inside Claude: the `pip` of Claude puts packages into its own sandbox, and the background job will not see them.
   ```powershell
   python -m pip install sqlcipher3
   ```
   If you choose `"shareCipher": "cryptography"`, also install `python -m pip install cryptography`.
2. **The first computer:** `/reload-plugins`, then `/cg-encrypt on`. The "Claude-CacheGuard" windows may appear behind other windows.
   - The first window shows the **recovery code**, once. Ctrl+C copies the text of the window: paste the code into a password manager. Without it the encrypted history cannot be read if the computers or their Windows accounts are lost.
   - The question "Is the recovery code saved?" defaults to No. Encryption starts only after Yes.
   - The index and its backup are encrypted. The unencrypted copies (`index.db.plain`) are deleted only after Yes. After No they stay, and `/cg-encrypt on` asks again.
   - The sync folder: the computer puts its own subfolder out again, encrypted. The older unencrypted data there is deleted only after Yes, and only once the new data is fully out.
3. **The second computer:** `git pull`, `/reload-plugins`, `/cg-encrypt join`. The recovery code is entered in a window with hidden input. One key serves all computers, so `on` on the second one refuses and points to `join`.
4. **Check:** `/cg-health` on both computers says "Encryption: on (sqlcipher)". If something is unfinished, the lines under it say what to repeat. `/cg-encrypt status` shows the state alone.

What else to know:
- **Commands:** `/cg-encrypt on | join | off | status`. With no argument it runs `status`.
  - `off` decrypts the index, puts this computer's subfolder out unencrypted again, and removes the key from `settings.json`. Turn it off on each computer. A new `on` makes a new key, and the other computers join again.
  - If the key is lost (another Windows account, a reinstall): `/cg-encrypt join` with the recovery code.
- **Until the second computer has joined**, the first takes nothing from it, and `/cg-health` names it: "run `join` on it". Its history is not lost: after `join` it puts it out encrypted.
- **The time limit.** One run is limited to about 10 minutes (the limit of the desktop app). A big first push may not fit: the reply says it is not finished. Repeat `/cg-encrypt on` (or `join`): it goes on from where it stopped, and every step is safe to repeat. The background job also pushes the rest, but the older unencrypted data in the folder is deleted only by repeating the command, after Yes.
- **How the folder is encrypted** is the `shareCipher` key in `settings.json`: `sqlcipher` (the default) or `cryptography`. `/cg-encrypt on` and `join` write it into the file themselves, and the file wins over `/config`, so change it later in the file, then `/reload-plugins` and `/cg-encrypt on`. A computer reads the others' data whichever way it was encrypted, if the package is installed.
- **Speed.** On the encrypted index `recall` is about 0.08 s slower (measured 2026-10-27 on 24.7 thousand vectors). The gap grows with the number of vectors.
- **What stays plain:**
  - Claude Code's conversations, `%USERPROFILE%\.claude\projects\*.jsonl`;
  - the auto-memory files of each project on the local disk (`.md`): Claude Code reads them;
  - the handoffs, `%USERPROFILE%\.claude\cacheguard\handoffs`: the same text goes into the chat unencrypted.

## Good to know

- **The mod is read straight from the repository folder.**
  - Keep the folder on the `master` branch. If it used to be on `stage1`, run `git checkout master; git pull` once.
  - After a `git pull`, type `/reload-plugins` in Claude Code: no need to reinstall.
- **The index and the vectors** live in `%USERPROFILE%\.claude\cacheguard\index.db` and never get into the repository. Each computer keeps its own index; the shared sync folder only carries batches between them.
- **`/cg-index rebuild`** re-reads every transcript and adds only what is missing: the vectors stay and nothing is deleted.
- **Settings:** a file of its own, `%USERPROFILE%\.claude\cacheguard\settings.json`, one per computer, beside the index. The keys: `language`, `compression`, `syncDir`, `bandMinTokens` and `bandIdleMinutes` (the band thresholds), `showLimits` (the limits line), `python` (the Python command, if it is `py`, not `python`), `embedModel`, `lessons` and `lessonsMinScore` (the lessons), `shareCipher` (the sync folder encryption). The table with the defaults is in [README.md](README.md).
  - The mod creates the file at the first start with all the current values, so you only open it and edit. A file may hold just the keys you change.
  - The mod reads it at every load: the start of a chat and `/reload-plugins`. After an edit, type `/reload-plugins`.
  - Its values win over `/config`. The desktop app has no settings window for mods, so the settings live in a file. `/config` in a terminal `claude` still works, but where they differ, the file wins.
  - Backslashes in JSON are doubled: `"Z:\\cacheguard"`.
  - `/cg-health` shows a line with the file's path.
  - The `encryptionKey` line is written by the mod itself: it is the encryption key, protected by Windows. Do not edit it.
  - A broken file (invalid JSON) gives a toast naming the file, and the mod keeps the `/config` values. An unknown key or a value of the wrong type gives the same toast, and that key is skipped.
- **The band above the prompt** is one area shared by all mods. The CacheGuard band is drawn in a frame of its own, with its title, and the bands of other mods stay under it.
- **`/cg-stats`** writes measurements to Obsidian through the `obsidian` MCP server, which must be connected on this computer. If Obsidian is unavailable, the measurement waits in a queue and goes out at the next run.
