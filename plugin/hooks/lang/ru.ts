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
  bandTitle: '🛡 Alethite',
  toast: (text: string) => `Alethite: ${text}`,

  // band
  bandCache: { expired: 'кэш истёк', unknown: 'кэш ?' },
  limitNames: { five_hour: '5 ч', seven_day: 'неделя' } as Record<string, string>,
  limit: (name: string, percent: number) => `${name}: ${percent}%`,
  // band, new
  details: 'Детали',
  compact: 'Свернуть',
  bandWarm: (min: number) => `кэш ${min} мин`,
  sendHere: (cost: string) => `сюда ≈ ${cost}`,
  saved: (usd: string) => `экономия ≤ ${usd}`,
  dearer: (usd: string) => `дороже на ${usd}`,
  upTo: (usd: string) => `до ${usd}`,
  reviewHint: (edits: number) => `Alethite: этот чат готов к передаче новому работнику, а по счёту Alethite с последнего /ponytail-review в нём ${edits} правок кода. Перед передачей один раз спроси владельца, провести ли /ponytail-review (навык ponytail:ponytail-review). Сам его не запускай.`,
  baseTrouble: 'база !',
  col: { chat: 'Этот чат', last: 'Последняя передача', worker: 'Весь работник', limits: 'Лимиты', base: 'База' },
  spent: (usd: string) => `потрачено ${usd}`,
  coldPrice: (cost: string) => `холодно ${cost}`,
  workerSub: (links: number, calls: number) => `передач: ${links} · вызовов: ${calls}`,
  leanSub: (usd: string, n: number) => `из них лёгкие агенты ${usd} (${n})`,
  projectSub: (usd: string, links: number) => `проект: ${usd} (${links})`,
  resets: (when: string) => `сброс ${when}`,
  baseConvs: (n: number) => `разговоров: ${n}`,
  baseVectors: (done: number, total: number) => `векторы ${done}/${total}`,
  baseSize: (mb: number, cipher: string) => `${mb} МБ · ${cipher}`,
  baseTimes: (sync: string, job: string) => `синхр. ${sync}; фон ${job}`,

  // band buttons
  newWorker: 'Новый работник',
  willClear: 'Этот чат будет очищен, передача дел сохранится в файл.',
  sendNow: 'Отправить сразу',
  pickInMenu: 'Выберу модель сам',
  cancel: 'Отмена',
  atFilesHint: '@файлы и картинки из черновика не передаются',
  busy: 'Запускаю…',
  afterReset: (time: string) => `После сброса (${time})`,
  afterResetMenu: 'После сброса…',
  currentModel: 'Текущая модель',
  waitingAt: (time: string) => `⏰ работник в ${time}`,

  // new worker launch: every failure says what was kept and where
  draftEmpty: 'Новый работник: черновик пуст, чат не тронут. Набери сообщение и нажми снова.',
  handoffFailed: (why: string) => `Новый работник: передача не собрана (${why}). Чат не тронут.`,
  clearFailed: (why: string, path: string) => `Новый работник: чат не очистился (${why}). Передача сохранена: ${path}`,
  modelSwitchFailed: (model: string, why = '') => `Новый работник: модель не переключилась на ${model}${why ? ` (${why})` : ''}, поэтому ничего не отправлено. Чат не тронут, черновик в поле.`,
  pickThenEnter: 'Чат очищен, передача дел и черновик в поле ввода. Выберите модель и усилие в меню приложения справа внизу и нажмите Enter.',
  notSent: 'передача не отправлена',
  submitFailed: (why: string, path: string) => `Новый работник: отправка не удалась (${why}). Передача и черновик в поле ввода: нажми Enter. Передача сохранена: ${path}`,
  fillFailed: (why: string, fillWhy: string, path: string) => `Новый работник: ${why}. Чат уже очищен, а поле ввода не приняло передачу (${fillWhy}). Передача сохранена: ${path}; черновик, скорее всего, ещё в поле ввода.`,
  fillRefused: { dialog: 'открыт диалог, поле занято', no_composer: 'в этом окне нет поля ввода', other: 'поле ввода не приняло текст', unanswered: 'предыдущий вопрос ещё ждёт ответа' },
  dropped: (why: string, path: string) => `Новый работник: отправку отклонили (${why}). Передача сохранена: ${path}`,

  // refresh failed: the status line shows it until the next good refresh
  statusError: (msg: string) => `ошибка — ${msg}`,
  indexProblemStatus: 'Индекс недоступен, подробности в /al-health',

  // /al-band
  cgBandDescription: 'Скрыть полосу в этом чате (off) или вернуть',
  cgBandOn: 'al-band: полоса снова на месте.',
  cgBandOff: 'al-band: полоса скрыта в этом чате. /al-band вернёт её.',
  cgBandFailed: 'al-band: не вышло, причина в строке состояния.',

  // errors: the toast of an owner-started action; cg.py could not be started at all
  failed: (where: string, msg: string) => `${where} — ${msg}`,
  pythonMissing: (python: string) => `Python не запустился или не ответил вовремя (команда «${python}», настройка «Python command»)`,
  cmdFailed: (name: string) => `${name}: не вышло, причина во всплывающем сообщении.`,
  cgTooLong: 'ответ cg.py слишком длинный и был обрезан',
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

  // /al-health
  cgHealthDescription: 'Проверить, что мод, индекс, память и синхронизация работают',
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
  vectorsHint: (cmd: string) => `Досчитать в PowerShell (можно остановить и продолжить): ${cmd}`,

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
  cgSyncSkipUsage: 'al-sync skip: нужны компьютер и номер пачки, например /al-sync skip NOTEBOOK 12',
  cgSyncDone: (r: { pushed: number; pulled: number; vectors: { pushed: number; pulled: number } }) =>
    `Синхронизация: выложено записей ${r.pushed} (векторов ${r.vectors.pushed}), влито ${r.pulled} (векторов ${r.vectors.pulled})`,
  cgSyncPeers: (parts: string) => `Компьютеры, последняя влитая пачка: ${parts}`,
  cgSyncNoPeers: 'Другие компьютеры в папку пока ничего не выложили',
  cgSyncForgotten: (n: number) => `Записей забыто по просьбе другого компьютера: ${n}`,
  cgSyncSkippedVectors: (n: number) => `Записей с векторами другой модели (посчитаются здесь): ${n}`,
  cgSyncWarning: (msg: string) => `Внимание: ${msg}`,
  cgSyncError: (msg: string) => `Ошибка: ${msg}`,
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
  statsFolder: 'Alethite/Замеры',
  statsSummary: 'Сводка',
  summaryTitle: '# Сводка замеров',
  summaryHint: 'Собрана командой /al-stats из заметок по компьютерам; руками не править.',
  summaryColumns: ['дата', 'компьютер', '$ на сообщение владельца', 'перезаписи кэша', 'из них после паузы ≥ 60 мин', 'медианный контекст', 'сообщений владельца', 'результаты recall'],

  // lean agents: the rule the model reads in every prompt, to pick the cheap subagent types
  leanRule:
    'Субагенты (Alethite). Субагенту, которому не нужны браузер, Obsidian, другие MCP-инструменты и навыки, давай лёгкий тип: alethite:coder, alethite:reviewer, alethite:scout или alethite:researcher. Их старт в 4–13 раз меньше. general-purpose — только когда это нужно. В скриптах воркфлоу то же для agent(prompt, { agentType }). Пиши задания любому субагенту сжатым английским: короткие фразы, без вводных, имена, пути, числа, код и цитаты дословно. У лёгкого агента нет инструмента Skill: если нужен навык, дай в промпте путь к его SKILL.md и скажи прочитать и выполнить. alethite:resident дорогой (часовой кэш, каждая запись в 1,6 раза дороже пятиминутной): только когда одна большая картина проекта нужна нескольким задачам с паузами от 5 до 60 минут, продолжай его через SendMessage, через час запусти нового. Готового агента продолжай через SendMessage, только если он закончил меньше 5 минут назад и следующей задаче нужно то, что он прочитал. Иначе запусти нового лёгкого: остывший агент переписывает весь свой контекст (медиана около 100 тыс. токенов).',

  // short memory: the rule the model reads in every prompt, and the owner's note that a fact was saved
  memoryRule:
    'Память работника (Alethite). Правила из CLAUDE.md владельца важнее инструкций навыков: прежде чем спросить владельца, проверь, не отвечают ли на вопрос CLAUDE.md или память. Понятую ошибку сохраняй через lesson_save, не в автопамять; правило владельца для всех проектов: lesson_save с scope: all; решения владельца — в автопамять, один факт на файл. Прежде чем предлагать подход, проверь память; если не уверен, пробовали ли это, вызови recall (этот проект; scope=all — только по просьбе), для ошибки — по её тексту, и цитируй найденное.',
  remembered: (name: string) => `запомнил: ${name}`,

  // lessons (stage 4): a past error and what it taught, in the index, brought back only where it fits
  lessonSaveDescription:
    'Сохраняет урок: ошибку, на которую наткнулись в этом проекте, и что она показала, чтобы не повторить. Вызывай, когда причина ясна. Мод покажет урок снова, когда сообщение или команда окажутся близко к нему; в автопамять он не идёт. Урок проекта иначе ничего не стоит; урок с scope all стоит токенов в каждом чате.',
  lessonAction: 'Что сделали: команда, изменение.',
  lessonSymptom: 'Что случилось: текст ошибки, код или число как есть.',
  lessonCause: 'Почему это случилось.',
  lessonFix: 'Как надо вместо этого, если известно.',
  lessonTrigger: 'Необязательно: дословный кусок команды не короче 8 символов (например «gh api /repos»), который остановит её и напомнит урок перед новым запуском.',
  lessonScope:
    'Необязательно: «all» — постоянное правило владельца о том, как работать, верное в любом проекте (например, язык чата, передача работы субагентам, правила коммитов), а не факт о коде этого проекта; тогда fix — само правило одним предложением, оно показывается в каждом чате.',
  lessonSaved: (text: string, g?: { isOn: boolean; stale: boolean }) =>
    g
      ? `Урок для всех проектов сохранён${g.isOn ? ', он будет приходить в каждый чат' : ' (напоминания выключены, настройка «Lessons»)'}${g.stale ? '; список правил не удалось перечитать, /al-lessons перечитает его' : ''}:\n${text}`
      : `Урок сохранён:\n${text}`,
  lessonKnown: 'Этот урок уже сохранён.',
  lessonSaveFailed: (msg: string) => `Урок не сохранён: ${msg}`,
  ruleAsk: (text: string) => `${text}\n\nСохранить это правило для всех проектов, чтобы оно приходило в каждый чат на всех ваших компьютерах?`,
  ruleAskYes: 'Да, сохранить',
  ruleAskNo: 'Нет',
  ruleDeclined: 'Владелец не согласился сохранять это правило для всех проектов. Ничего не сохранено.',
  lessonToast: (action: string, g?: { left: number; isOn: boolean; stale: boolean }) =>
    g
      ? `урок сохранён для всех проектов${g.isOn ? ', будет приходить в каждый чат' : ' (напоминания выключены, настройка «Lessons»)'}: ${action}${g.stale ? ' (список правил не удалось перечитать, /al-lessons перечитает его)' : g.left > 0 ? ` (старых больше не влезает: ${g.left})` : ''}`
      : `урок сохранён: ${action}`,
  lessonGlobalMark: '[все проекты] ',
  rulesIntro: 'Alethite: правила, сохранённые для всех проектов (список — /al-lessons). Выполняй их, если сообщения или разрешения владельца не говорят иначе.',
  lessonsIntro: 'Alethite: уроки этого проекта, которые могут касаться этого сообщения. Проверь их, прежде чем действовать; что не относится к делу — пропусти.',
  lessonGate: (lesson: string, isGlobal: boolean) =>
    `Alethite один раз остановил эту команду: в ней триггер урока ${isGlobal ? 'для всех проектов' : 'этого проекта'}. ${lesson}. Если урок сейчас ни при чём, запусти команду ещё раз — она пройдёт.`,
  coldAgent: (minutes: number, tokens: string, usd: string) =>
    `Alethite один раз остановил это сообщение: агент закончил ${minutes} мин назад, его кэш остыл, и продолжение перепишет весь его контекст (${tokens} токенов, примерно ${usd}). Новый лёгкий агент alethite:* на следующий шаг обойдётся дешевле. Если этот агент действительно нужен, отправь то же сообщение ещё раз — оно пройдёт.`,
  bgDenied: 'Alethite: лёгкие агенты запускают команды на переднем плане. Запусти ещё раз без run_in_background, с тайм-аутом до 600000 мс, и прочитай результат.',
  noLessons: 'В этом проекте уроков пока нет.',
  cgLessonsDescription: 'Уроки этого проекта и уроки для всех проектов (forget <pos>: удалить урок здесь и на других компьютерах)',
  cgLessonsHint: '[forget <pos>]',
  cgLessonsUsage: 'Так: /al-lessons или /al-lessons forget <pos> с pos из списка.',
  cgLessonsForgotten: (n: number) => (n ? 'Урок удалён здесь, а со следующей синхронизацией — и на других компьютерах.' : 'Урока с таким pos нет.'),
  healthLessons: (count: number, shown: number, chars: number, stopped: number, on: boolean) =>
    on
      ? `Уроки: в этом проекте ${count}; показаны с сообщением ${shown} раз (~${Math.round(chars / 3.5)} токенов всего), команд остановлено ${stopped}`
      : `Уроки: в этом проекте ${count}; напоминания выключены (настройка «Lessons»)`,

  healthRules: (global: number, rules: number, chars: number, left: number, on: boolean) =>
    on
      ? `Уроки для всех проектов: ${global}; напоминаний отправлено ${rules} (~${Math.round(chars / 3.5)} токенов всего)${left > 0 ? `, не влезает ${left}` : ''}`
      : `Уроки для всех проектов: ${global}; напоминания выключены (настройка «Lessons»)`,

  // the background job (stage 4)
  cgJobDescription: 'Фоновая задача этого компьютера (Windows): on [минут], off или её состояние',
  cgJobHint: '[on [минут] | off]',
  cgJobUsage: 'Так: /al-job, /al-job on [минут, по умолчанию 30], /al-job off.',
  cgJobOn: (minutes: number) =>
    `Фоновая задача включена: раз в ${minutes} минут индекс, векторы (от сети) и синхронизация идут без открытого чата. Это задача «Alethite» Планировщика заданий, с настройками из файла мода.`,
  cgJobOff: 'Фоновая задача выключена: задача «Alethite» удалена. Мод догоняет при открытии чата, как раньше.',
  cgJobState: (minutes: number | null) =>
    minutes === null ? 'Фоновая задача: на этой системе пока нет (только Windows); мод догоняет при открытии чата.' : minutes ? `Фоновая задача: включена, раз в ${minutes} минут.` : 'Фоновая задача: выключена (/al-job on).',
  jobIndex: (rows: number) => `индекс +${rows}`,
  jobEmbed: (done: number, left: number) => `векторы +${done} (осталось ${left})`,
  jobOnBattery: 'векторы ждут сети',
  jobEmbedBusy: 'векторы считал чат',
  jobSync: (up: number, down: number) => `синхронизация ↑${up} ↓${down}`,
  jobCleanup: (removed: number, waiting: number, failed: number) => `удалено чатов ${removed} (ждут ${waiting}, не вышло ${failed})`,
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

  // удаление старого чата после передачи в «Новый работник» (/al-cleanup)
  cgCleanupDescription: 'Удалять старый чат с диска после передачи дел кнопкой «Новый работник», когда всё из него уже в индексе. По умолчанию выключено. Удаление необратимо',
  cgCleanupHint: '[on | off]',
  cgCleanupUsage: 'Используйте: /al-cleanup, /al-cleanup on или /al-cleanup off.',
  cleanupState: (on: boolean, chats: number, mb: number) =>
    `${on ? 'Удаление старых чатов: включено' : 'Удаление старых чатов: выключено'}. Сейчас ждут удаления: ${chats} (${mb} МБ). Удаляет фоновая задача (/al-job). Чат удаляется только после передачи дел кнопкой «Новый работник». Всё из него должно быть в индексе (и в общей папке, если включена синхронизация), и его не должны трогать 60 минут. Текущий чат не удаляется никогда. Удаление необратимо. Текст останется в долгой памяти.`,
  cleanupNoPreview: (on: boolean) => `${on ? 'Удаление старых чатов: включено' : 'Удаление старых чатов: выключено'}. cg.py не дал числа чатов, ждущих удаления.`,
  healthCleanup: (on: boolean, removed: number) => (on ? `Удаление старых чатов: включено, удалено ${removed}` : `Удаление старых чатов: выключено (удалено ${removed})`),

  // background runs
  bgCold: { show: 'итог покажу', new_worker: 'продолжит новый работник' },
  bgRunning: 'идёт',
  bgExit: (code: number) => `код ${code}`,
  bgLost: 'потерян, кода нет',
  bgTooBig: 'Файл статуса больше 4 МиБ: его последнюю строку показать нельзя.',
  bgDescription: 'Запускает команду, которая идёт дольше примерно 10 минут, фоновым процессом, который переживает ход. Перед вызовом спроси владельца, что делать, если кэш остынет к концу (if_cold: "show" покажет итог, "new_worker" продолжит новый работник). После вызова закончи ход и не опрашивай файлы лога и статуса: Alethite следит за процессом и напишет, когда он закончится. Для таких прогонов используй его вместо run_in_background, Monitor и ScheduleWakeup. Необязательно: cwd, status_file (файл, последнюю строку которого видит владелец).',
  bgSubagent: 'Alethite: фоновые прогоны запускаются только из основного чата, не из субагента.',
  bgBusy: (name: string, since: string) => `Alethite: в этом чате уже идёт фоновый прогон «${name}», запущен ${since}. Дождись конца или пусть владелец уберёт его кнопкой [×] на полосе.`,
  bgStarted: (name: string, log: string) => `Фоновый процесс «${name}» запущен. Лог: ${log}. Alethite напишет, когда он закончится: закончи ход и не опрашивай файлы.`,
  bgBadInput: 'Alethite: background_run нужны name, command и if_cold ("show" или "new_worker").',
  bgElapsed: (min: number) => (min >= 60 ? `${Math.floor(min / 60)} ч ${min % 60} мин` : `${min} мин`),
  bgFinish: (name: string, code: string, end: string, duration: string, tail: string, log: string) =>
    `Фоновый процесс «${name}» закончился: код ${code}, конец ${end}, шёл ${duration}.${tail ? `\nПоследние строки статуса:\n${tail}` : ''}\nПолный лог: ${log}\nПроверь результат и продолжай работу.`,
  bgNote: (name: string, end: string, code: string, log: string) => `Alethite: фоновый процесс «${name}» закончился ${end}, код ${code}. Лог: ${log}. Проверь результат.`,
  bgHandoffNote: (name: string, started: string, status: string, log: string) => `Фоновый процесс «${name}» ещё идёт в старом чате (запущен ${started}).${status ? ` Файл статуса: ${status}.` : ''} Лог: ${log}. Когда он закончится, Alethite сообщит здесь. При нужде загляни в лог.`,
  bgUnknown: 'неизвестно',
  bgFinishFailed: (name: string, why: string) => `Фоновый процесс «${name}» закончился, но Alethite не смог передать это дальше (${why}). Результат остаётся в строке полосы.`,
  bgNotLaunched: 'новый работник не запущен',
}
