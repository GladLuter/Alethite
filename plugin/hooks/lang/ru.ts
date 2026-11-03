// Russian: the texts of stage 1. A key missing here is taken from English (en.ts).
import type { Texts } from './en'

export const ru: Partial<Texts> = {
  locale: 'ru-RU',

  // status line
  ctx: (tokens: string, cache: string) => `ctx ${tokens} · кэш ${cache}`,
  cacheLeft: (min: number) => `~${min} мин`,
  cacheExpired: 'истёк',
  cacheUnknown: '?',

  // marks: whose a band and a toast are, so neither is taken for another mod's (the engine labels the status line with the plugin name itself). Every other text here is written without the name.
  bandTitle: 'Alethite - 🛡 CacheGuard',
  toast: (text: string) => `Alethite: ${text}`,

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
  willClear: 'Этот чат будет очищен, передача дел сохранится в файл.',
  sendNow: 'Отправить сразу',
  pickInMenu: 'Выберу модель сам',
  cancel: 'Отмена',
  atFilesHint: '@файлы и картинки из черновика не передаются',
  busy: 'Запускаю…',

  // new worker launch: every failure says what was kept and where
  draftEmpty: 'Новый работник: черновик пуст, чат не тронут. Набери сообщение и нажми снова.',
  handoffFailed: (why: string) => `Новый работник: передача не собрана (${why}). Чат не тронут.`,
  clearFailed: (why: string, path: string) => `Новый работник: чат не очистился (${why}). Передача сохранена: ${path}`,
  pickThenEnter: 'Чат очищен, передача дел и черновик в поле ввода. Выберите модель и усилие в меню приложения справа внизу и нажмите Enter.',
  notSent: 'передача не отправлена',
  submitFailed: (why: string, path: string) => `Новый работник: отправка не удалась (${why}). Передача и черновик в поле ввода: нажми Enter. Передача сохранена: ${path}`,
  fillFailed: (why: string, fillWhy: string, path: string) => `Новый работник: ${why}. Чат уже очищен, а поле ввода не приняло передачу (${fillWhy}). Передача сохранена: ${path}; черновик, скорее всего, ещё в поле ввода.`,
  fillRefused: { dialog: 'открыт диалог, поле занято', no_composer: 'в этом окне нет поля ввода', other: 'поле ввода не приняло текст' },
  dropped: (why: string, path: string) => `Новый работник: отправку отклонили (${why}). Передача сохранена: ${path}`,

  // refresh failed: the status line shows it until the next good refresh
  statusError: (msg: string) => `ошибка — ${msg}`,
  indexProblemStatus: 'Индекс недоступен, подробности в /al-health',

  // /al-band
  cgBandDescription: 'Показать полосу принудительно (off: вернуть пороги)',
  cgBandOn: 'al-band: полоса показана принудительно.',
  cgBandOff: 'al-band: полоса снова по порогам.',
  cgBandFailed: 'al-band: не вышло, причина в строке состояния.',

  // errors: the toast of an owner-started action; cg.py could not be started at all
  failed: (where: string, msg: string) => `${where} — ${msg}`,
  pythonMissing: (python: string) => `Python не запустился или не ответил вовремя (команда «${python}», настройка «Python command»)`,
  catchUpFailed: (where: string) => `фоновое обновление памяти (${where}) не удалось, подробности — /al-health`,

  // long memory: what the model reads (tool descriptions, results) and what the owner sees about the index
  recallDescription: 'Поиск по прошлым разговорам владельца: что обсуждали и решали, что ломалось и почему, где что лежит. По умолчанию — в этом проекте, scope=all — везде. Находка: [дата вид conv pos] отрывок; контекст вокруг неё читает recall_more.',
  recallQuery: 'Слова, как их писали, не вопрос. Для прошлой ошибки — её точный текст, код или число (429, ENOENT, «database is locked»).',
  recallAlso: 'До 3 других формулировок, ищутся в том же вызове: для ошибки — действие и причина рядом с её текстом.',
  recallMoreDescription: 'Читает записи вокруг находки recall: по conv и pos из её строки возвращает соседние сообщения того разговора по порядку.',
  noHits: (query: string, scope: string) =>
    `По «${query}» ничего не найдено${scope === 'all' ? '' : ' в этом проекте'}. Попробуй ${scope === 'all' ? '' : 'scope=all или '}другие слова; для ошибки — её текст или код в also.`,
  noQuery: 'query не передан: нужны слова, которые встретились бы в разговоре.',
  noRows: 'Записей нет: проверь conv и pos в строке находки recall.',
  recallFailed: (msg: string) => `Поиск по памяти не удался: ${msg}`,
  vectorOff: (why: string) => `векторный поиск недоступен: ${why}`,
  indexErrors: (n: number) => `индекс — ${n} ошибок, см. /al-health`,
  indexErrorFirst: (n: number, path: string, msg: string) => `не прочитано файлов: ${n}, первый ${path} — ${msg}`,
  cgIndexDescription: 'Обновить индекс долгой памяти (rebuild: перечитать все транскрипты и добавить недостающее)',
  cgIndexHint: '[rebuild]',
  cgIndexDone: (i: Record<string, unknown>) => `Индекс обновлён: ${i.files} файлов, ${i.rows} новых записей, всего ${i.convs} чатов, ${(i.seconds as number).toFixed(1)} с`,
  cgIndexFailed: 'al-index: не вышло, причина во всплывающем сообщении.',

  // /al-health
  cgHealthDescription: 'Проверить, что мод, индекс, память и синхронизация работают',
  cgHealthFailed: 'al-health: не вышло, причина во всплывающем сообщении.',
  healthVersion: (version: string) => `Claude Code ${version}`,
  healthIndex: (i: Record<string, unknown>) => `Индекс: ${i.convs} чатов, ${i.rows} записей, ${i.size_mb} МБ, обновлён ${i.last_update ?? 'ни разу'} (${i.db})`,
  healthIndexError: (msg: string) => `Индекс: ошибка — ${msg}`,
  projectLine: (root: string, convs: number) => `Проект: ${root} — разговоров в индексе: ${convs}`,
  projectEmpty: 'Поиск «в этом проекте» ничего не найдёт: попросите scope=all',
  healthMemory: (dir: string, facts: number) => `Память: ${facts} файлов в ${dir}`,
  memoryIndexTokens: (n: number) => `MEMORY.md в каждом запросе: ~${n} токенов`,
  compressionLine: (level: string) => `Сжатие памяти: ${level} (настройка «compression»: off, lite, full, ultra)`,
  memoryNotLoaded: 'Память: папка auto-memory в этот чат не загружена',
  healthLastError: (when: string, where: string, msg: string) => `Последняя ошибка: ${when}, ${where} — ${msg}`,
  healthNoError: 'Последняя ошибка: нет',
  healthSettings: (path: string) => `Настройки: ${path} (главнее /config; после правки — /reload-plugins)`,
  settingsBad: (path: string, keys: string[]) => `${path}: такой настройки нет или значение не того типа, пропущено: ${keys.join(', ')}`,
  settingsNotObject: 'в файле настроек должен быть один объект JSON: { "language": "ru", ... }',
  vectorsLine: (model: string | null, withVectors: number, total: number, updated: string | null) => `Векторы: ${withVectors} из ${total} записей (${model ?? 'модель не выбрана'}), обновлены ${updated ?? 'ни разу'}`,
  vectorsFailed: (n: number) => `Записей, которые модель не смогла посчитать: ${n} (находятся только по словам)`,
  vectorsHint: (cmd: string) => `Досчитать в терминале (можно остановить и продолжить): ${cmd}`,

  // sync of two computers: /al-health, /al-sync and the background run
  healthSyncOff: 'Синхронизация: выключена (настройка «syncDir»)',
  healthSync: (dir: string, machine: string) => `Синхронизация: ${dir}, этот компьютер ${machine}`,
  healthOrigins: (parts: string) => `Записи по компьютерам: ${parts}`,
  healthOwnBatches: (machine: string, n: number) => `${machine} (этот): выложено пачек до ${n}`,
  healthPeer: (machine: string, n: number, model: string | null) => `${machine}: влито пачек до ${n}${model ? ` (векторы: ${model})` : ''}`,
  healthPeerSkipped: (machine: string, batches: string) => `${machine}: пачки, пропущенные вручную: ${batches}`,
  healthPeerError: (machine: string, msg: string) => `${machine}: ошибка — ${msg}`,
  healthSyncLast: (at: string, pushed: number, pulled: number, errors: number) => `Последняя синхронизация: ${at}, выложено записей ${pushed}, влито ${pulled}, ошибок ${errors}`,
  healthSyncNever: 'Последняя синхронизация: ни разу',
  healthSyncError: (msg: string) => `Ошибка синхронизации: ${msg}`,
  syncMemory: (m: { pushed: number; pulled: number; conflicts: number }) => `Короткая память: выложено фактов ${m.pushed}, влито ${m.pulled}, совпадений имён ${m.conflicts}`,
  syncMemoryOff: 'Короткая память: не синхронизируется, папка автопамяти в этот чат не загружена',
  cgSyncDescription: 'Синхронизировать память с другими компьютерами сейчас (skip <компьютер> <N>: идти дальше без испорченной пачки)',
  cgSyncHint: '[skip <компьютер> <N>]',
  cgSyncOff: 'al-sync: синхронизация выключена. Укажите общую папку в «syncDir» файла настроек (его путь — в /al-health), затем /reload-plugins.',
  cgSyncSkipUsage: 'al-sync skip: нужны компьютер и номер пачки, например /al-sync skip MTLAPTOP 12',
  cgSyncDone: (r: { pushed: number; pulled: number; vectors: { pushed: number; pulled: number } }) =>
    `Синхронизация: выложено записей ${r.pushed} (векторов ${r.vectors.pushed}), влито ${r.pulled} (векторов ${r.vectors.pulled})`,
  cgSyncPeers: (parts: string) => `Компьютеры, последняя влитая пачка: ${parts}`,
  cgSyncNoPeers: 'Другие компьютеры в папку пока ничего не выложили',
  cgSyncForgotten: (n: number) => `Записей забыто по просьбе другого компьютера: ${n}`,
  cgSyncSkippedVectors: (n: number) => `Записей с векторами другой модели (посчитаются здесь): ${n}`,
  cgSyncWarning: (msg: string) => `Внимание: ${msg}`,
  cgSyncError: (msg: string) => `Ошибка: ${msg}`,
  cgSyncFailed: 'al-sync: не вышло, причина во всплывающем сообщении.',
  syncErrors: (n: number) => `синхронизация — ${n} ошибок, см. /al-health`,

  // /al-stats: the reply, and the summary note written into the vault
  cgStatsDescription: 'Замер расходов за N дней (по умолчанию 7): заметка и сводка в Obsidian',
  cgStatsHint: '[дней]',
  cgStatsDays: 'al-stats: число дней — целое больше нуля, например /al-stats 7.',
  noHost: 'имя компьютера неизвестно (переменная COMPUTERNAME пуста)',
  cgStatsErrors: (n: number) => `Внимание: цифры могут быть занижены или неточны, ошибок при замере: ${n} (подробности в /al-health).`,
  cgStatsSent: (n: number) => `В Obsidian записано заметок: ${n}, сводка обновлена.`,
  cgStatsQueued: (n: number) => `Obsidian недоступен (причина во всплывающем сообщении). В очереди неотправленных заметок: ${n}; следующий /al-stats их дошлёт.`,
  cgStatsSummaryFailed: 'Заметка записана, но сводка не пересобрана (причина во всплывающем сообщении); следующий /al-stats попробует снова.',
  summaryTruncated: 'В папке замеров больше 1000 файлов — сводка неполная, разложите старые заметки по подпапкам',
  cgStatsFailed: 'al-stats: не вышло, причина во всплывающем сообщении.',
  summaryTitle: '# Сводка замеров',
  summaryHint: 'Собрана командой /al-stats из заметок по компьютерам; руками не править.',
  summaryColumns: ['дата', 'компьютер', '$ на сообщение владельца', 'перезаписи кэша', 'из них после паузы ≥ 60 мин', 'медианный контекст', 'сообщений владельца', 'результаты recall'],

  // short memory: the rule the model reads in every prompt, and the owner's note that a fact was saved
  memoryRule:
    'Память работника (Alethite). Правила из CLAUDE.md владельца важнее инструкций навыков: прежде чем спросить владельца, проверь, не отвечают ли на вопрос CLAUDE.md или память. Понятую ошибку сразу сохраняй через lesson_save, не в автопамять; решения владельца — в автопамять, один факт на файл. Прежде чем предлагать подход, проверь память; если не уверен, пробовали ли это, вызови recall (этот проект; scope=all — только по просьбе), для ошибки — по её тексту, и цитируй найденное.',
  remembered: (name: string) => `запомнил: ${name}`,

  // lessons (stage 4): a past error and what it taught, in the index, brought back only where it fits
  lessonSaveDescription:
    'Сохраняет урок: ошибку, на которую наткнулись в этом проекте, и что она показала, чтобы не повторить. Вызывай, когда причина ясна. Мод покажет урок снова, когда сообщение или команда окажутся близко к нему; в автопамять он не идёт и иначе ничего не стоит.',
  lessonAction: 'Что сделали: команда, изменение.',
  lessonSymptom: 'Что случилось: текст ошибки, код или число как есть.',
  lessonCause: 'Почему это случилось.',
  lessonFix: 'Как надо вместо этого, если известно.',
  lessonTrigger: 'Необязательно: дословный кусок команды не короче 8 символов (например «gh api /repos»), который остановит её и напомнит урок перед новым запуском.',
  lessonSaved: (text: string) => `Урок сохранён:\n${text}`,
  lessonKnown: 'Этот урок уже сохранён.',
  lessonSaveFailed: (msg: string) => `Урок не сохранён: ${msg}`,
  lessonToast: (action: string) => `урок сохранён: ${action}`,
  lessonsIntro: 'Alethite: уроки этого проекта, которые могут касаться этого сообщения. Проверь их, прежде чем действовать; что не относится к делу — пропусти.',
  lessonGate: (lesson: string) =>
    `Alethite один раз остановил эту команду: в ней триггер урока этого проекта. ${lesson}. Если урок сейчас ни при чём, запусти команду ещё раз — она пройдёт.`,
  noLessons: 'В этом проекте уроков пока нет.',
  cgLessonsDescription: 'Уроки этого проекта (forget <pos>: удалить урок здесь и на других компьютерах)',
  cgLessonsHint: '[forget <pos>]',
  cgLessonsUsage: 'Так: /al-lessons или /al-lessons forget <pos> с pos из списка.',
  cgLessonsForgotten: (n: number) => (n ? 'Урок удалён здесь, а со следующей синхронизацией — и на других компьютерах.' : 'Урока с таким pos нет.'),
  cgLessonsFailed: 'al-lessons: не получилось, причина — в уведомлении.',
  healthLessons: (count: number, shown: number, chars: number, stopped: number, on: boolean) =>
    on
      ? `Уроки: в этом проекте ${count}; показаны с сообщением ${shown} раз (~${Math.round(chars / 3.5)} токенов всего), команд остановлено ${stopped}`
      : `Уроки: в этом проекте ${count}; напоминания выключены (настройка «Lessons»)`,

  // the background job (stage 4)
  cgJobDescription: 'Фоновая задача этого компьютера (Windows): on [минут], off или её состояние',
  cgJobHint: '[on [минут] | off]',
  cgJobUsage: 'Так: /al-job, /al-job on [минут, по умолчанию 30], /al-job off.',
  cgJobOn: (minutes: number) =>
    `Фоновая задача включена: раз в ${minutes} минут индекс, векторы (от сети) и синхронизация идут без открытого чата. Это задача «Alethite» Планировщика заданий, с настройками из файла мода.`,
  cgJobOff: 'Фоновая задача выключена: задача «Alethite» удалена. Мод догоняет при открытии чата, как раньше.',
  cgJobState: (minutes: number | null) =>
    minutes === null ? 'Фоновая задача: на этой системе пока нет (только Windows); мод догоняет при открытии чата.' : minutes ? `Фоновая задача: включена, раз в ${minutes} минут.` : 'Фоновая задача: выключена (/al-job on).',
  cgJobFailed: 'al-job: не получилось, причина — в уведомлении.',
  jobIndex: (rows: number) => `индекс +${rows}`,
  jobEmbed: (done: number, left: number) => `векторы +${done} (осталось ${left})`,
  jobOnBattery: 'векторы ждут сети',
  jobEmbedBusy: 'векторы считал чат',
  jobSync: (up: number, down: number) => `синхронизация ↑${up} ↓${down}`,
  jobLast: (at: string, did: string, errors: number) => `Фоновая задача, последний запуск ${at}: ${did}${errors ? `; ошибок: ${errors}` : ''}`,
  jobError: (where: string, error: string) => `Ошибка фоновой задачи (${where}): ${error}`,
  jobNever: 'Фоновая задача: запусков ещё не было (/al-job on).',

  // encryption of the index and the sync folder (stage 5)
  cgEncryptDescription: 'Шифрование индекса и папки синхронизации: on, join (по коду восстановления с другого компьютера), off или состояние',
  cgEncryptHint: '[on | join | off | status]',
  cgEncryptUsage: 'Так: /al-encrypt (состояние), /al-encrypt on, /al-encrypt join (по коду восстановления с другого компьютера), /al-encrypt off.',
  encryptWindow: 'смотрите окно Alethite, оно может быть за другими окнами',
  encryptDone: (action: string) =>
    action === 'off'
      ? 'Шифрование выключено: индекс и то, что этот компьютер кладёт в папку синхронизации, снова открыты.'
      : action === 'join'
        ? 'Этот компьютер присоединился к шифрованию: ключ у него теперь есть, индекс и то, что он кладёт в папку синхронизации, зашифрованы.'
        : 'Шифрование включено: индекс и то, что этот компьютер кладёт в папку синхронизации, зашифрованы.',
  encryptUnfinished: 'al-encrypt: не закончено. Команду можно безопасно повторить: она продолжит с того места, где остановилась.',
  encryptTimeout: (action: string) => `не закончилось за 10 минут и было остановлено. Каждый шаг можно безопасно повторить: запустите /al-encrypt ${action} ещё раз или посмотрите /al-encrypt status.`,
  cgEncryptFailed: 'al-encrypt: не получилось, причина — в уведомлении.',
  healthEncryptionOn: (index: string, share: string) => `Шифрование: включено (индекс: ${index}, папка синхронизации: ${share})`,
  healthEncryptionOff: 'Шифрование: выключено',

  // the folder of the index (/al-index-dir)
  cgIndexDirDescription: 'Перенести индекс в папку по вашему выбору на локальном диске. Без пути окно спросит папку',
  cgIndexDirHint: '[папка]',
  indexDirWindow: 'смотрите окно Alethite с вопросом о папке, оно может быть за другими окнами',
  indexDirDone: (dir: string) => `Индекс теперь в ${dir}.`,
  indexDirUnfinished: 'al-index-dir: не закончено.',
  indexDirTimeout: 'не закончилось за 10 минут и было остановлено. /al-health покажет, где индекс сейчас. Если он не там, где нужно, запустите /al-index-dir ещё раз.',
}
