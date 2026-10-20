// Ukrainian. A key missing here is taken from English (en.ts).
import type { Texts } from './en'

export const uk: Partial<Texts> = {
  locale: 'uk-UA',

  // status line
  ctx: (tokens: string, cache: string) => `ctx ${tokens} · кеш ${cache}`,
  cacheLeft: (min: number) => `~${min} хв`,
  cacheExpired: 'сплив',
  cacheUnknown: '?',

  // marks: whose a band and a toast are, so neither is taken for another mod's (the engine labels the status line with the plugin name itself). Every other text here is written without the name.
  bandTitle: '🛡 CacheGuard',
  toast: (text: string) => `CacheGuard: ${text}`,

  // band
  head: (k: string, ago: string, cache: string) => `Цей чат: ${k} тис. токенів · остання відповідь ${ago} · ${cache}`,
  bandCache: { expired: 'кеш сплив', warm: 'кеш теплий', unknown: 'кеш ?' },
  agoNow: 'щойно',
  agoMin: (n: number) => `${n} хв тому`,
  agoHours: (n: number) => `${n} год тому`,
  agoDays: (n: number) => `${n} дн тому`,
  send: (cost: string) => `Надіслати сюди ≈ ${cost}`,
  sendHot: (cost: string) => `${cost} (кеш теплий)`,
  sendCold: (cost: string) => `${cost} (перезапис кешу)`,
  sendEither: (hot: string, cold: string) => `${hot} або ${cold}`,
  limitNames: { five_hour: '5 год', seven_day: 'тиждень' } as Record<string, string>,
  limit: (name: string, percent: number) => `${name}: ${percent}%`,
  limits: (parts: string) => `Ліміт ${parts}`,

  // band buttons
  newWorker: 'Новий працівник',
  stayHere: 'Лишитися тут',
  willClear: 'Цей чат буде очищено, передачу справ буде збережено у файл.',
  sendNow: 'Надіслати одразу',
  pickInMenu: 'Модель оберу сам',
  cancel: 'Скасувати',
  atFilesHint: '@файли та зображення з чернетки не передаються',
  busy: 'Запускаю…',

  // new worker launch: every failure says what was kept and where
  draftEmpty: 'Новий працівник: чернетка порожня, чат не зачеплено. Наберіть повідомлення й натисніть знову.',
  handoffFailed: (why: string) => `Новий працівник: передачу справ не зібрано (${why}). Чат не зачеплено.`,
  clearFailed: (why: string, path: string) => `Новий працівник: чат не очистився (${why}). Передачу збережено: ${path}`,
  pickThenEnter: 'Чат очищено, передача справ і чернетка в полі вводу. Оберіть модель і зусилля в меню застосунку праворуч унизу й натисніть Enter.',
  notSent: 'передачу не надіслано',
  submitFailed: (why: string, path: string) => `Новий працівник: надіслати не вдалося (${why}). Передача й чернетка в полі вводу: натисніть Enter. Передачу збережено: ${path}`,
  fillFailed: (why: string, fillWhy: string, path: string) =>
    `Новий працівник: ${why}. Чат уже очищено, а поле вводу не прийняло передачу (${fillWhy}). Передачу збережено: ${path}; чернетка, найімовірніше, ще в полі вводу.`,
  fillRefused: { dialog: 'відкрито діалог, поле зайняте', no_composer: 'у цьому вікні немає поля вводу', other: 'поле вводу не прийняло текст' },
  dropped: (why: string, path: string) => `Новий працівник: надсилання відхилено (${why}). Передачу збережено: ${path}`,

  // refresh failed: the status line shows it until the next good refresh
  statusError: (msg: string) => `помилка — ${msg}`,

  // /cg-band
  cgBandDescription: 'Показати смугу примусово (off: повернути пороги)',
  cgBandOn: 'cg-band: смугу показано примусово.',
  cgBandOff: 'cg-band: смуга знову за порогами.',
  cgBandFailed: 'cg-band: не вийшло, причина в рядку стану.',

  // errors: the toast of an owner-started action; cg.py could not be started at all
  failed: (where: string, msg: string) => `${where} — ${msg}`,
  pythonMissing: (python: string) => `Python не запустився або не відповів вчасно (команда «${python}», налаштування «Python command»)`,
  catchUpFailed: (where: string) => `фонове оновлення пам'яті (${where}) не вдалося, подробиці — /cg-health`,

  // long memory: what the model reads (tool descriptions, results) and what the owner sees about the index
  recallDescription:
    'Пошук у минулих розмовах власника: що обговорювали, вирішували, де що лежить. query — слова шуканого, не питання. Типово шукає в цьому проєкті, scope=all — скрізь. Знахідка: [дата вид conv pos] уривок; контекст навколо неї читає recall_more.',
  recallMoreDescription: 'Читає записи навколо знахідки recall: за conv і pos з її рядка повертає сусідні повідомлення тієї розмови по черзі.',
  noHits: (query: string, scope: string) => `За «${query}» нічого не знайдено${scope === 'all' ? '' : ' в цьому проєкті'}. Спробуйте ${scope === 'all' ? '' : 'scope=all або '}інші слова.`,
  noQuery: 'query не передано: потрібні слова, які трапилися б у розмові.',
  noRows: 'Записів немає: перевірте conv і pos у рядку знахідки recall.',
  recallFailed: (msg: string) => `Пошук у пам'яті не вдався: ${msg}`,
  vectorOff: (why: string) => `векторний пошук недоступний: ${why}`,
  indexErrors: (n: number) => `індекс — ${n} помилок, див. /cg-health`,
  indexErrorFirst: (n: number, path: string, msg: string) => `не прочитано файлів: ${n}, перший ${path} — ${msg}`,
  cgIndexDescription: "Оновити індекс довгої пам'яті (rebuild: перечитати всі транскрипти й додати те, чого бракує)",
  cgIndexHint: '[rebuild]',
  cgIndexDone: (i: Record<string, unknown>) => `Індекс оновлено: ${i.files} файлів, ${i.rows} нових записів, усього ${i.convs} чатів, ${(i.seconds as number).toFixed(1)} с`,
  cgIndexFailed: 'cg-index: не вийшло, причина у спливному повідомленні.',

  // /cg-health
  cgHealthDescription: "Перевірити, що мод, індекс, пам'ять і синхронізація працюють",
  cgHealthFailed: 'cg-health: не вийшло, причина у спливному повідомленні.',
  healthVersion: (version: string) => `Claude Code ${version}`,
  healthIndex: (i: Record<string, unknown>) => `Індекс: ${i.convs} чатів, ${i.rows} записів, ${i.size_mb} МБ, оновлено ${i.last_update ?? 'жодного разу'} (${i.db})`,
  healthIndexError: (msg: string) => `Індекс: помилка — ${msg}`,
  projectLine: (root: string, convs: number) => `Проєкт: ${root} — розмов в індексі: ${convs}`,
  projectEmpty: 'Пошук «у цьому проєкті» нічого не знайде: попросіть scope=all',
  healthMemory: (dir: string, facts: number) => `Пам'ять: ${facts} файлів у ${dir}`,
  memoryIndexTokens: (n: number) => `MEMORY.md у кожному запиті: ~${n} токенів`,
  compressionLine: (level: string) => `Стиснення пам'яті: ${level} (налаштування «compression»: off, lite, full, ultra)`,
  memoryNotLoaded: "Пам'ять: папку auto-memory в цей чат не завантажено",
  healthLastError: (when: string, where: string, msg: string) => `Остання помилка: ${when}, ${where} — ${msg}`,
  healthNoError: 'Остання помилка: немає',
  healthSettings: (path: string) => `Налаштування: ${path} (важливіші за /config; після зміни — /reload-plugins)`,
  settingsBad: (path: string, keys: string[]) => `${path}: такого налаштування немає або значення не того типу, пропущено: ${keys.join(', ')}`,
  settingsNotObject: 'у файлі налаштувань має бути один об’єкт JSON: { "language": "uk", ... }',
  vectorsLine: (model: string | null, withVectors: number, total: number, updated: string | null) =>
    `Вектори: ${withVectors} з ${total} записів (${model ?? 'модель не обрано'}), оновлено ${updated ?? 'жодного разу'}`,
  vectorsFailed: (n: number) => `Записів, які модель не змогла порахувати: ${n} (знаходяться лише за словами)`,
  vectorsHint: (cmd: string) => `Дорахувати в терміналі (можна зупинити й продовжити): ${cmd}`,

  // sync of two computers: /cg-health, /cg-sync and the background run
  healthSyncOff: 'Синхронізація: вимкнена (налаштування «syncDir»)',
  healthSync: (dir: string, machine: string) => `Синхронізація: ${dir}, цей комп'ютер ${machine}`,
  healthOrigins: (parts: string) => `Записи за комп'ютерами: ${parts}`,
  healthOwnBatches: (machine: string, n: number) => `${machine} (цей): викладено пачок до ${n}`,
  healthPeer: (machine: string, n: number, model: string | null) => `${machine}: влито пачок до ${n}${model ? ` (вектори: ${model})` : ''}`,
  healthPeerSkipped: (machine: string, batches: string) => `${machine}: пачки, пропущені вручну: ${batches}`,
  healthPeerError: (machine: string, msg: string) => `${machine}: помилка — ${msg}`,
  healthSyncLast: (at: string, pushed: number, pulled: number, errors: number) => `Остання синхронізація: ${at}, викладено записів ${pushed}, влито ${pulled}, помилок ${errors}`,
  healthSyncNever: 'Остання синхронізація: жодного разу',
  healthSyncError: (msg: string) => `Помилка синхронізації: ${msg}`,
  syncMemory: (m: { pushed: number; pulled: number; conflicts: number }) => `Коротка пам'ять: викладено фактів ${m.pushed}, влито ${m.pulled}, збігів імен ${m.conflicts}`,
  syncMemoryOff: "Коротка пам'ять: не синхронізується, папку автопам'яті в цей чат не завантажено",
  cgSyncDescription: "Синхронізувати пам'ять з іншими комп'ютерами зараз (skip <комп'ютер> <N>: іти далі без зіпсованої пачки)",
  cgSyncHint: "[skip <комп'ютер> <N>]",
  cgSyncOff: 'cg-sync: синхронізацію вимкнено. Вкажіть спільну папку в «syncDir» файлу налаштувань (його шлях — у /cg-health), потім /reload-plugins.',
  cgSyncSkipUsage: "cg-sync skip: потрібні комп'ютер і номер пачки, наприклад /cg-sync skip MTLAPTOP 12",
  cgSyncDone: (r: { pushed: number; pulled: number; vectors: { pushed: number; pulled: number } }) =>
    `Синхронізація: викладено записів ${r.pushed} (векторів ${r.vectors.pushed}), влито ${r.pulled} (векторів ${r.vectors.pulled})`,
  cgSyncPeers: (parts: string) => `Комп'ютери, остання влита пачка: ${parts}`,
  cgSyncNoPeers: "Інші комп'ютери в папку поки нічого не виклали",
  cgSyncForgotten: (n: number) => `Записів забуто на прохання іншого комп'ютера: ${n}`,
  cgSyncSkippedVectors: (n: number) => `Записів із векторами іншої моделі (порахуються тут): ${n}`,
  cgSyncWarning: (msg: string) => `Увага: ${msg}`,
  cgSyncError: (msg: string) => `Помилка: ${msg}`,
  cgSyncFailed: 'cg-sync: не вийшло, причина у спливному повідомленні.',
  syncErrors: (n: number) => `синхронізація — ${n} помилок, див. /cg-health`,

  // /cg-stats: the reply, and the summary note written into the vault
  cgStatsDescription: 'Замір витрат за N днів (типово 7): нотатка й зведення в Obsidian',
  cgStatsHint: '[днів]',
  cgStatsDays: 'cg-stats: кількість днів — ціле число більше нуля, наприклад /cg-stats 7.',
  noHost: "ім'я комп'ютера невідоме (змінна COMPUTERNAME порожня)",
  cgStatsErrors: (n: number) => `Увага: цифри можуть бути заниженими або неточними, помилок під час заміру: ${n} (подробиці в /cg-health).`,
  cgStatsSent: (n: number) => `До Obsidian записано нотаток: ${n}, зведення оновлено.`,
  cgStatsQueued: (n: number) => `Obsidian недоступний (причина у спливному повідомленні). У черзі ненадісланих нотаток: ${n}; наступний /cg-stats їх дошле.`,
  cgStatsSummaryFailed: 'Нотатку записано, але зведення не перезібрано (причина у спливному повідомленні); наступний /cg-stats спробує знову.',
  summaryTruncated: 'У папці замірів понад 1000 файлів — зведення неповне, розкладіть старі нотатки по підпапках',
  cgStatsFailed: 'cg-stats: не вийшло, причина у спливному повідомленні.',
  summaryTitle: '# Зведення замірів',
  summaryHint: "Зібране командою /cg-stats з нотаток за комп'ютерами; вручну не правити.",
  summaryColumns: ['дата', "комп'ютер", '$ на повідомлення власника', 'перезаписи кешу', 'з них після паузи ≥ 60 хв', 'медіанний контекст', 'повідомлень власника', 'результати recall'],

  // short memory: the rule the model reads in every prompt, and the owner's note that a fact was saved
  memoryRule:
    "Пам'ять працівника (CacheGuard). Правила з CLAUDE.md власника важливіші за інструкції навичок: перш ніж питати власника, перевір, чи не відповідають на питання CLAUDE.md або пам'ять. Граблі («X не працює, бо Y») і рішення власника одразу записуй в автопам'ять, один факт на файл. Перш ніж пропонувати підхід, перевір пам'ять; якщо не певен, чи пробували це, виклич recall (цей проєкт; scope=all — лише на прохання) і цитуй знайдене замість здогадок.",
  remembered: (name: string) => `запам'ятав: ${name}`,
}
