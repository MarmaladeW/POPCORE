import { rankInfo } from '../../lib/ranks'

/** 长 副 资 正 训: a person's rank (owner, 2026-11-19). The glyph carries the meaning; screen readers hear the rank. */
export default function RankBadge({ rank, className }: { rank: string | null | undefined; className?: string }) {
  const info = rankInfo(rank)
  if (!info) return null
  return <span className={`pc-rank pc-rank-${info.value}${className ? ` ${className}` : ''}`} title={`${info.zh} ${info.en}`}>
    <span aria-hidden="true">{info.glyph}</span>
    <span className="pc-sr">{info.zh} {info.en}</span>
  </span>
}
