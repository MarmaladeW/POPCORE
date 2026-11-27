type Balance = {location_id:number;disposition:string;quantity:number|null}
type Product = {identity_status:string;stock_form?:string|null;balances:Balance[];stocked_store_ids?:number[];floor_store_ids?:number[]}
type Location = {id:number;store_id:number;code:string}
export type InventoryAttention = 'unverified'|'out_of_stock'|'replenish'|'condition'

// Sealed sets stay upstairs and confirmed designs live upstairs or on display, so neither is refilled on the floor.
const NOT_ON_FLOOR = ['sealed_set','confirmed_design']
const BACK = ['upstairs','warehouse']
const saleable = (product:Product|undefined, id:number) => product?.balances.find(balance=>balance.location_id===id&&balance.disposition==='saleable')?.quantity

/**
 * `sealedSet` is a blind box's sealed set: its whole sets count as the box's stock, upstairs too.
 * Only what a store has held since going live can run out there, and only a floor that has held a product can be
 * empty of it (owner, 2026-11-11): a zero for something the store never carried, or for back stock the floor never
 * held (big figures on display, old series upstairs), is not a problem.
 */
export function inventoryAttention(product:Product, locations:Location[], sealedSet?:Product):InventoryAttention[] {
  const states:InventoryAttention[]=[]
  const available=(id:number)=>saleable(product,id)
  const withSets=(id:number)=>(available(id)??0)+(saleable(sealedSet,id)??0)
  const known=product.identity_status==='verified'&&locations.length>0&&locations.every(location=>available(location.id)!=null)
  const stocked=[product,sealedSet].some(item=>item?.stocked_store_ids?.length)
  if(!known)states.push('unverified')
  else if(product.stock_form!=='sealed_set'&&stocked&&locations.every(location=>withSets(location.id)===0))states.push('out_of_stock')
  if(product.identity_status==='verified'&&!NOT_ON_FLOOR.includes(product.stock_form??'')&&locations.some(floor=>floor.code==='floor'&&available(floor.id)===0
    &&product.floor_store_ids?.includes(floor.store_id)&&locations.some(location=>location.store_id===floor.store_id&&BACK.includes(location.code)&&withSets(location.id)>0)))states.push('replenish')
  if(product.balances.some(balance=>['hold','damaged'].includes(balance.disposition)&&(balance.quantity??0)>0))states.push('condition')
  return states
}

/** Needs attention: what staff can act on. A product never reviewed is catalog setup, still listed under Unverified stock. */
export const needsAttention = (product:Product, states:InventoryAttention[]) =>
  states.some(state=>state!=='unverified'||product.identity_status==='verified')
