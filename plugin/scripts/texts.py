"""Every text of the Python side that the owner or the model reads, one table per language.
A new language is one more table in TABLES; a key it lacks is taken from EN."""

EN = {
    # vectors.py: why the vector search or the embedding run did not work
    'numpy_missing': 'numpy is not installed: pip install numpy',
    'ollama_down': 'Ollama is not answering: {e}',
    'model_missing': 'model {model} not found: run ollama pull {model}',
    'too_few_vectors': 'returned {got} vectors instead of {want}',
    'short_vector': 'a vector of {n} numbers, at least {dims} needed',
    'wrong_count': 'Ollama returned {got} vectors instead of {want}',
    'no_model_vectors': 'no vectors of model {model} (in the index: {have})',
    'nothing': 'none',

    # cg.py embed: the progress line in a terminal
    'embed_progress': 'pieces embedded: {n}, {s} s passed',

    # handoff.py: the handoff to a new worker chat
    'handoff_head': 'Handoff from the previous chat (session {id}, {today}). This is a new chat of the same worker.',
    'handoff_memory': 'Short memory: {dir} ({n} facts)',
    'handoff_no_memory': 'Short memory is EMPTY: {dir}',
    'handoff_recall': 'Long memory: the recall tool; the old conversation is id {id}.',
    'handoff_resume': 'To open the old conversation (it is not in the sidebar): in a terminal `cd "{cwd}"`, then `claude --resume {id}`.',
    'handoff_stopped': '## Where we stopped',
    'handoff_owner': '**You:** ',
    'handoff_claude': '**Claude:** ',
    'handoff_state': '## State of the work',
    'handoff_commits': 'Latest commits:',
    'handoff_files': '### Files changed (chat and subagents)',
    'bad_session': 'not a session id: {id}',

    # stats.py: the measurement note written into the vault
    'stats_weekly_base': '70% in 4.5 days (2026-10-13)',
    'stats_usd': '$ per owner message',
    'stats_rewrite': 'share of cache rewrites in all costs',
    'stats_idle': 'of them after a pause of 60 min or more',
    'stats_context': 'median context of a main session',
    'stats_weekly': 'weekly limit (snapshot)',
    'stats_title': '# Measurement {today}, {host}',
    'stats_period': 'Period: {since} to {until}. Owner messages: {owner}, calls in main sessions: {calls}, cost of main sessions: {cost} (API prices).',
    'stats_columns': '| metric | baseline (before 2026-10-13) | now |',
    'stats_limits': 'Limits at the time of measuring: ',
    'stats_rule': 'The memory rule in every request: {rule} tokens × {calls} calls = {total} tokens for the period.',

    # sync.py: what went wrong with the shared folder
    'sync_dir_missing': 'the sync folder is unavailable: {dir} ({e})',
    'sync_bad_machine': 'the computer name cannot name a folder: {name!r}',
    'batch_missing': 'batch {n} is missing while later ones exist; to go on without it: /cg-sync skip {machine} {n}',
    'batch_broken': 'batch {n} is broken ({e}); its records wait. To go on without it: /cg-sync skip {machine} {n}',
    'batch_header': 'the first line is not the header of {machine} batch {n}',
    'bad_line': 'line {line}: {e}',
    'bad_hash': 'the text does not match its hash',
    'bad_vector': 'a vector of {n} bytes, {want} expected',
    'digest_differs': '{machine}: model {model} is another build there (digest {theirs} here {ours}); its vectors are taken all the same',
    'skip_batch_bad': 'skip-batch: the batch number is a whole number above zero',
    'memory_dir_missing': 'the auto-memory folder is not there: {dir}',
}

RU = {
    'numpy_missing': 'numpy не установлен: pip install numpy',
    'ollama_down': 'Ollama не отвечает: {e}',
    'model_missing': 'модель {model} не найдена: выполните ollama pull {model}',
    'too_few_vectors': 'вернул {got} векторов вместо {want}',
    'short_vector': 'вектор из {n} чисел, нужно не меньше {dims}',
    'wrong_count': 'Ollama вернул {got} векторов вместо {want}',
    'no_model_vectors': 'векторов модели {model} нет (в базе: {have})',
    'nothing': 'ничего',

    'embed_progress': 'посчитано кусков: {n}, прошло {s} с',

    'handoff_head': 'Передача дел от предыдущего чата (сессия {id}, {today}). Это новый чат того же работника.',
    'handoff_memory': 'Короткая память: {dir} (фактов: {n})',
    'handoff_no_memory': 'Короткая память ПУСТА: {dir}',
    'handoff_recall': 'Долгая память: инструмент recall; старый разговор — id {id}.',
    'handoff_resume': 'Открыть старый разговор (в боковой панели его нет): в терминале `cd "{cwd}"`, затем `claude --resume {id}`.',
    'handoff_stopped': '## Где остановились',
    'handoff_owner': '**Ты:** ',
    'handoff_claude': '**Claude:** ',
    'handoff_state': '## Состояние работы',
    'handoff_commits': 'Последние коммиты:',
    'handoff_files': '### Файлы, которые меняли (чат и субагенты)',
    'bad_session': 'это не id сессии: {id}',

    'stats_weekly_base': '70% за 4,5 дня (2026-10-13)',
    'stats_usd': '$ на сообщение владельца',
    'stats_rewrite': 'доля перезаписей кэша во всех расходах',
    'stats_idle': 'из них после паузы ≥ 60 мин',
    'stats_context': 'медианный контекст основной сессии',
    'stats_weekly': 'недельный лимит (снимок)',
    'stats_title': '# Замер {today}, {host}',
    'stats_period': 'Период: {since} — {until}. Сообщений владельца: {owner}, вызовов в основных сессиях: {calls}, расходы основных сессий: {cost} (цены API).',
    'stats_columns': '| метрика | база (до 2026-10-13) | сейчас |',
    'stats_limits': 'Лимиты на момент замера: ',
    'stats_rule': 'Правило памяти в каждом запросе: {rule} токенов × {calls} вызовов = {total} токенов за период.',

    'sync_dir_missing': 'папка синхронизации недоступна: {dir} ({e})',
    'sync_bad_machine': 'имя компьютера не годится для папки: {name!r}',
    'batch_missing': 'пачки {n} нет, а следующие есть; чтобы идти дальше без неё: /cg-sync skip {machine} {n}',
    'batch_broken': 'пачка {n} испорчена ({e}); её записи ждут. Чтобы идти дальше без неё: /cg-sync skip {machine} {n}',
    'batch_header': 'первая строка — не шапка пачки {n} компьютера {machine}',
    'bad_line': 'строка {line}: {e}',
    'bad_hash': 'текст не совпадает со своим хэшем',
    'bad_vector': 'вектор из {n} байт, ожидалось {want}',
    'digest_differs': '{machine}: модель {model} там другой сборки (digest {theirs}, здесь {ours}); векторы всё равно влиты',
    'skip_batch_bad': 'skip-batch: номер пачки — целое больше нуля',
    'memory_dir_missing': 'папки автопамяти нет: {dir}',
}

TABLES = {'en': EN, 'ru': RU}
lang = 'en'  # cg.py sets it from --lang before any work


def t(key, **kw):
    """The text of `key` in the current language, filled in; a key the language lacks comes from EN."""
    return TABLES.get(lang, EN).get(key, EN[key]).format(**kw)
