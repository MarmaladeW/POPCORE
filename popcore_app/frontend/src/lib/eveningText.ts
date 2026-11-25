// 晚盘 as staff write it in WeChat (owner, 2026-11-20): one line per bestseller, `per*sets➕loose` for the whole store
// with display left out, ✅ when POPCORE holds the same and 🥲 when it does not.
export interface EveningLine { name: string; per_set: number | null; sets: number; loose: number; matches?: boolean | null }

export function eveningAmount(line: Pick<EveningLine, 'per_set' | 'sets' | 'loose'>): string {
  if (!line.per_set || !line.sets) return String(line.loose)   // no whole set counted: just the boxes
  return `${line.per_set}*${line.sets}${line.loose ? `➕${line.loose}` : ''}`
}

export function eveningText(day: string, store: string, lines: EveningLine[]): string {
  const mark = (matches?: boolean | null) => matches === true ? ' ✅' : matches === false ? ' 🥲' : ''
  return [`${day.replaceAll('-', '.')} ${store} 晚盘`, ...lines.map(line => `${line.name} ${eveningAmount(line)}${mark(line.matches)}`)].join('\n')
}
