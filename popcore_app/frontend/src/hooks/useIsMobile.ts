import { Grid } from 'antd'

const { useBreakpoint } = Grid

/** Returns true when viewport width is < 768px (Ant Design's `md` breakpoint) */
export function useIsMobile(): boolean {
  const screens = useBreakpoint()
  return !screens.md
}
