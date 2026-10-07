type Product = {identity_status:string;balances:Array<{location_id:number;disposition:string;quantity:number|null}>}
type Location = {id:number;store_id:number;code:string}
export type InventoryAttention = 'unverified'|'out_of_stock'|'replenish'|'condition'

export function inventoryAttention(product:Product, locations:Location[]):InventoryAttention[] {
  const states:InventoryAttention[]=[]
  const available=(id:number)=>product.balances.find(balance=>balance.location_id===id&&balance.disposition==='saleable')?.quantity
  const known=product.identity_status==='verified'&&locations.length>0&&locations.every(location=>available(location.id)!=null)
  if(!known)states.push('unverified')
  else if(locations.every(location=>available(location.id)===0))states.push('out_of_stock')
  if(product.identity_status==='verified'&&locations.some(floor=>floor.code==='floor'&&available(floor.id)===0&&locations.some(back=>back.store_id===floor.store_id&&back.code!=='floor'&&(available(back.id)??0)>0)))states.push('replenish')
  if(product.balances.some(balance=>['hold','damaged'].includes(balance.disposition)&&(balance.quantity??0)>0))states.push('condition')
  return states
}
