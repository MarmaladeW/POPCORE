// Staff ranks (owner, 2026-11-19), as popcore_app/ranks.py keeps them. 店长 and 副店长 have manager rights, the
// others staff rights. 管理员 Admin and 查看者 Viewer have no rank: a viewer (the store computer's account) is never a
// 培训员工. Every list of people shows them in this order, 店长 first.
export type Rank = 'store_manager' | 'assistant_manager' | 'senior' | 'regular' | 'trainee'

export const RANKS: ReadonlyArray<{ value: Rank; zh: string; en: string; glyph: string; role: 'manager' | 'staff' }> = [
  { value: 'store_manager', zh: '店长', en: 'Store manager', glyph: '长', role: 'manager' },
  { value: 'assistant_manager', zh: '副店长', en: 'Assistant manager', glyph: '副', role: 'manager' },
  { value: 'senior', zh: '资深员工', en: 'Senior staff', glyph: '资', role: 'staff' },
  { value: 'regular', zh: '正式员工', en: 'Regular staff', glyph: '正', role: 'staff' },
  { value: 'trainee', zh: '培训员工', en: 'Trainee', glyph: '训', role: 'staff' },
]

export const rankInfo = (rank: string | null | undefined) => RANKS.find(entry => entry.value === rank)

/** What the schedule marks someone 转正 with (owner, 2026-11-22): their rank, 长 副 资 or 正. Someone 转正 without one
 *  of those ranks (an older account) keeps 正. 培训员工 is never 转正, so it shows 训 instead. */
export function regularRankInfo(rank: string | null | undefined) {
  const info = rankInfo(rank)
  return info && info.value !== 'trainee' ? info : RANKS[3]
}

/** Sort key: 店长 first, people without a rank after everyone ranked. */
export const rankIndex = (rank: string | null | undefined) => {
  const index = RANKS.findIndex(entry => entry.value === rank)
  return index < 0 ? RANKS.length : index
}

// What the Users page's picker sets: a rank, or one of the two roles without one.
export const POSITION_OPTIONS = [
  { value: 'admin', label: '管理员 Admin' },
  ...RANKS.map(entry => ({ value: entry.value as string, label: `${entry.zh} ${entry.en}` })),
  { value: 'viewer', label: '查看者 Viewer' },
]

/** The picker's value for an account: its rank, or admin / viewer. Staff or a manager without a rank yet has none. */
export function positionOf(user: { role: string; rank?: string | null }): string | undefined {
  if (user.role === 'admin' || user.role === 'viewer') return user.role
  return rankInfo(user.rank)?.value
}

/** An account's position in words, short. */
export function positionLabel(user: { role: string; rank?: string | null }): string {
  if (user.role === 'admin') return '管理员'
  if (user.role === 'viewer') return '查看者'
  return rankInfo(user.rank)?.zh ?? (user.role === 'manager' ? '经理' : '店员')
}

/** What saving a picked position sends to PATCH /api/users/<id>. */
export const positionBody = (value: string) =>
  value === 'admin' || value === 'viewer' ? { role: value } : { rank: value }

/** The account after saving a picked position. */
export function withPosition<T extends { role: string; rank?: string | null }>(user: T, value: string): T {
  const rank = rankInfo(value)
  return { ...user, role: rank ? rank.role : value, rank: rank ? rank.value : null }
}
