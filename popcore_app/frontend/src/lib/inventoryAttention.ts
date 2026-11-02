type Balance = {location_id:number;disposition:string;quantity:number|null}
type Product = {identity_status:string;stock_form?:string|null;balances:Balance[]}
type Location = {id:number;store_id:number;code:string}
export type InventoryAttention = 'unverified'|'out_of_stock'|'replenish'|'condition'

// Sealed sets stay upstairs and confirmed designs live upstairs or on display, so neither is refilled on the floor.
const NOT_ON_FLOOR = ['sealed_set','confirmed_design']
const BACK = ['upstairs','warehouse']
const saleable = (product:Product|undefined, id:number) => product?.balances.find(balance=>balance.location_id===id&&balance.disposition==='saleable')?.quantity

/** `sealedSet` is a blind box's sealed set: its whole sets upstairs refill the box's floor too. */
export function inventoryAttention(product:Product, locations:Location[], sealedSet?:Product):InventoryAttention[] {
  const states:InventoryAttention[]=[]
  const available=(id:number)=>saleable(product,id)
  const known=product.identity_status==='verified'&&locations.length>0&&locations.every(location=>available(location.id)!=null)
  if(!known)states.push('unverified')
  else if(locations.every(location=>available(location.id)===0))states.push('out_of_stock')
  const back=(id:number)=>(available(id)??0)+(saleable(sealedSet,id)??0)
  if(product.identity_status==='verified'&&!NOT_ON_FLOOR.includes(product.stock_form??'')&&locations.some(floor=>floor.code==='floor'&&available(floor.id)===0&&locations.some(location=>location.store_id===floor.store_id&&BACK.includes(location.code)&&back(location.id)>0)))states.push('replenish')
  if(product.balances.some(balance=>['hold','damaged'].includes(balance.disposition)&&(balance.quantity??0)>0))states.push('condition')
  return states
}
