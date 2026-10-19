// Pure side of the short memory: is this path a fact file of Claude Code's auto-memory (the index MEMORY.md is not one).
// The hooks that use it hold `$` and so stay in register.tsx.
const FACT = /[\\/]\.claude[\\/]projects[\\/][^\\/]+[\\/]memory[\\/](?!memory\.md$)[^\\/]+\.md$/i

export const isMemoryFact = (path: string) => FACT.test(path)

type MemoryFile = { path: string; type: string; tokens: number }

// The auto-memory folder is the one the first loaded AutoMem file sits in; none loaded, none known.
export const autoMemDir = (files: readonly MemoryFile[]) => files.find(f => f.type === 'AutoMem')?.path.replace(/[\\/][^\\/]*$/, '') ?? null

// What the auto-memory puts in every request (MEMORY.md), as the engine counts it.
export const autoMemTokens = (files: readonly MemoryFile[]) => files.filter(f => f.type === 'AutoMem').reduce((n, f) => n + f.tokens, 0)

// The compression levels cg.py knows (stage 3). A desktop that draws the setting as a free string may hand over anything:
// case and spaces are forgiven, anything else is the default level rather than an argparse error that breaks recall.
const LEVELS = ['off', 'lite', 'full', 'ultra'] as const
export type Compression = (typeof LEVELS)[number]
export const compressionLevel = (value: unknown): Compression => {
  const level = String(value ?? '').trim().toLowerCase()
  return (LEVELS as readonly string[]).includes(level) ? (level as Compression) : 'ultra'
}
