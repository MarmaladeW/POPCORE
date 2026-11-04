import { cn } from '@/lib/utils'

/** 正: this person is 转正. The glyph carries the meaning, so it never depends on colour;
 *  screen readers hear 转正 with the date. */
export default function RegularMark({ since, className }: { since?: string | null; className?: string }) {
  const label = since ? `转正 Regular since ${since}` : '转正 Regular'
  return <span className={cn('pc-regular-mark', className)} title={label}>
    <span aria-hidden="true">正</span>
    <span className="pc-sr">{label}</span>
  </span>
}
