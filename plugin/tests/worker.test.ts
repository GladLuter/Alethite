import { expect, test } from 'claude-code/testing'
import { launch } from '../hooks/worker'
import type { LaunchIO } from '../hooks/worker'

// A LaunchIO that only writes down what was called; `o` makes one step fail or refuse.
const fake = (o: { draft?: string; handoffError?: string; clearError?: string; modelError?: string; submitError?: string; fillError?: string; drop?: string } = {}) => {
  const calls: string[] = []
  const toasts: string[] = []
  const sent: string[] = []
  const models: string[] = []
  const filled: string[] = []
  const io: LaunchIO = {
    draft: async () => (calls.push('draft'), o.draft ?? 'черновик'),
    handoff: async () => {
      calls.push('handoff')
      if (o.handoffError) throw new Error(o.handoffError)
      return { text: '[cg-handoff]\nтело\n[/cg-handoff]', path: 'C:/h/old.md' }
    },
    clear: async () => {
      calls.push('clear')
      if (o.clearError) throw new Error(o.clearError)
    },
    setModel: async model => {
      calls.push('setModel')
      if (o.modelError) throw new Error(o.modelError)
      models.push(model)
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
  return { io, calls, toasts, sent, models, filled }
}

test('empty draft: nothing else runs', async () => {
  const { io, calls } = fake({ draft: '  ' })
  expect(await launch(io, null)).toBe('empty')
  expect(calls).toEqual(['draft', 'toast'])
})

test('handoff fails: chat is not cleared, reason shown', async () => {
  const { io, calls, toasts } = fake({ handoffError: 'python: not found' })
  expect(await launch(io, null)).toBe('failed')
  expect(calls).not.toContain('clear')
  expect(toasts[0]).toContain('python: not found')
})

test('clear fails: nothing submitted, saved path shown', async () => {
  const { io, calls, toasts } = fake({ clearError: 'busy' })
  expect(await launch(io, null)).toBe('failed')
  expect(calls).not.toContain('submit')
  expect(toasts[0]).toContain('C:/h/old.md')
})

test('submit dropped: box untouched, reason and path shown', async () => {
  const { io, calls, toasts } = fake({ drop: 'policy' })
  expect(await launch(io, null)).toBe('dropped')
  expect(calls).not.toContain('emptyBox')
  expect(toasts[0]).toContain('policy')
  expect(toasts[0]).toContain('C:/h/old.md')
})

test('ok: order and message shape', async () => {
  const { io, calls, sent } = fake({ draft: 'продолжаем' })
  expect(await launch(io, null)).toBe('sent')
  expect(calls).toEqual(['draft', 'handoff', 'clear', 'submit', 'emptyBox'])
  expect(sent[0]!.startsWith('[cg-handoff]')).toBe(true)
  expect(sent[0]!.endsWith('\n\nпродолжаем')).toBe(true)
})

test('model: switched after clear, before submit', async () => {
  const { io, calls, models } = fake({ draft: 'x' })
  expect(await launch(io, 'sonnet')).toBe('sent')
  expect(calls).toEqual(['draft', 'handoff', 'clear', 'setModel', 'submit', 'emptyBox'])
  expect(models).toEqual(['sonnet'])
})

test('model switch fails: nothing submitted, handoff and draft back in the box', async () => {
  const { io, calls, filled, toasts } = fake({ draft: 'x', modelError: 'unknown model' })
  expect(await launch(io, 'sonnet')).toBe('failed')
  expect(calls).not.toContain('submit')
  expect(filled[0]!.startsWith('[cg-handoff]')).toBe(true)
  expect(filled[0]!.endsWith('\n\nx')).toBe(true)
  expect(toasts[0]).toContain('unknown model')
  expect(toasts[0]).toContain('C:/h/old.md')
})

test('submit throws: handoff and draft back in the box, nothing lost', async () => {
  const { io, calls, filled, toasts } = fake({ draft: 'x', submitError: 'offline' })
  expect(await launch(io, null)).toBe('failed')
  expect(calls).not.toContain('emptyBox')
  expect(filled[0]!.endsWith('\n\nx')).toBe(true)
  expect(toasts[0]).toContain('offline')
  expect(toasts[0]).toContain('C:/h/old.md')
})

test('model switch fails and the box refuses the text: both reasons and the saved path in one toast', async () => {
  const { io, calls, toasts } = fake({ draft: 'x', modelError: 'unknown model', fillError: 'dialog is open' })
  expect(await launch(io, 'sonnet')).toBe('failed')
  expect(calls).not.toContain('submit')
  expect(toasts).toHaveLength(1)
  for (const part of ['unknown model', 'dialog is open', 'C:/h/old.md']) expect(toasts[0]).toContain(part)
})

test('submit throws and the box refuses the text: both reasons and the saved path in one toast', async () => {
  const { io, calls, toasts } = fake({ draft: 'x', submitError: 'offline', fillError: 'dialog is open' })
  expect(await launch(io, null)).toBe('failed')
  expect(calls).not.toContain('emptyBox')
  expect(toasts).toHaveLength(1)
  for (const part of ['offline', 'dialog is open', 'C:/h/old.md']) expect(toasts[0]).toContain(part)
})
