// Pure side of the short memory: is this path a fact file of Claude Code's auto-memory (the index MEMORY.md is not one).
// The hooks that use it hold `$` and so stay in register.tsx.
const FACT = /[\\/]\.claude[\\/]projects[\\/][^\\/]+[\\/]memory[\\/](?!memory\.md$)[^\\/]+\.md$/i

export const isMemoryFact = (path: string) => FACT.test(path)

type MemoryFile = { path: string; type: string; tokens: number }

// The auto-memory folder is the one the first loaded AutoMem file sits in; none loaded, none known.
export const autoMemDir = (files: readonly MemoryFile[]) => files.find(f => f.type === 'AutoMem')?.path.replace(/[\\/][^\\/]*$/, '') ?? null

// What the auto-memory puts in every request (MEMORY.md), as the engine counts it.
export const autoMemTokens = (files: readonly MemoryFile[]) => files.filter(f => f.type === 'AutoMem').reduce((n, f) => n + f.tokens, 0)
