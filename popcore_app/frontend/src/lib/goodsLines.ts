import type { NativeUnit, ReceiptLine } from '../api/goods'

// Lines of a receipt or transfer being built: picking or scanning a product that is already listed adds to its line.

const nextLineNo=(lines:{line_no:number}[])=>Math.max(0,...lines.map(line=>line.line_no))+1

export function addReceiptLine(lines:ReceiptLine[],productId:number,unit:NativeUnit,quantity:number):ReceiptLine[]{
  const same=(line:ReceiptLine)=>line.product_id===productId&&line.native_unit===unit
  if(lines.some(same))return lines.map(line=>same(line)?{...line,saleable_quantity:line.saleable_quantity+quantity}:line)
  return [...lines,{line_no:nextLineNo(lines),product_id:productId,native_unit:unit,expected_quantity:null,saleable_quantity:quantity,damaged_quantity:0,hold_quantity:0,discrepancy_note:null}]
}

export const receiptLineTotal=(line:ReceiptLine)=>line.saleable_quantity+line.damaged_quantity+line.hold_quantity

export const receiptLineBody=(line:ReceiptLine)=>({product_id:line.product_id,unit:line.native_unit,expected_quantity:line.expected_quantity,saleable_quantity:line.saleable_quantity,damaged_quantity:line.damaged_quantity,hold_quantity:line.hold_quantity,discrepancy_note:line.discrepancy_note})

export interface PlannedTransferLine {product_id:number;unit:NativeUnit;requested_quantity:number}

export function addTransferLine(lines:PlannedTransferLine[],productId:number,unit:NativeUnit,quantity:number):PlannedTransferLine[]{
  const same=(line:PlannedTransferLine)=>line.product_id===productId&&line.unit===unit
  if(lines.some(same))return lines.map(line=>same(line)?{...line,requested_quantity:line.requested_quantity+quantity}:line)
  return [...lines,{product_id:productId,unit,requested_quantity:quantity}]
}

/** Why a product cannot go on a receipt or transfer, or '' when it can. */
export function unreceivable(product:{identity_status?:string|null;stock_unit?:string|null}){
  if(product.identity_status==='unverified')return '未核实 identity not verified'
  if(!product.stock_unit)return '无库存单位 no stock unit'
  return ''
}

const unitCount=(quantity:number,unit:NativeUnit)=>`${quantity} ${unit}${quantity===1?'':unit==='box'?'es':'s'}`

/** "+4 pieces · +1 set": units differ by product, so each unit gets its own total. */
export function unitTotals(entries:Array<{unit:NativeUnit;quantity:number}>,sign='+'){
  const totals=new Map<NativeUnit,number>()
  for(const entry of entries)totals.set(entry.unit,(totals.get(entry.unit)||0)+entry.quantity)
  return [...totals].filter(([,quantity])=>quantity>0).map(([unit,quantity])=>`${sign}${unitCount(quantity,unit)}`).join(' · ')
}
