import type { ThemeConfig } from 'antd'

export const operationsTheme: ThemeConfig = {
  token: {
    colorPrimary: '#4F46E5', colorInfo: '#4F46E5', colorSuccess: '#15803D',
    colorWarning: '#B45309', colorError: '#DC2626', colorText: '#20242D',
    colorTextSecondary: '#596273', colorBorder: '#DDE2EA', colorBgLayout: '#F7F8FA',
    colorBgContainer: '#FFFFFF', borderRadius: 8,
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    controlHeight: 40, zIndexPopupBase: 1010,
  },
  components: {
    Button: { borderRadius: 8, controlHeight: 40 },
    Card: { paddingLG: 16 },
    Table: { headerBg: '#F7F8FA', headerColor: '#384152', rowHoverBg: '#F5F5FF', borderColor: '#DDE2EA' },
    Tag: { borderRadiusSM: 6 },
  },
}
