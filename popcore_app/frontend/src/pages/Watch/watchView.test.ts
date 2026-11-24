import assert from 'node:assert/strict'
import test from 'node:test'
import type { WatchAlert, WatchStatus } from '../../api/watch.ts'
import { outcomeText, splitAlerts, statusLine, torontoTime } from './watchView.ts'

const alert = (fields: Partial<WatchAlert>): WatchAlert => ({
  id: 1, kind: 'stock_loss', severity: 'normal', status: 'open', store: 'DT', business_date: '2026-11-23',
  title_zh: '坐坐派对 损耗 3盒 · 损坏', title_en: '坐坐派对: 3 box written off as broken', verdict: null,
  note_zh: null, note_en: null, created_at: '2026-11-24 15:00:00', checked_at: null, checked_by: null, ...fields,
})

const status = (fields: Partial<WatchStatus>): WatchStatus => ({
  enabled: true, claude: true, has_key: true, model: 'claude-opus-5-5', daily_cap_usd: 0.5, review_time: '07:00',
  spent_today_usd: 0.0612, review_requested: false, last_rules: null, last_review: null, last_sent: null, ...fields,
})

test('what Claude marked low is folded apart, never dropped', () => {
  const { look, quiet } = splitAlerts([alert({ id: 1 }), alert({ id: 2, verdict: 'low' }), alert({ id: 3, verdict: 'keep' })])
  assert.deepEqual(look.map(a => a.id), [1, 3])
  assert.deepEqual(quiet.map(a => a.id), [2])
})

test('saved UTC times show as Toronto time', () => {
  assert.equal(torontoTime('2026-11-24 15:00:00'), '11/24 10:00')
  assert.equal(torontoTime(null), '')
})

test('the admin status line says what the review can do', () => {
  assert.equal(statusLine(status({ enabled: false })), '监控已关闭 · The watcher is off (设置 Settings → 异常提醒)')
  assert.equal(statusLine(status({ has_key: false })), '监控已开启 · Watching · 没有密钥 no key')
  assert.equal(statusLine(status({})), '监控已开启 · Watching · claude-opus-5-5 · 每天 07:00 · 今日 $0.06 / $0.50')
})

test('run outcomes read in both languages', () => {
  assert.equal(outcomeText('cap'), '已到今日花费上限 Daily spend cap reached')
  assert.equal(outcomeText('something_new'), 'something_new something_new')
})
