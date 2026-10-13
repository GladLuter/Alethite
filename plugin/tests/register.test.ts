import { expect, mock, test } from 'claude-code/testing'

const usage = { context: { tokens: 512_000, window: 1_000_000 }, rateLimits: [{ kind: 'five_hour', percentUsed: 7 }] }
const turn = { answer: '', durationMs: 1, isAborted: false, turnId: 't', reason: 'answer', usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: 'claude-opus-5-5' } }
const props = { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80 }

// stands for the engine beneath the plugin; `statuses` collects what the status line was set to
const wire = (on: any, statuses: (string | undefined)[], answerUsage: () => unknown) => {
  mock.clock(on, { now: 10_000_000_000 })
  mock.store(on, {})
  on('session.id', () => ({ value: 'id' }))
  on('session.model', () => ({ value: 'claude-opus-5-5' }))
  on('session.usage', answerUsage)
  on('ui.status', (_: unknown, e: { text?: string }) => (statuses.push(e.text), { value: undefined }))
  on('ui.render', () => ({ type: 'Text', children: ['no band'] }))
  on('turn.complete', () => ({ text: '' }))
}
const buttons = async (ui: any) => (await ui.findAll({ type: 'Button' })).map((b: { key: string }) => b.key)

test('a failed refresh shows its error in the status line', async ($, on) => {
  const statuses: (string | undefined)[] = []
  wire(on, statuses, () => ({ deny: 'boom' }))
  await ($ as any).command.run({ command: 'cg-band', args: '' })
  expect(statuses.some(t => t?.startsWith('CacheGuard: ошибка — '))).toBe(true)
})

test('"Остаться здесь" and an armed picker last one turn', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  await ($ as any).command.run({ command: 'cg-band', args: '' })
  const ui = await $.ui.mount({ plugin: 'cacheguard', surface: 'terminal', component: 'AbovePrompt', props } as never)

  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toContain('cancel')
  await ($ as any).turn.complete(turn)
  expect(await buttons(ui)).toEqual(['launch', 'stay'])

  await ui.press({ key: 'stay' })
  expect(await buttons(ui)).toEqual([])
  await ($ as any).turn.complete(turn)
  expect(await buttons(ui)).toEqual(['launch', 'stay'])
})
