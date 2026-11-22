import { cn } from '@/lib/utils'
import { regularRankInfo } from '@/lib/ranks'

/** 长 副 资 正: this person is 转正, shown with their rank (owner, 2026-11-22). The glyph carries the meaning, so it
 *  never depends on colour; screen readers hear the rank with the 转正 date. */
export default function RegularMark({ since, rank, className }: {
  since?: string | null; rank?: string | null; className?: string
}) {
  const info = regularRankInfo(rank)
  const label = `${info.zh} ${info.en}${since ? ` · 转正 ${since}` : ''}`
  return <span className={cn('pc-regular-mark', `pc-regular-mark-${info.value}`, className)} title={label}>
    <span aria-hidden="true">{info.glyph}</span>
    <span className="pc-sr">{label}</span>
  </span>
}
