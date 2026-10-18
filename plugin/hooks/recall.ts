// Pure side of the memory tools: the cg.py command line for a search and the text the model reads back.
// Everything that touches `$` (tool registration, runCg, the index timer) lives in register.tsx: the loader follows `$` only inside its own file.
import { T } from './text'

export type Hit = { conv: string; parent: string; ts: string; kind: string; pos: number; snippet: string }
export type Row = { ts: string; kind: string; pos: number; text: string }
export type IndexError = { path: string; error: string }

// The query is one `--query=…` item: one starting with `-` would otherwise be read as a flag.
export const recallArgs = (input: { query: string; scope?: string; limit?: number }, cwd: string, model: string) => [
  'recall',
  `--query=${input.query}`,
  ...(input.scope === 'all' ? ['--all'] : ['--cwd', cwd]),
  '--limit',
  String(input.limit ?? 8),
  '--model',
  model,
]

const day = (ts: string) => ts.slice(0, 10)

export const formatHits = (hits: Hit[], query: string, scope: string) =>
  hits.length === 0 ? T.noHits(query, scope) : hits.map(h => `[${day(h.ts)} ${h.kind} conv=${h.conv} pos=${h.pos}] ${h.snippet.replace(/\s+/g, ' ')}`).join('\n')

export const formatRows = (rows: Row[]) => (rows.length === 0 ? T.noRows : rows.map(r => `[${day(r.ts)} ${r.kind} pos=${r.pos}] ${r.text}`).join('\n\n'))
