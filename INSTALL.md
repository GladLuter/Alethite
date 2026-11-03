**English** · [Русский](INSTALL.ru.md) · [Українська](INSTALL.uk.md)

# Installing Alethite on a Windows computer

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
   git clone https://github.com/GladLuter/Alethite.git C:\_git\Alethite
   cd C:\_git\Alethite
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
   & $CC plugin marketplace add C:\_git\Alethite
   & $CC plugin install alethite@alethite-local
   ```
   Then restart the desktop app.
4. **Build the index** of this computer's conversations. The first time takes a few minutes:
   ```powershell
   python C:\_git\Alethite\plugin\scripts\cg.py index
   ```
   - `--lang ru` or `--lang uk` before `index` sets the language of the scripts' messages and errors in the terminal; without it they are in English.
   - If sync is already set up on this computer (see below) and the index is new, run `cg.py sync` there first: this computer's records and vectors come back from its folder and need not be computed again.
5. **Compute the vectors.** This takes hours: on the i3 the whole history took about 6 hours, so it is best run overnight. Once stopped, it goes on from where it was:
   ```powershell
   python C:\_git\Alethite\plugin\scripts\cg.py embed --max-seconds 100000
   ```
   After that, new records are embedded by themselves at the start of a session, up to 2 minutes at a time.
   - **Laptop:** keep it awake during the run. On the charger it falls asleep without input and stops the run. After waking up, simply run the command again.
   - **Built-in Intel graphics (Iris Xe):** the one-off run is about twice as fast on it, and the vectors are the same (measured 2026-10-19 on MTLAPTOP: 2.44 long pieces per second against 1.15 on the processor). Ollama sees such a card only when started with `OLLAMA_IGPU_ENABLE=1`. Stop the usual Ollama in the tray, then in a PowerShell window:
     ```powershell
     $env:OLLAMA_IGPU_ENABLE = '1'; ollama serve
     ```
     Run the computation in another window. Then close this one and start Ollama again from the Start menu. Do not keep it on: text models are slower on the built-in card.
6. **Language.** By default the mod speaks English: the interface, the memory rule, the descriptions of `recall`, and the handoff. For Russian, open the settings file `%USERPROFILE%\.claude\alethite\settings.json` (the mod created it at the first start), set `"language": "ru"` in it and type `/reload-plugins`. For Ukrainian, set `"uk"`.
7. **Check:** in a new chat type `/al-health`. It should show the version, the index, the auto-memory folder, the project, the vectors "N of M" and a sync line.

## Moving from CacheGuard

The mod used to be called Claude-CacheGuard, and so was its repository. A computer that has the old one moves with the steps below. Keep the order.

0. **Send the waiting measurements.** In a chat, type `/cg-stats` once. Measurement notes that Obsidian has not taken yet wait in the old plugin's store, and they are lost with it.
1. **Remove the old mod.** In a new PowerShell window `$CC` is empty. Set it as in "Installing", step 2 (if no path is found, the wider search is there too), and remove the old mod:
   ```powershell
   $CC = (Get-ChildItem "$env:APPDATA\Claude\claude-code\*\*\claude.exe", "$env:LOCALAPPDATA\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code\*\*\claude.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1).FullName; $CC
   & $CC plugin uninstall cacheguard@cacheguard-local
   & $CC plugin marketplace remove cacheguard-local
   ```
2. **Install the new one.** If there is no clone in `C:\_git\Alethite` yet, clone it first ("Installing", step 1). Then:
   ```powershell
   & $CC plugin marketplace add C:\_git\Alethite
   & $CC plugin install alethite@alethite-local
   ```
3. **Reload.** Type `/reload-plugins` in every open chat. A chat that is not reloaded goes on with the old mod and can make the old folder again.
4. **Check.** Type `/al-health`. The first call moves the mod folder `%USERPROFILE%\.claude\cacheguard` to `%USERPROFILE%\.claude\alethite`, and the index, the key, the settings and the handoffs go with it. If the background job was on, the old Task Scheduler task "CacheGuard" is removed and the task "Alethite" is made with the same interval. Look in the answer at the index, the key, the sync and the job.
   - If the folder could not be moved, the answer says why. The mod works in the old folder for now, and the next run tries again.
   - If the old folder is still there beside the new one, a chat with the old mod made it again. Nothing is moved. Check the old folder and delete it.
   - An index that you moved with `/al-index-dir` stays where it is. Only the mod folder moves.
   - The sync folder keeps working under its old name. You set the path in `syncDir` yourself, the rename does not touch it, and the folder on the disk need not be renamed.
5. **Keep the memory of the old project folder.** The clone now lies in another folder, and a search from it would not find the old conversations of the project. Add this line to `%USERPROFILE%\.claude\alethite\settings.json`:
   ```json
   "projectAliases": { "C:\\_git\\Alethite": ["C:\\_git\\Claude-CacheGuard"] }
   ```
   Put it inside the outer braces of the file, and add a comma after the line before it. Do not replace the whole file: it holds your other settings and, if encryption is on, the encryption key. The mod reads the file at every call. Then check that `recall` in a chat opened in `C:\_git\Alethite` finds the old conversations of the project.

Values that you set through `/config` under the old plugin are not carried over. Check them in the new plugin and set again what you still need. What lies in `settings.json` moves with the folder.

**The second computer and the laptop.** The history of the old repository was rewritten, so an old clone cannot pull any more. But it is still needed for now: the old mod points at it, and the old Task Scheduler task may run `cg.py` from there until step 4. The order is:
- On the laptop only: first accept the invitation to the private repository `GladLuter/Alethite`.
- Clone `GladLuter/Alethite` into `C:\_git\Alethite`, next to the old clone ("Installing", step 1).
- Do steps 0 to 5 above. In step 5 the old path is the folder of the old clone on this computer.
- Delete the old clone after `/al-health` checks out, and only if nothing in it is unpushed.
- Measurement notes. `/al-stats` now writes into the Obsidian folder `Alethite/Замеры`. The old mod (step 0, and any measurement run before a computer moves) still writes into `Claude-CacheGuard/Замеры`, and Obsidian makes that folder again if you renamed it. After the last computer has moved, move those notes into `Alethite/Замеры` and delete what is left of `Claude-CacheGuard/Замеры`, `Сводка.md` included. The next `/al-stats` builds the summary again from the notes of the new folder.
- Encryption on the second computer is set up afterwards, as a separate step: "Encryption (stage 5)", step 4 (`/al-encrypt join`).

## Shared memory of two computers (stage 2)

Each computer puts its new conversations, vectors and auto-memory facts into a shared folder and takes the other's from there.
- It writes only into its own subfolder (`<folder>\<computer name>\`), so the index is not damaged even when both computers work at the same time.
- Without the network folder everything works as before; the sync catches up when the folder is reachable again.

1. **Create the folder** on the network drive, once, for example `\\NAS\share\alethite`. The mod does not create it itself: if the drive is not connected, it shows an error instead of writing to the local disk.
2. **On each computer**, set it in the settings file `%USERPROFILE%\.claude\alethite\settings.json`: `"syncDir": "\\\\NAS\\share\\alethite"`, then `/reload-plugins`. Backslashes in JSON are doubled.
3. **The first exchange.** The whole history is tens of megabytes. The mod syncs for at most 60 seconds at a time and goes on at the next run, but the first time is easier from a terminal — first on the i3, then on the laptop:
   ```powershell
   python C:\_git\Alethite\plugin\scripts\cg.py sync --sync-dir \\NAS\share\alethite --max-seconds 3600
   ```
   Then once more on the i3, to take in the laptop's history. The vectors travel with the records: the other computer's need not be computed again if both use the same embedding model.
4. **From then on by itself:** at the start of a session, after the index and the vectors. By hand: `/al-sync`; the answer says how much was pushed and taken in, and any errors.
5. **Check:** `recall` on the laptop finds the morning's conversation from the i3, and `/al-health` shows both computers and the numbers of their last batches.

What else to know:
- **Short memory** is synced for the project of the current chat.
  - Facts created on this computer go to the shared folder.
  - The other computer's facts are copied into the auto-memory folder when they are not there, and a pointer line is added to `MEMORY.md`.
  - A file of your own with the same name is never overwritten: the other version is put beside it as `name--COMPUTER.md`. An imported fact you deleted does not come back.
- **A project** is recognised by its path without the home folder: `C:\Users\AIJ\work\x` and `C:\Users\gladl\work\x` are one project. Paths outside the profile (`C:\_git\…`) match anyway.
- **A broken batch** (a dropped network, a disk failure) stops taking in only from its computer; the error is shown in `/al-health` at every sync. If the batch cannot be restored: `/al-sync skip <computer> <number>`. The skip is shown in `/al-health`.
- **Deleting a record** (say, a secret the filter missed) is only possible explicitly, and the deletion reaches the other computer:
  ```powershell
  python C:\_git\Alethite\plugin\scripts\cg.py forget --conv <conversation id> --pos <pos>
  ```
  `conv` and `pos` come from the line of a `recall` hit. Nothing else ever leaves the index: neither on a rebuild nor when Claude Code rewrites or deletes transcripts.

## Memory compression (stage 3)

The mod compresses what it puts into the model's context itself: the results of `recall` and `recall_more` and the quotes of the conversation in a handoff. The compression is mechanical, with no model calls. Code, paths, links, numbers, names and negations are never touched.

- **Level:** the `"compression"` key in the settings file `%USERPROFILE%\.claude\alethite\settings.json`, then `/reload-plugins`.
  - `off` — no compression;
  - `lite` — spaces and markup;
  - `full` — plus function words;
  - `ultra` — plus punctuation and capitals (the default).
- **To see what a level does** on any text:
  ```powershell
  python C:\_git\Alethite\plugin\scripts\cg.py compress --level ultra --file note.txt
  ```
- **How much memory is in the context:** `/al-stats` writes a "Memory in context" section into its note: the memory rule, `MEMORY.md`, the results of `recall` and their cost, the handoffs. `/al-health` shows the compression level and the size of `MEMORY.md`.
- If the worker understands the compressed excerpts worse, set `full` or `lite`.

## Lessons, the background job, searching for errors (stage 4)

After `git pull` and `/reload-plugins`:

1. **Rebuild the index once**, so the tool errors of old conversations get into it:
   ```powershell
   python C:\_git\Alethite\plugin\scripts\cg.py index --rebuild
   ```
   A rebuild only adds what is missing. The vectors of the new records are computed by the background job or the next chat start.
2. **Turn the background job on** on each computer: in a chat, `/al-job on` (or `/al-job on 15` — every 15 minutes).
   - The task "Alethite" appears in the Task Scheduler: no window, only while you are logged on, on a battery the vectors wait for the mains.
   - It takes its settings from the same `settings.json` file as the mod.
   - It runs the `cg.py` of the installed mod. After the mod is updated, the task is pointed at the new copy by itself at the start of a session.
   - `/al-job` shows the state and the last run, `/al-job off` removes the task.
   - If the task does not run, check that Python is installed from python.org (`pythonw.exe` must lie beside `python.exe`).
3. **Lessons** work by themselves: the worker saves an understood error with `lesson_save`. `/al-lessons` lists the project's lessons, `/al-lessons forget <pos>` deletes one.
   - A lesson comes beside a message close in meaning and stops once a command that holds its trigger.
   - If lessons come where they do not belong, raise `lessonsMinScore` in `settings.json` to `0.55`; if they never come, lower it to `0.4`. To turn them off: `"lessons": false`.

## Encryption (stage 5)

Optional, and Windows only. The index (with its backup `index.before-stage2.db`) and everything a computer puts in the sync folder are encrypted. In the folder anyone can read the computer's name, the batch numbers, their count and sizes, and the label of a generation (`gen-0001\generation.json`) also names the cipher, the key's fingerprint and the date. The fingerprint does not give the key away.
- The key sits in `settings.json`, protected by Windows (DPAPI). On another computer or under another Windows account the file gives no key.
- Until you run `/al-encrypt on`, nothing changes for you.

**The ciphers.** The index and the sync folder are encrypted each in its own way, and each computer chooses them itself:
- The index, key `indexCipher`:
  - `sqlcipher` (the default): SQLCipher, needs the `sqlcipher3` package;
  - `efs`: the file encryption of Windows itself. It needs Windows Pro (or Enterprise, Education) and an NTFS drive. There is no package to install. The whole mod folder `%USERPROFILE%\.claude\alethite` goes under EFS, and the index folder too if it is another one: the index, its journal, the backups, the handoffs, `settings.json`, `forgotten.json`.
- The sync folder, key `shareCipher`:
  - `sqlcipher` (the default): SQLCipher, needs the `sqlcipher3` package;
  - `cryptography`: AES-GCM, built into Windows (`bcrypt.dll`). The `cryptography` package is no longer needed.
- **Smart App Control** may refuse to load `sqlcipher3`, because its module is not signed. Under it only `efs` and `cryptography` work, and a computer with that pair does not need `sqlcipher3`. The exceptions are converting an existing SQLCipher index and reading the folder of a computer that writes with SQLCipher.
- Each computer writes its own subfolder in the sync folder in its own cipher and reads the others' by the label of the generation. If another computer's subfolder is written with SQLCipher and `sqlcipher3` does not load here, `/al-health` names that computer and advises moving it to AES-GCM.

1. **Choose the ciphers** on each computer in `%USERPROFILE%\.claude\alethite\settings.json`. For example, EFS and AES-GCM:
   ```json
   {
     "indexCipher": "efs",
     "shareCipher": "cryptography"
   }
   ```
   If you choose nothing, both stay `sqlcipher`. `/al-encrypt on` and `join` take the ciphers from this file and write them back beside the key. The mod reads the file when it loads, so make this edit before the `/reload-plugins` below.
2. **Install the `sqlcipher3` package**, only if you need it: for the `sqlcipher` cipher (of the index or of the folder), and once, to move an index that is already encrypted with SQLCipher over to EFS. Install it on each computer from an ordinary PowerShell window, not from inside Claude: the `pip` of Claude puts packages into its own sandbox, and the background job will not see them.
   ```powershell
   python -m pip install sqlcipher3
   ```
3. **The first computer:** `/reload-plugins`, then `/al-encrypt on`. The "Alethite" windows may appear behind other windows.
   - With `efs` the command first checks that the drive can do EFS. If it cannot (Windows Home, or a drive that is not NTFS), it refuses before any key is made and changes nothing.
   - With `efs` the index also needs a folder of its own. EFS would encrypt everything in the folder, so when the index folder is not the mod's own folder and holds files that are not the mod's, the command refuses before any key is made. Move the index into a folder of its own with `/al-index-dir`, then repeat.
   - The first window shows the **recovery code**, once. Ctrl+C copies the text of the window: paste the code into a password manager. Without it the encrypted history cannot be read if the computers or their Windows accounts are lost.
   - The question "Is the recovery code saved?" defaults to No. Encryption starts only after Yes.
   - The index and its backup are encrypted. With `sqlcipher` the unencrypted copies (`index.db.plain`) are deleted only after Yes. After No they stay, and `/al-encrypt on` asks again.
   - With `efs` the mod folder and the index folder go under the encryption of Windows, and a SQLCipher index becomes an ordinary database under EFS with 16 KB pages. There is never an unencrypted copy outside EFS, so there is no question about `index.db.plain`.
   - The sync folder: the computer puts its own subfolder out again, in the cipher you chose. The older data there (unencrypted or in another cipher) is deleted only after Yes, and only once the new data is fully out.
4. **The second computer:** `git pull`, its own ciphers in `settings.json` (step 1), `/reload-plugins`, `/al-encrypt join`. The recovery code is entered in a window with hidden input. One key serves all computers, so `on` on the second one refuses and points to `join`.
   - If you change the cipher of the folder on the first computer (for example, SQLCipher to AES-GCM), join the second one only after that. Otherwise it will see a SQLCipher generation, which it cannot open under Smart App Control.
5. **Check:** `/al-health` on both computers says, for example, "Encryption: on (index: EFS, sync folder: AES-GCM)". If something is unfinished, the lines under it say what to repeat. `/al-encrypt status` shows the state alone.

What else to know:
- **Commands:** `/al-encrypt on | join | off | status`. With no argument it runs `status`.
  - `off` decrypts the index (with EFS it takes the Windows encryption off both folders), puts this computer's subfolder out unencrypted again, and removes the key from `settings.json`. Turn it off on each computer. A new `on` makes a new key, and the other computers join again.
  - If the key is lost (another Windows account, a reinstall): `/al-encrypt join` with the recovery code.
  - If both the key and the code are lost, this computer can no longer read what it encrypted. Turn encryption off (`off`) on the other computers and delete this computer's folder in the sync folder by hand, and its encrypted `index.db` too if one is left (the mod builds a new one from the conversations on disk). Then turn encryption on again: `on` on one computer, `join` with the new code on the others.
- **Until the second computer has joined**, the first takes nothing from it, and `/al-health` names it: "run `join` on it". Its history is not lost: after `join` it puts it out encrypted.
- **The time limit.** One run is limited to about 10 minutes (the limit of the desktop app). A big first push may not fit: the reply says it is not finished. Repeat `/al-encrypt on` (or `join`): it goes on from where it stopped, and every step is safe to repeat. The background job also pushes the rest, but the older unencrypted data in the folder is deleted only by repeating the command, after Yes.
- **The ciphers** are the keys `indexCipher` (`sqlcipher`, the default, or `efs`) and `shareCipher` (`sqlcipher`, the default, or `cryptography`, which is AES-GCM) in `settings.json`. `/al-encrypt on` and `join` write them into the file themselves, and the file wins over `/config`, so change them later in the file, then `/reload-plugins` and `/al-encrypt on`. A computer reads the others' data in any cipher it can open itself: SQLCipher only when `sqlcipher3` is installed.
- **Speed.** On the encrypted index `recall` is about 0.08 s slower (measured 2026-10-27 on 24.7 thousand vectors). The gap grows with the number of vectors. A measurement of 2026-11-02 on a copy of the real index (25.3 thousand vectors, median of 20 runs): one search takes 290 ms with SQLCipher and 170 ms with EFS, 16 KB pages in both.
- **What you accept with EFS:**
  - A copy of the index on a FAT or exFAT stick or over the network comes out unencrypted, and so does one made by a program that reads the file and writes it again into a folder outside EFS. The mod itself never copies the index out.
  - The files open only under the same Windows account. If an index under EFS does not open (another user, a lost certificate), the mod says so and does not make a new empty index over it.
  - If you reinstall Windows without a backup of the certificate, the old index cannot be opened. Delete it, and the mod builds a new one from the conversations on disk. A copy of the certificate is optional, and the mod does not make one. To make it, run `cipher /x <file>`: Windows asks for a password and saves the EFS certificate and key to a `.pfx` file protected by it.
- **What stays plain:**
  - Claude Code's conversations, `%USERPROFILE%\.claude\projects\*.jsonl`;
  - the auto-memory files of each project on the local disk (`.md`): Claude Code reads them;
  - the handoffs, the `handoffs` folder beside the index: the same text goes into the chat unencrypted. With EFS the files themselves are encrypted on the disk.
  - `forgotten.json` in `%USERPROFILE%\.claude\alethite`, made only when the index lives in another folder: the keys of the records deleted with `forget`. They are hashes, with no text.

## The index in another folder

For a computer that is short of space on C:. The index grows with every chat, and it can move to another local disk. Windows only. Without this command nothing changes, and the index stays in `%USERPROFILE%\.claude\alethite\`.

1. **Move it:** `/al-index-dir D:\Alethite`. Without a path a window opens to choose the folder.
   - The command copies the index, checks the copy, and only then switches the mod to the new folder. If a chat or the background job is using the index, it asks you to repeat in a minute.
   - Then a window asks whether to delete the old copy. The default is No. The old files are deleted only after Yes. After No they stay, and `/al-health` reminds you where they are. To be asked again, repeat `/al-index-dir` with the same folder.
2. **Check:** `/al-health`. The "Index" line now shows the new path, for example `D:\Alethite\index.db`.

The background job and the `recall` and `lesson_save` tools use the new folder on their own. Nothing needs reinstalling.

What else to know:
- **What moves:** `index.db`, its backup `index.before-stage2.db` (if there is one), the `handoffs` folder and the background job's `job.xml`. An encrypted index moves encrypted. An index under EFS stays under EFS: the target folder is encrypted before the copy.
- **What stays on C:** the `settings.json` file (it holds the encryption key and the new `indexDir` line) and Claude Code's conversations, `%USERPROFILE%\.claude\projects`.
- **A refusal.** The command changes nothing and names the reason if:
  - the folder is not on a local fixed disk (network paths, network disks and removable disks do not fit) or lies inside OneDrive
  - the folder already holds another `index.db`, and the command does not write over a foreign index
  - the target disk lacks space (it needs the size of the index files plus 200 MB)
  - an `index.db.plain` lies beside the index, so finish `/al-encrypt on` first
  - the index is under EFS and the target drive cannot encrypt files (it needs NTFS): choose a folder on an NTFS drive, or turn the encryption off first (`/al-encrypt off`)
  - what was saved on C: while the folder was away is not merged yet (see below).
- **If the folder or the `index.db` in it is gone** (the disk was unplugged, the folder deleted), the mod says so in a toast, once per session, and in a `/al-health` line: "the index in D:\Alethite is not available, working with …". It works with the index on C: and leaves `indexDir` alone.
  - If the standard folder already holds an index (the old copy after No), the work goes on with it. If not, a new one is made only when C: has at least 2 GB free. Otherwise the commands that read the index answer with an error that names the paths and the numbers, and nothing is created. The ones that need no index still work: the handoff of New worker, the token count and the compression preview. While this lasts, the status line says "Index unavailable, details in /al-health", and every message you send gets a toast with the reason.
  - Sync goes on as usual.
  - What you deleted with `forget` stays deleted on C: too. Its keys are kept in `forgotten.json` beside `settings.json`, and the index on C: applies them before it reads any conversation.
- **When the folder is back**, the first run of `cg.py` (the background job or a command in a chat) merges everything gathered on C: into it and deletes the temporary index. It does not ask, because space on C: is worth more. An old copy that you kept with No and that stood in during the outage is merged and deleted in the same way. The message gives the number of records merged, and `/al-health` keeps it. What was deleted with `forget` does not come back. If the merge does not match, both indexes stay in place, the work goes on with the index folder, `/al-health` shows the reason, and the next run tries again.
- **The folder is not coming back.** While the mod works with C:, the command with the full path of the standard folder, `/al-index-dir C:\Users\<name>\.claude\alethite`, removes `indexDir` and copies nothing. C: becomes the permanent place of the index. If the folder is back but has no index, the command with that folder's path moves the index from C: into it.
- **Moving the index back to C:** from the working folder is done by the same command, `/al-index-dir C:\Users\<name>\.claude\alethite`. If C: holds an old copy that is not byte for byte the same as the index, the command refuses. Then first repeat `/al-index-dir` with the working folder and answer Yes to the question about deleting. An old copy that is byte for byte the same does not stop the move.
- **The merge keeps failing.** `/al-health` shows the reason in its line, and `/al-index-dir` refuses while the merge is not done. If the same reason comes back every time, delete `index.db` in `C:\Users\<name>\.claude\alethite` by hand, but only once the records saved there are not needed. The index in the working folder stays as it is.

## Good to know

- **The mod is read straight from the repository folder.**
  - Keep the folder on the `master` branch. If it used to be on `stage1`, run `git checkout master; git pull` once.
  - After a `git pull`, type `/reload-plugins` in Claude Code: no need to reinstall.
- **The index and the vectors** live in `%USERPROFILE%\.claude\alethite\index.db` and never get into the repository. Each computer keeps its own index; the shared sync folder only carries batches between them.
- **If the index has moved** with `/al-index-dir`, it is in the folder you chose (see "The index in another folder").
- **`/al-index rebuild`** re-reads every transcript and adds only what is missing: the vectors stay and nothing is deleted.
- **Settings:** a file of its own, `%USERPROFILE%\.claude\alethite\settings.json`, one per computer. It stays in this folder even when the index has moved. The keys: `language`, `compression`, `syncDir`, `bandMinTokens` and `bandIdleMinutes` (the band thresholds), `showLimits` (the limits line), `python` (the Python command, if it is `py`, not `python`), `embedModel`, `lessons` and `lessonsMinScore` (the lessons), `indexCipher` (the index encryption), `shareCipher` (the sync folder encryption), `indexDir` (the index folder), `projectAliases` (old folders whose memory a project folder also sees). The table with the defaults is in [README.md](README.md).
  - The mod creates the file at the first start with all the current values, so you only open it and edit. A file may hold just the keys you change.
  - The mod reads it at every load: the start of a chat and `/reload-plugins`. After an edit, type `/reload-plugins`.
  - Its values win over `/config`. The desktop app has no settings window for mods, so the settings live in a file. `/config` in a terminal `claude` still works, but where they differ, the file wins.
  - Backslashes in JSON are doubled: `"Z:\\alethite"`.
  - `/al-health` shows a line with the file's path.
  - The `encryptionKey` line is written by the mod itself: it is the encryption key, protected by Windows. Do not edit it.
  - The `indexDir` line is written by the `/al-index-dir` command. Do not edit it by hand: the index files will not move on their own.
  - The `projectAliases` line is yours to write when a project folder is renamed or moved: `"projectAliases": {"C:\\work\\new": ["C:\\work\\old"]}`. The new folder then finds the conversations and lessons of the old one when it searches. New conversations and lessons are saved under the new folder. Nothing in the index is rewritten and nothing goes to the sync folder, so every computer has its own aliases. The mod reads the file at every call. No `/reload-plugins` is needed. `/config` does not show this key.
  - A broken file (invalid JSON) gives a toast naming the file, and the mod keeps the `/config` values. An unknown key or a value of the wrong type gives the same toast, and that key is skipped.
- **The band above the prompt** is one area shared by all mods. The Alethite band is drawn in a frame of its own, with its title, and the bands of other mods stay under it.
- **`/al-stats`** writes measurements to Obsidian through the `obsidian` MCP server, which must be connected on this computer. If Obsidian is unavailable, the measurement waits in a queue and goes out at the next run.
