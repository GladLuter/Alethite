import { expect, test } from 'claude-code/testing'
import { launch } from '../hooks/worker'
import type { LaunchIO } from '../hooks/worker'
import { T } from '../hooks/text'

// A LaunchIO that only writes down what was called; `o` makes one step fail or refuse.
const fake = (o: { draft?: string; handoffError?: string; clearError?: string; submitError?: string; fillError?: string; drop?: string } = {}) => {
  const calls: string[] = []
  const toasts: string[] = []
  const sent: string[] = []
  const filled: string[] = []
  const io: LaunchIO = {
    draft: async () => (calls.push('draft'), o.draft ?? 'черновик'),
    handoff: async () => {
      calls.push('handoff')
      if (o.handoffError) throw new Error(o.handoffError)
      return { text: '[al-handoff]\nтело\n[/al-handoff]', path: 'C:/h/old.md' }
    },
    clear: async () => {
      calls.push('clear')
      if (o.clearError) throw new Error(o.clearError)
    },
    submit: async text => {
      calls.push('submit')
      if (o.submitError) throw new Error(o.submitError)
      sent.push(text)
      return o.drop === undefined ? {} : { drop: o.drop }
    },
    fill: async text => {
      calls.push('fill')
      if (o.fillError) throw new Error(o.fillError)
      filled.push(text)
    },
    emptyBox: async () => void calls.push('emptyBox'),
    toast: text => void (calls.push('toast'), toasts.push(text)),
  }
  return { io, calls, toasts, sent, filled }
}

test('empty draft: nothing else runs', async () => {
  const { io, calls } = fake({ draft: '  ' })
  expect(await launch(io, 'send')).toBe('empty')
  expect(calls).toEqual(['draft', 'toast'])
})

test('handoff fails: chat is not cleared, reason shown', async () => {
  const { io, calls, toasts } = fake({ handoffError: 'python: not found' })
  expect(await launch(io, 'send')).toBe('failed')
  expect(calls).not.toContain('clear')
  expect(toasts[0]).toContain('python: not found')
})

test('clear fails: nothing submitted, saved path shown', async () => {
  const { io, calls, toasts } = fake({ clearError: 'busy' })
  expect(await launch(io, 'send')).toBe('failed')
  expect(calls).not.toContain('submit')
  expect(toasts[0]).toContain('C:/h/old.md')
})

test('submit dropped: box untouched, reason and path shown', async () => {
  const { io, calls, toasts } = fake({ drop: 'policy' })
  expect(await launch(io, 'send')).toBe('dropped')
  expect(calls).not.toContain('emptyBox')
  expect(toasts[0]).toContain('policy')
  expect(toasts[0]).toContain('C:/h/old.md')
})

test('ok: order and message shape', async () => {
  const { io, calls, sent } = fake({ draft: 'продолжаем' })
  expect(await launch(io, 'send')).toBe('sent')
  expect(calls).toEqual(['draft', 'handoff', 'clear', 'submit', 'emptyBox'])
  expect(sent[0]!.startsWith('[al-handoff]')).toBe(true)
  expect(sent[0]!.endsWith('\n\nпродолжаем')).toBe(true)
})

test('submit throws: handoff and draft back in the box, nothing lost', async () => {
  const { io, calls, filled, toasts } = fake({ draft: 'x', submitError: 'offline' })
  expect(await launch(io, 'send')).toBe('failed')
  expect(calls).not.toContain('emptyBox')
  expect(filled[0]!.endsWith('\n\nx')).toBe(true)
  expect(toasts[0]).toContain('offline')
  expect(toasts[0]).toContain('C:/h/old.md')
})

test('submit throws and the box refuses the text: both reasons and the saved path in one toast', async () => {
  const { io, calls, toasts } = fake({ draft: 'x', submitError: 'offline', fillError: 'dialog is open' })
  expect(await launch(io, 'send')).toBe('failed')
  expect(calls).not.toContain('emptyBox')
  expect(toasts).toHaveLength(1)
  for (const part of ['offline', 'dialog is open', 'C:/h/old.md']) expect(toasts[0]).toContain(part)
})

test('fill mode: handoff and draft are left in the box for the owner to send, nothing submitted', async () => {
  const { io, calls, filled } = fake({ draft: 'продолжаем' })
  expect(await launch(io, 'fill')).toBe('ready')
  expect(calls).toEqual(['draft', 'handoff', 'clear', 'fill'])
  expect(filled).toEqual(['[al-handoff]\nтело\n[/al-handoff]\n\nпродолжаем'])
})

test('fill mode: the box refuses the text: the reason and the saved path in one toast', async () => {
  const { io, calls, toasts } = fake({ draft: 'x', fillError: 'dialog is open' })
  expect(await launch(io, 'fill')).toBe('failed')
  expect(calls).not.toContain('submit')
  expect(toasts).toEqual([T.fillFailed(T.notSent, 'dialog is open', 'C:/h/old.md')])
})

test('fill mode: the same early exits as send (empty draft, failed handoff, failed clear)', async () => {
  expect(await launch(fake({ draft: ' ' }).io, 'fill')).toBe('empty')
  expect(await launch(fake({ handoffError: 'x' }).io, 'fill')).toBe('failed')
  const { io, calls } = fake({ clearError: 'busy' })
  expect(await launch(io, 'fill')).toBe('failed')
  expect(calls).not.toContain('fill')
})
