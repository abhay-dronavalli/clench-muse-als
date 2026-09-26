import { describe, expect, it } from 'vitest'
import { cameraProblem } from './camera'

describe('cameraProblem', () => {
  it.each([
    ['NotAllowedError', 'denied'],
    ['SecurityError', 'denied'],
    ['NotFoundError', 'none'],
    ['OverconstrainedError', 'none'],
    ['NotReadableError', 'busy'],
    ['AbortError', 'busy'],
    ['TypeError', 'error'],
  ] as const)('%s -> %s', (name, problem) => {
    expect(cameraProblem(new DOMException('x', name))).toBe(problem)
  })

  it('handles things that are not errors', () => {
    expect(cameraProblem('boom')).toBe('error')
    expect(cameraProblem(undefined)).toBe('error')
  })
})
