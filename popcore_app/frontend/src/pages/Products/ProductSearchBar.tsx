import { useState, useEffect } from 'react'
import { Input, Select, Grid } from 'antd'
import { SearchOutlined } from '@ant-design/icons'

const { useBreakpoint } = Grid

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
  const [inputQ,      setInputQ]      = useState('')
  const debouncedQ                    = useDebounce(inputQ, 300)
  const [filterSeries, setFilterSeries] = useState('')
  const [filterType,   setFilterType]   = useState('')
  const screens  = useBreakpoint()
  const isMobile = !screens.md

  useEffect(() => {
    onChange(debouncedQ, filterSeries, filterType)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ, filterSeries, filterType])

  return (
    <>
      <Input
        prefix={<SearchOutlined style={{ color: '#9ca3af' }} />}
        placeholder="Search name, SKU, 记账名..."
        allowClear
        value={inputQ}
        onChange={e => setInputQ(e.target.value)}
        style={{ width: isMobile ? '100%' : 260 }}
      />
      <Select
        placeholder="All Series"
        allowClear
        value={filterSeries || undefined}
        style={{ width: isMobile ? '100%' : 140 }}
        options={series.map(s => ({ value: s, label: s }))}
        onChange={v => setFilterSeries(v ?? '')}
      />
      <Select
        placeholder="All Types"
        allowClear
        value={filterType || undefined}
        style={{ width: isMobile ? '100%' : 120 }}
        options={productTypes.map(t => ({ value: t, label: t }))}
        onChange={v => setFilterType(v ?? '')}
      />
    </>
  )
}
