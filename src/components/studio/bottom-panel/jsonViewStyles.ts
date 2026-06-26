import { darkStyles } from 'react-json-view-lite';

/* Version-proof JsonView styling. We keep the library's structural classes
   (indentation, icons, layout) from `darkStyles` and append our own stable
   `.rpa-jv-*` color classes (themed in json-view-theme.css). This replaces the
   old approach of overriding the library's hashed CSS-module class names, which
   broke on every release. (StyleProps isn't re-exported from the package root,
   so we derive the type from darkStyles.) */
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
