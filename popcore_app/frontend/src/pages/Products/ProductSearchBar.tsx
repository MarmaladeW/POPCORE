import { useState, useEffect } from 'react'
import { Search, X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { useIsMobile } from '../../hooks/useIsMobile'

function useDebounce<T>(value: T, delay: number): T {
  const [dv, setDv] = useState<T>(value)
  useEffect(() => {
    const h = setTimeout(() => setDv(value), delay)
    return () => clearTimeout(h)
  }, [value, delay])
  return dv
}

interface Props {
  series: string[]
  productTypes: string[]
  onChange: (q: string, series: string, type: string) => void
}

export default function ProductSearchBar({ series, productTypes, onChange }: Props) {
  const [inputQ,       setInputQ]       = useState('')
  const debouncedQ                      = useDebounce(inputQ, 300)
  const [filterSeries, setFilterSeries] = useState('')
  const [filterType,   setFilterType]   = useState('')
  const isMobile = useIsMobile()

  useEffect(() => {
    onChange(debouncedQ, filterSeries, filterType)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ, filterSeries, filterType])

  return (
    <>
      {/* Search input with prefix icon + clear button */}
      <div className="relative" style={{ width: isMobile ? '100%' : 340 }}>
        <Search
          className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none"
        />
        <Input
          className="pl-9 pr-8"
          placeholder="记账名、产品名或 SKU"
          aria-label="Search name, SKU, 记账名"
          value={inputQ}
          onChange={e => setInputQ(e.target.value)}
        />
        {inputQ && (
          <button
            onClick={() => setInputQ('')}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Series filter */}
      <Select
        value={filterSeries || '__all__'}
        onValueChange={v => setFilterSeries(v === '__all__' ? '' : v)}
      >
        <SelectTrigger aria-label="Series filter" style={{ width: isMobile ? 'calc(50% - 4px)' : 160 }}>
          <SelectValue placeholder="全部系列" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">全部系列</SelectItem>
          {series.map(s => (
            <SelectItem key={s} value={s}>{s}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Type filter */}
      <Select
        value={filterType || '__all__'}
        onValueChange={v => setFilterType(v === '__all__' ? '' : v)}
      >
        <SelectTrigger aria-label="Type filter" style={{ width: isMobile ? 'calc(50% - 4px)' : 140 }}>
          <SelectValue placeholder="所有类型" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">所有类型</SelectItem>
          {productTypes.map(t => (
            <SelectItem key={t} value={t}>{t}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </>
  )
}
