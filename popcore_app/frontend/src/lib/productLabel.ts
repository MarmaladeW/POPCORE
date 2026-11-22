type ProductIdentity = {id:number;sku?:string|null;jizhanming?:string|null;name_cn_en?:string|null;series_name?:string|null;ip_series?:string|null;design_name?:string|null;stock_form?:string|null}

/** Shop name (记账名) and product name together, so either one is recognisable in results. */
export function productNames(product:ProductIdentity) {
  const shop = product.jizhanming?.trim()
  const name = product.name_cn_en?.trim()
  if (shop && name && shop !== name) return `${shop} · ${name}`
  return shop || name || product.sku || `Product #${product.id}`
}

/** A pick's parts, the most telling first (owner, 2026-11-22: a box cut "Sonny Angel Easter Series 20…" short, so
 * Easter Lily and Easter Bunny looked the same and the wrong one was picked): the design's own name, the SKU, then
 * the shop and product names. */
export function pickParts(product:ProductIdentity) {
  return {design:product.design_name?.trim() || '', sku:product.sku?.trim() || '', names:productNames(product)}
}

/** One line for a pick box: what gets cut off at its end is the long series name, never the design or the SKU. */
export function pickText(product:ProductIdentity) {
  const {design, sku, names} = pickParts(product)
  return [design, sku, names].filter((part, index, parts) => part && parts.indexOf(part) === index).join(' · ')
}

export function productLabel(product:ProductIdentity|undefined) {
  if (!product) return 'Unknown product'
  const name = product.stock_form === 'confirmed_design' && product.design_name
    ? [product.series_name || product.ip_series, product.design_name].filter(Boolean).join(' · ')
    : productNames(product)
  const form = ({confirmed_design:'Confirmed',random_box:'Blind box',sealed_set:'Sealed set'} as Record<string,string>)[product.stock_form || '']
  return form ? `${name} · ${form}` : name
}
