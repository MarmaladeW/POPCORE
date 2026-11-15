// Where a daily report's stock lines and notices point: a location by its code.
export const locationText: Record<string, string> = {
  floor: '楼下 Floor', upstairs: '楼上 Upstairs', warehouse: '仓库 Warehouse', display: 'Display', claw: '娃娃机 Claw',
  not_counted: '还没盘点 Not counted',
}

/** A location of this store ("upstairs"), or of the other store: "MK:warehouse", or its code with `store`. */
export function whereText(code: string, store?: string | null): string {
  const [other, place] = code.includes(':') ? code.split(':') : [store ?? '', code]
  return `${other ? `${other} ` : ''}${locationText[place] ?? place}`
}
