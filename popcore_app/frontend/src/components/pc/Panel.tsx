import type { ReactNode } from 'react'
import './pc.css'

// Figma "panel": white box, header with Chinese title, English sub and an optional count dot.
export function Panel({ zh, en, count, tone = 'warn', action, label, children, className }: {
  zh: ReactNode; en?: ReactNode; count?: ReactNode; tone?: 'warn' | 'ok' | 'out'; action?: ReactNode
  label?: string; children: ReactNode; className?: string
}) {
  return <section className={className ? `pc-panel ${className}` : 'pc-panel'} aria-label={label}>
    <header className="pc-panel-head">
      <h2><span>{zh}</span>{en && <> <small>{en}</small></>}</h2>
      {count !== undefined && <span className="pc-panel-count"><i className={`pc-dot pc-dot-${tone}`} aria-hidden="true" />{count}</span>}
      {action}
    </header>
    {children}
  </section>
}

// Figma "List row": fixed-width label, two-line text, trailing time / button / label.
export function ListRow({ label, title, meta, trailing }: { label?: ReactNode; title: ReactNode; meta?: ReactNode; trailing?: ReactNode }) {
  return <div className="pc-row">
    {label !== undefined && <span className="pc-row-label">{label}</span>}
    <span className="pc-row-main"><strong>{title}</strong>{meta && <small>{meta}</small>}</span>
    {trailing}
  </div>
}

// Section heading outside a box (phone lists): bold Chinese, English beside it, link on the right.
export function SectionHeading({ zh, en, action }: { zh: ReactNode; en?: ReactNode; action?: ReactNode }) {
  return <div className="pc-section-head"><h2><span>{zh}</span>{en && <> <small>{en}</small></>}</h2>{action}</div>
}

export function Tag({ children }: { children: ReactNode }) {
  return <span className="pc-tag">{children}</span>
}
