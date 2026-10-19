// Pure side of /cg-stats: where the notes live in the vault, the summary table, the outbox flush.
// The Obsidian calls (`$.mcp.call`) live in register.tsx: the loader follows `$` only inside its own file.
import { message } from './py'
import { T } from './text'

export type Note = { path: string; content: string }

export const FOLDER = 'Claude-CacheGuard/Замеры'
export const SUMMARY_PATH = `${FOLDER}/Сводка.md`
export const notePath = (host: string, today: string) => `${FOLDER}/${today} ${host}.md`

// Sends in order and stops at the first failure; what was not sent is `left`, for the next run.
export const flush = async (queue: Note[], send: (n: Note) => Promise<void>): Promise<{ sent: number; left: Note[]; error?: string }> => {
  let sent = 0
  try {
    for (; sent < queue.length; sent++) await send(queue[sent]!)
  } catch (err) {
    return { sent, left: queue.slice(sent), error: message(err) }
  }
  return { sent, left: [] }
}

const num = (x: unknown, show: (n: number) => string) => (typeof x === 'number' ? show(x) : '—')
const usd = (n: number) => `$${n.toFixed(2)}`
const pct = (n: number) => `${(n * 100).toFixed(1)}%`
const grouped = (n: number) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ')

// `fronts` are the frontmatters of the folder's files; one that is no measurement (no host) is left out.
export const summaryNote = (fronts: Record<string, unknown>[]) => {
  const rows = fronts
    .filter(f => typeof f.host === 'string' && f.date !== undefined)
    .map(f => ({ date: String(f.date).slice(0, 10), host: f.host as string, f }))
    .sort((a, b) => b.date.localeCompare(a.date) || a.host.localeCompare(b.host))
    .map(({ date, host, f }) => [
      date,
      host,
      num(f.usd_per_owner_msg, usd),
      num(f.rewrite_share, pct),
      num(f.idle60_share, pct),
      num(f.median_main_context, grouped),
      num(f.owner_msgs, grouped),
      num(f.recall_share, pct), // stage 3: what recall's results cost; a note from before has none
    ])
  const line = (cells: string[]) => `| ${cells.join(' | ')} |`
  return [T.summaryTitle, '', T.summaryHint, '', line(T.summaryColumns), line(T.summaryColumns.map(() => '---')), ...rows.map(line)].join('\n') + '\n'
}
