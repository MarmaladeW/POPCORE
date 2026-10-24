import type { CSSProperties } from 'react'

// Stroke icons exported from the Figma Icons board (24×24, 1.6 stroke). Drawn as a CSS mask so
// they take the current text colour, the way Figma recolours each instance.
const files = import.meta.glob('../../assets/icons/*.svg', { eager: true, query: '?url', import: 'default' }) as Record<string, string>
const urls = Object.fromEntries(Object.entries(files).map(([path, url]) => [path.split('/').pop()!.replace('.svg', ''), url]))

export type IconName = 'home' | 'pay' | 'in' | 'gift' | 'doc' | 'cal' | 'dots' | 'box' | 'swap' | 'chart' | 'grid' | 'right'
  | 'clock' | 'camera' | 'up' | 'search' | 'back' | 'refresh' | 'plus' | 'check' | 'alert' | 'user' | 'gear' | 'up-2'
  | 'count' | 'down' | 'sort'

export default function Icon({ name, size = 18, className }: { name: IconName; size?: number; className?: string }) {
  const style = { '--pc-icon': `url("${urls[name]}")`, width: size, height: size } as CSSProperties
  return <span aria-hidden="true" className={className ? `pc-icon ${className}` : 'pc-icon'} style={style} />
}
