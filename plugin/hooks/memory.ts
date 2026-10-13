// Pure side of the short memory: is this path a fact file of Claude Code's auto-memory (the index MEMORY.md is not one).
// The hooks that use it hold `$` and so stay in register.tsx.
const FACT = /[\\/]\.claude[\\/]projects[\\/][^\\/]+[\\/]memory[\\/](?!memory\.md$)[^\\/]+\.md$/i

export const isMemoryFact = (path: string) => FACT.test(path)
