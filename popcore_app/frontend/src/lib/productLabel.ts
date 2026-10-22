type ProductIdentity = {id:number;sku?:string|null;jizhanming?:string|null;name_cn_en?:string|null;series_name?:string|null;ip_series?:string|null;design_name?:string|null;stock_form?:string|null}

/** Shop name (记账名) and product name together, so either one is recognisable in results. */
export function productNames(product:ProductIdentity) {
  const shop = product.jizhanming?.trim()
  const name = product.name_cn_en?.trim()
  if (shop && name && shop !== name) return `${shop} · ${name}`
  return shop || name || product.sku || `Product #${product.id}`
}

export function productLabel(product:ProductIdentity|undefined) {
  if (!product) return 'Unknown product'
  const name = product.stock_form === 'confirmed_design' && product.design_name
    ? [product.series_name || product.ip_series, product.design_name].filter(Boolean).join(' · ')
    : productNames(product)
  const form = ({confirmed_design:'Confirmed',random_box:'Blind box',sealed_set:'Sealed set'} as Record<string,string>)[product.stock_form || '']
  return form ? `${name} · ${form}` : name
}
