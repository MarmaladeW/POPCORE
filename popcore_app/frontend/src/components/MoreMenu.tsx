import { Link, Navigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'

import { useHasRole, useRole } from '../auth/useRole'
import { moreItems } from '../lib/navigation'
import { ROLE_LABELS } from './AppLayout'
import Icon from './pc/Icon'
import PageHeader from './pc/PageHeader'
import { useIsPhone } from './pc/useIsPhone'

// Phone 更多 page (Figma Menus · Phone · More). Desktop has everything in the rail.
export default function MoreMenu() {
  const phone = useIsPhone()
  const { user, logout } = useAuth0()
  const role = useRole()
  const access = { staff: useHasRole('staff'), manager: useHasRole('manager'), admin: useHasRole('admin') }
  if (!phone) return <Navigate to="/" replace />
  return <div className="pc-more">
    <PageHeader title="更多" sub="More" />
    <nav className="pc-more-list" aria-label="More navigation">
      {moreItems(access).map(item => <Link key={item.key} to={item.key}>
        <Icon name={item.icon} size={18} />
        <span className="pc-nav-text"><strong>{item.zh}</strong> <small>{item.en}{item.note ? ` · ${item.note}` : ''}</small></span>
        <Icon name="right" size={14} className="pc-more-chevron" />
      </Link>)}
    </nav>
    <div className="pc-more-account">
      <span className="pc-nav-text"><strong>{user?.nickname ?? user?.name ?? 'User'}</strong><small>{ROLE_LABELS[role] || role}</small></span>
      <button type="button" onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}>退出登录 Sign out</button>
    </div>
  </div>
}
