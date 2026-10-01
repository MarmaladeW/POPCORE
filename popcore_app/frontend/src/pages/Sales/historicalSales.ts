type SavedPriceRow = {unit_price?:number|null;qty_sold:number}

export function savedLineValue(row:SavedPriceRow):number|null {
  return row.unit_price == null ? null : row.unit_price * row.qty_sold
}

export function savedPriceSummary(rows:SavedPriceRow[]) {
  return rows.reduce((total,row) => {
    const value = savedLineValue(row)
    if (value == null) total.unknownRows += 1
    else { total.knownValue += value; total.knownRows += 1 }
    return total
  }, {knownValue:0,unknownRows:0,knownRows:0})
}
