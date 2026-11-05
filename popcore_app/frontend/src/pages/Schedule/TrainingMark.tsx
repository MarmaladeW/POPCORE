import { cn } from '@/lib/utils'

/** 训: this person is 培训中, in training. Same square as 正 in amber; the glyph carries the meaning. */
export default function TrainingMark({ className }: { className?: string }) {
  return <span className={cn('pc-training-mark', className)} title="培训中 In training">
    <span aria-hidden="true">训</span>
    <span className="pc-sr">培训中 In training</span>
  </span>
}
