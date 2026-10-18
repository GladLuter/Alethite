# Установка CacheGuard на компьютер с Windows

Все команды — в PowerShell.

## Что нужно заранее

- **Десктопное приложение Claude Code**, движок не ниже 2.1.295. Проверка версии:
  ```powershell
  Get-ChildItem "$env:USERPROFILE\.claude\projects" -Recurse -Filter *.jsonl | Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content -Tail 50 | Select-String '"version":"([^"]+)"' | Select-Object -Last 1 | ForEach-Object { $_.Matches[0].Groups[1].Value }
  ```
- **Python 3.8 или новее и numpy.** Проверка: `python -c "import numpy; print(numpy.__version__)"`. Если numpy нет: `python -m pip install numpy`.
- **Ollama с моделью эмбеддингов**, нужна для поиска по смыслу. Если Ollama ещё нет, поставить её с ollama.com. Затем:
  ```powershell
  ollama pull embeddinggemma
  ```
  Это около 622 МБ. Без модели мод работает, только поиск идёт по одним словам.
- **git.**

## Установка

1. **Скачать код**, ветка `stage1`:
   ```powershell
   git clone https://github.com/GladLuter/Claude-CacheGuard.git C:\_git\Claude-CacheGuard
   cd C:\_git\Claude-CacheGuard
   git checkout stage1
   ```
2. **Найти движок десктопа.** `claude` из PATH — отдельный CLI, часто старее, поэтому нужен именно `claude.exe` десктопа:
   ```powershell
   $CC = (Get-ChildItem "$env:APPDATA\Claude\claude-code\*\*\claude.exe", "$env:LOCALAPPDATA\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code\*\*\claude.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1).FullName; $CC
   ```
   Если ничего не нашлось, искать шире (это медленно):
   ```powershell
   $CC = (Get-ChildItem $env:LOCALAPPDATA, $env:APPDATA -Recurse -Filter claude.exe -ErrorAction SilentlyContinue | Where-Object FullName -like '*claude-code*' | Sort-Object LastWriteTime | Select-Object -Last 1).FullName; $CC
   ```
3. **Поставить мод:**
   ```powershell
   & $CC plugin marketplace add C:\_git\Claude-CacheGuard
   & $CC plugin install cacheguard@cacheguard-local
   ```
   Затем перезапустить десктопное приложение.
4. **Собрать индекс** по разговорам этого компьютера. Первый раз занимает несколько минут:
   ```powershell
   python C:\_git\Claude-CacheGuard\plugin\scripts\cg.py index
   ```
5. **Посчитать векторы.** Это часы: на i3 вся история заняла около 6 часов, лучше запускать на ночь. После остановки расчёт продолжается с места:
   ```powershell
   python C:\_git\Claude-CacheGuard\plugin\scripts\cg.py embed --max-seconds 100000
   ```
   Дальше новые записи досчитываются сами при старте сессии, до 2 минут за раз.
6. **Проверить:** в новом чате набрать `/cg-health`. Там должны быть версия, индекс, папка автопамяти, проект и векторы «N из M».

## Полезно знать

- **Мод читается прямо из папки репозитория.** Держите её на ветке `stage1`. После `git pull` наберите в Claude Code `/reload-plugins`, переустанавливать не нужно.
- **Индекс и векторы** лежат в `%USERPROFILE%\.claude\cacheguard\index.db`, в репозиторий не попадают. Каждый компьютер строит свои; общее хранилище — этап 2.
- **Настройки:** `/config`, строки `cacheguard`. Там пороги полосы, строка лимитов, команда Python (если это `py`, а не `python`) и модель эмбеддингов.
- **`/cg-stats`** пишет замеры в Obsidian через MCP-сервер `obsidian`. Он должен быть подключён на этом компьютере. Если Obsidian недоступен, замер ждёт в очереди и уходит при следующем запуске.
