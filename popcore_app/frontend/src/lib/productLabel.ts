type ProductIdentity = {id:number;sku?:string|null;jizhanming?:string|null;name_cn_en?:string|null;series_name?:string|null;ip_series?:string|null;design_name?:string|null;stock_form?:string|null}

export function productLabel(product:ProductIdentity|undefined) {
  if (!product) return 'Unknown product'
  const name = product.stock_form === 'confirmed_design' && product.design_name
    ? [product.series_name || product.ip_series, product.design_name].filter(Boolean).join(' · ')
    : product.jizhanming || product.name_cn_en || product.sku || `Product #${product.id}`
  const form = ({confirmed_design:'Confirmed',random_box:'Blind box',sealed_set:'Sealed set'} as Record<string,string>)[product.stock_form || '']
  return form ? `${name} · ${form}` : name
}
