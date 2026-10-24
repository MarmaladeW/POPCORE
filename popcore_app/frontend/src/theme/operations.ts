import type { ThemeConfig } from 'antd'

// Mirrors styles/tokens.css (Figma variables). Sharp corners, 1px hairlines, ink primary.
const font = '"Geist", "Noto Sans SC Variable", "Noto Sans SC", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif'

export const operationsTheme: ThemeConfig = {
  token: {
    colorPrimary: '#1f2328', colorInfo: '#1f2328', colorLink: '#1f2328', colorLinkHover: '#5f6670',
    colorSuccess: '#2f6b4c', colorWarning: '#946200', colorError: '#b4322a',
    colorText: '#1f2328', colorTextSecondary: '#5f6670', colorTextTertiary: '#9aa1ab', colorTextQuaternary: '#b9bdc4',
    colorTextDisabled: '#b9bdc4', colorTextPlaceholder: '#9aa1ab',
    colorBorder: '#eceef1', colorBorderSecondary: '#eceef1', colorSplit: '#eceef1',
    colorBgLayout: '#f7f7f8', colorBgContainer: '#ffffff', colorFillAlter: '#fafafb', colorFillTertiary: '#f2f3f5',
    colorErrorBg: '#fbf1f0', colorErrorBorder: '#efc9c5', colorWarningBg: '#fbf6ea', colorWarningBorder: '#ecdcb2',
    colorSuccessBg: '#f2f7f4', colorSuccessBorder: '#cfe3d7', colorInfoBg: '#f2f3f5', colorInfoBorder: '#e4e7ec',
    borderRadius: 0, borderRadiusLG: 0, borderRadiusSM: 0, borderRadiusXS: 0, borderRadiusOuter: 0,
    fontFamily: font, fontSize: 13, controlHeight: 36, controlHeightLG: 44, controlHeightSM: 32,
    boxShadow: 'none', boxShadowSecondary: '0 8px 24px rgba(31,35,40,0.10)',
    controlOutline: 'transparent', controlOutlineWidth: 0, lineWidthFocus: 1,
    zIndexPopupBase: 1010,
  },
  components: {
    Button: {
      fontWeight: 500, contentFontSize: 12.5, contentFontSizeLG: 13, paddingInline: 12, paddingInlineLG: 14,
      defaultBorderColor: '#eceef1', defaultColor: '#1f2328', defaultHoverBorderColor: '#c7ccd4', defaultHoverColor: '#1f2328',
      defaultActiveBorderColor: '#1f2328', defaultActiveColor: '#1f2328', primaryShadow: 'none', defaultShadow: 'none', dangerShadow: 'none',
    },
    Input: { activeBorderColor: '#1f2328', hoverBorderColor: '#c7ccd4', activeShadow: 'none', paddingInline: 10 },
    InputNumber: { activeBorderColor: '#1f2328', hoverBorderColor: '#c7ccd4', activeShadow: 'none', paddingInline: 10 },
    Select: { activeBorderColor: '#1f2328', hoverBorderColor: '#c7ccd4', activeOutlineColor: 'transparent', optionSelectedBg: '#f2f3f5', optionSelectedFontWeight: 500 },
    DatePicker: { activeBorderColor: '#1f2328', hoverBorderColor: '#c7ccd4', activeShadow: 'none' },
    Card: { paddingLG: 16, headerFontSize: 13 },
    Table: { headerBg: '#fafafb', headerColor: '#5f6670', headerSplitColor: 'transparent', rowHoverBg: '#fafafb', borderColor: '#eceef1', cellFontSize: 12.5 },
    Tabs: { inkBarColor: '#1f2328', itemColor: '#5f6670', itemSelectedColor: '#1f2328', itemHoverColor: '#1f2328', itemActiveColor: '#1f2328' },
    Segmented: { itemSelectedBg: '#1f2328', itemSelectedColor: '#ffffff', trackBg: '#ffffff', trackPadding: 0 },
    Tag: { defaultBg: '#ffffff', defaultColor: '#5f6670' },
    Alert: { withDescriptionPadding: '12px 14px' },
    Modal: { titleFontSize: 15 },
    Drawer: { footerPaddingBlock: 12 },
    Checkbox: { colorPrimary: '#1f2328' },
    Switch: { colorPrimary: '#1f2328' },
  },
}
