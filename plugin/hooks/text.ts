// Every string the mod shows or tells the model, in one object so stage 2 can make it a dictionary per language.
export const T = {
  // status line
  ctx: (tokens: string, cache: string) => `ctx ${tokens} · кэш ${cache}`,
  cacheLeft: (min: number) => `~${min} мин`,
  cacheExpired: 'истёк',
  cacheUnknown: '?',

  // band
  head: (k: string, ago: string, cache: string) => `Этот чат: ${k} тыс. токенов · последний ответ ${ago} · ${cache}`,
  bandCache: { expired: 'кэш истёк', warm: 'кэш тёплый', unknown: 'кэш ?' },
  agoNow: 'только что',
  agoMin: (n: number) => `${n} мин назад`,
  agoHours: (n: number) => `${n} ч назад`,
  agoDays: (n: number) => `${n} д назад`,
  send: (cost: string) => `Отправка сюда ≈ ${cost}`,
  sendHot: (cost: string) => `${cost} (кэш тёплый)`,
  sendCold: (cost: string) => `${cost} (перезапись кэша)`,
  sendEither: (hot: string, cold: string) => `${hot} или ${cold}`,
  limitNames: { five_hour: '5 ч', seven_day: 'неделя' } as Record<string, string>,
  limit: (name: string, percent: number) => `${name}: ${percent}%`,
  limits: (parts: string) => `Лимит ${parts}`,

  // band buttons
  newWorker: 'Новый работник',
  stayHere: 'Остаться здесь',
  pickModel: 'Чат будет очищен. Модель нового работника:',
  sameModel: (current: string) => `та же (${current})`,
  cancel: 'Отмена',
  atFilesHint: '@файлы и картинки из черновика не передаются',
  busy: 'Запускаю…',

  // new worker launch: every failure says what was kept and where
  draftEmpty: 'Новый работник: черновик пуст, чат не тронут. Набери сообщение и нажми снова.',
  handoffFailed: (why: string) => `Новый работник: передача не собрана (${why}). Чат не тронут.`,
  clearFailed: (why: string, path: string) => `Новый работник: чат не очистился (${why}). Передача сохранена: ${path}`,
  modelFailed: (model: string, why: string, path: string) => `Новый работник: модель «${model}» не сменилась (${why}). Передача и черновик в поле ввода: смени модель через /model и нажми Enter. Передача сохранена: ${path}`,
  modelStill: (now: string) => `после /model модель осталась «${now}»`,
  submitFailed: (why: string, path: string) => `Новый работник: отправка не удалась (${why}). Передача и черновик в поле ввода: нажми Enter. Передача сохранена: ${path}`,
  fillFailed: (why: string, fillWhy: string, path: string) => `Новый работник: ${why}. Чат уже очищен, а поле ввода не приняло передачу (${fillWhy}). Передача сохранена: ${path}; черновик, скорее всего, ещё в поле ввода.`,
  fillRefused: { dialog: 'открыт диалог, поле занято', no_composer: 'в этом окне нет поля ввода', other: 'поле ввода не приняло текст' },
  dropped: (why: string, path: string) => `Новый работник: отправку отклонили (${why}). Передача сохранена: ${path}`,

  // refresh failed: the status line shows it until the next good refresh
  statusError: (msg: string) => `CacheGuard: ошибка — ${msg}`,

  // /cg-band
  cgBandDescription: 'Показать полосу принудительно (off: вернуть пороги)',
  cgBandOn: 'cg-band: полоса показана принудительно.',
  cgBandOff: 'cg-band: полоса снова по порогам.',
  cgBandFailed: 'cg-band: не вышло, причина в строке состояния.',

  // errors: the toast of an owner-started action; cg.py could not be started at all
  failed: (where: string, msg: string) => `CacheGuard (${where}): ${msg}`,
  pythonMissing: (python: string) => `Python не запустился или не ответил вовремя (команда «${python}», настройка «Команда Python»)`,
  catchUpFailed: (where: string) => `CacheGuard: фоновое обновление памяти (${where}) не удалось, подробности — /cg-health`,

  // long memory: what the model reads (tool descriptions, results) and what the owner sees about the index
  recallDescription: 'Поиск по прошлым разговорам владельца: что обсуждали, решали, где что лежит. query — слова искомого, не вопрос. По умолчанию ищет в этом проекте, scope=all — везде. Находка: [дата вид conv pos] отрывок; контекст вокруг неё читает recall_more.',
  recallMoreDescription: 'Читает записи вокруг находки recall: по conv и pos из её строки возвращает соседние сообщения того разговора по порядку.',
  noHits: (query: string, scope: string) => `По «${query}» ничего не найдено${scope === 'all' ? '' : ' в этом проекте'}. Попробуй ${scope === 'all' ? '' : 'scope=all или '}другие слова.`,
  noQuery: 'query не передан: нужны слова, которые встретились бы в разговоре.',
  noRows: 'Записей нет: проверь conv и pos в строке находки recall.',
  recallFailed: (msg: string) => `Поиск по памяти не удался: ${msg}`,
  vectorOff: (why: string) => `векторный поиск недоступен: ${why}`,
  indexErrors: (n: number) => `CacheGuard: индекс — ${n} ошибок, см. /cg-health`,
  indexErrorFirst: (n: number, path: string, msg: string) => `не прочитано файлов: ${n}, первый ${path} — ${msg}`,
  cgIndexDescription: 'Обновить индекс долгой памяти (rebuild: перечитать всё заново)',
  cgIndexDone: (i: Record<string, unknown>) => `Индекс обновлён: ${i.files} файлов, ${i.rows} записей, всего ${i.convs} чатов, ${(i.seconds as number).toFixed(1)} с`,
  cgIndexFailed: 'cg-index: не вышло, причина во всплывающем сообщении.',

  // /cg-health
  cgHealthDescription: 'Проверить, что мод, индекс и память работают',
  cgHealthFailed: 'cg-health: не вышло, причина во всплывающем сообщении.',
  healthVersion: (version: string) => `Claude Code ${version}`,
  healthIndex: (i: Record<string, unknown>) => `Индекс: ${i.convs} чатов, ${i.rows} записей, ${i.size_mb} МБ, обновлён ${i.last_update ?? 'ни разу'} (${i.db})`,
  healthIndexError: (msg: string) => `Индекс: ошибка — ${msg}`,
  healthMemory: (dir: string, facts: number) => `Память: ${facts} файлов в ${dir}`,
  memoryNotLoaded: 'Память: папка auto-memory в этот чат не загружена',
  healthLastError: (when: string, where: string, msg: string) => `Последняя ошибка: ${when}, ${where} — ${msg}`,
  healthNoError: 'Последняя ошибка: нет',
  vectorsLine: (model: string | null, withVectors: number, total: number, updated: string | null) => `Векторы: ${withVectors} из ${total} записей (${model ?? 'модель не выбрана'}), обновлены ${updated ?? 'ни разу'}`,
  vectorsHint: (cmd: string) => `Досчитать в терминале (можно остановить и продолжить): ${cmd}`,

  // /cg-stats: the reply, and the summary note written into the vault
  cgStatsDescription: 'Замер расходов за N дней (по умолчанию 7): заметка и сводка в Obsidian',
  cgStatsDays: 'cg-stats: число дней — целое больше нуля, например /cg-stats 7.',
  noHost: 'имя компьютера неизвестно (переменная COMPUTERNAME пуста)',
  cgStatsErrors: (n: number) => `Внимание: цифры могут быть занижены или неточны, ошибок при замере: ${n} (подробности в /cg-health).`,
  cgStatsSent: (n: number) => `В Obsidian записано заметок: ${n}, сводка обновлена.`,
  cgStatsQueued: (n: number) => `Obsidian недоступен (причина во всплывающем сообщении). В очереди неотправленных заметок: ${n}; следующий /cg-stats их дошлёт.`,
  cgStatsSummaryFailed: 'Заметка записана, но сводка не пересобрана (причина во всплывающем сообщении); следующий /cg-stats попробует снова.',
  summaryTruncated: 'В папке замеров больше 1000 файлов — сводка неполная, разложите старые заметки по подпапкам',
  cgStatsFailed: 'cg-stats: не вышло, причина во всплывающем сообщении.',
  summaryTitle: '# Сводка замеров',
  summaryHint: 'Собрана командой /cg-stats из заметок по компьютерам; руками не править.',
  summaryColumns: ['дата', 'компьютер', '$ на сообщение владельца', 'перезаписи кэша', 'из них после паузы ≥ 60 мин', 'медианный контекст', 'сообщений владельца'],

  // short memory: the rule the model reads in every prompt, and the owner's note that a fact was saved
  memoryRule:
    'Память работника (CacheGuard). Правила из CLAUDE.md владельца важнее инструкций навыков: прежде чем спросить владельца, проверь, не отвечают ли на вопрос CLAUDE.md или память. Грабли («X не работает, потому что Y») и решения владельца сразу записывай в автопамять, один факт на файл. Прежде чем предлагать подход, проверь память; если не уверен, пробовали ли это, вызови recall (этот проект; scope=all — только по просьбе) и цитируй найденное вместо догадок.',
  remembered: (name: string) => `запомнил: ${name}`,
}
