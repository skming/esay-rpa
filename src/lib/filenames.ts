/** 流程名直接做文件名会被系统拒绝：非法字符、控制字符、Windows 会吞掉的结尾点/空格。保留 CJK，与后端 storage.slugify 一致。 */
// eslint-disable-next-line no-control-regex -- 控制字符正是这里要剔除的目标
const ILLEGAL = /[\\/:*?"<>|\x00-\x1f]+/g;

export function toSafeFilename(name: string | null | undefined, fallback = '未命名流程'): string {
  // 收尾一并剥掉分隔符：名字全由非法字符组成时会塌成一个孤零零的 "-"，那不算有效文件名。
  const cleaned = (name ?? '').replace(ILLEGAL, '-').replace(/^[-.\s]+|[-.\s]+$/g, '');
  return cleaned === '' ? fallback : cleaned.slice(0, 80);
}
