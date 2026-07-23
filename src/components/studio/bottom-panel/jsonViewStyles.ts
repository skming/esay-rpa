import { darkStyles } from 'react-json-view-lite';

// 保留 darkStyles 的结构类名，只追加自定义 .rpa-jv-* 颜色类；直接覆盖库的 hashed 类名在每次发版都会失效
// StyleProps 未从包根导出，类型借 darkStyles 推导
export const rpaJsonViewStyles: typeof darkStyles = {
  ...darkStyles,
  container: `${darkStyles.container} rpa-jv-container`,
  basicChildStyle: `${darkStyles.basicChildStyle} rpa-jv-row`,
  label: `${darkStyles.label} rpa-jv-key`,
  clickableLabel: `${darkStyles.clickableLabel} rpa-jv-key`,
  nullValue: `${darkStyles.nullValue} rpa-jv-null`,
  undefinedValue: `${darkStyles.undefinedValue} rpa-jv-null`,
  numberValue: `${darkStyles.numberValue} rpa-jv-num`,
  stringValue: `${darkStyles.stringValue} rpa-jv-str`,
  booleanValue: `${darkStyles.booleanValue} rpa-jv-bool`,
  otherValue: `${darkStyles.otherValue} rpa-jv-other`,
  punctuation: `${darkStyles.punctuation} rpa-jv-punc`,
  collapseIcon: `${darkStyles.collapseIcon} rpa-jv-icon`,
  expandIcon: `${darkStyles.expandIcon} rpa-jv-icon`,
  collapsedContent: `${darkStyles.collapsedContent} rpa-jv-collapsed`,
};
