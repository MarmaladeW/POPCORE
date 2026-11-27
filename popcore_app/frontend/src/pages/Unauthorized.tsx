import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useHasRole, type Role } from '../auth/useRole'
import './NoAccess.css'

// Who a closed page is for, in the words staff use. 'board' is 网站订单: the store computer's and the admin's.
export type ClosedFor = Role | 'board'
const WHO: Record<ClosedFor, { zh: string; en: string }> = {
  admin: { zh: '只有管理员能看这一页。', en: 'Only admins see this page.' },
  manager: { zh: '只有店长和管理员能看这一页。', en: 'Only managers and admins see this page.' },
  staff: { zh: '店铺电脑的账号打不开这一页，请用自己的账号登录。', en: 'The store computer’s account cannot open this page. Sign in as yourself.' },
  viewer: { zh: '这个账号打不开这一页。', en: 'This account cannot open this page.' },
  board: { zh: '网站订单在店铺电脑上处理。', en: 'Web orders are handled on the store computer.' },
}

/** One look for every "you cannot open this" page: what it is, why, and where to go instead. No 返回 Back: these
 * pages are mostly reached from an old link or notification in a fresh tab, where there is nothing to go back to. */
export function NoAccess({ zh, en, why, whyEn, children }: { zh: string; en: string; why: ReactNode; whyEn: ReactNode; children?: ReactNode }) {
  return <section className="pc-noaccess" aria-labelledby="pc-noaccess-title">
    <h1 id="pc-noaccess-title"><span>{zh}</span><small>{en}</small></h1>
    <p><span>{why}</span><small>{whyEn}</small></p>
    <div className="pc-noaccess-go">
      {children ?? <Link className="pc-btn pc-btn-ink" to="/">回首页 Home</Link>}
    </div>
  </section>
}

// A page this account's role does not have (owner, 2026-11-26: figure out how the page looks when an employee has
// no access). Staff read Chinese first; nothing here says what the page holds.
export default function Unauthorized({ closedFor = 'viewer' }: { closedFor?: ClosedFor }) {
  const staff = useHasRole('staff')
  const who = WHO[closedFor]
  return <NoAccess zh="这一页没有对你开放" en="This page is not open to you" why={who.zh} whyEn={who.en}>
    <Link className="pc-btn pc-btn-ink" to="/">{staff ? '回首页 Home' : '回网站订单 Web orders'}</Link>
  </NoAccess>
}
