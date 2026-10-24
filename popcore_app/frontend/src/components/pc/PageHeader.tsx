import { createContext, useContext, useLayoutEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import Icon from './Icon'
import StoreChip from './StoreChip'

// A page declares its header once; the shell draws it as the phone Top bar or the desktop header.
export type PageHeaderSpec = {
  title: ReactNode
  sub?: ReactNode
  back?: { label: ReactNode; to: string }
  actions?: ReactNode
  phoneAction?: ReactNode
  roomy?: boolean
}

type Slots = { phone: HTMLElement | null; desktop: HTMLElement | null; mobile: boolean; register: () => () => void }
export const PageHeaderSlots = createContext<Slots | null>(null)

export function PhoneTopBar({ title, sub, back, right, roomy }: { title: ReactNode; sub?: ReactNode; back?: PageHeaderSpec['back']; right?: ReactNode; roomy?: boolean }) {
  return <header className={roomy ? 'pc-topbar pc-topbar-roomy' : 'pc-topbar'}>
    <div className="pc-topbar-title">
      {back && <Link className="pc-topbar-back" to={back.to}><Icon name="back" size={14} />{back.label}</Link>}
      <h1>{title}</h1>
      {sub && !back && <p>{sub}</p>}
    </div>
    {right ?? <StoreChip />}
  </header>
}

export function DesktopHeader({ title, sub, actions }: Pick<PageHeaderSpec, 'title' | 'sub' | 'actions'>) {
  return <header className="pc-desk-header">
    <div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>
    {actions && <div className="pc-desk-header-actions">{actions}</div>}
  </header>
}

export default function PageHeader(spec: PageHeaderSpec) {
  const slots = useContext(PageHeaderSlots)
  // Layout effect: the shell drops its fallback top bar before paint, so two never show at once.
  useLayoutEffect(() => slots?.register(), [slots])
  if (!slots) return null
  if (slots.mobile) {
    return slots.phone ? createPortal(<PhoneTopBar title={spec.title} sub={spec.sub} back={spec.back} right={spec.phoneAction} roomy={spec.roomy} />, slots.phone) : null
  }
  return slots.desktop ? createPortal(<DesktopHeader title={spec.title} sub={spec.sub} actions={spec.actions} />, slots.desktop) : null
}
