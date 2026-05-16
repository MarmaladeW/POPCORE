import { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  TrendingUp, TrendingDown, PackageX, DollarSign,
  AlertTriangle, Database, RefreshCw, X,
} from 'lucide-react'
import client from '../../api/client'
import { useAppStore } from '../../store'

interface Insight {
  id:           number
  store:        string
  check_type:   string
  severity:     'alert' | 'warning' | 'info'
  title:        string
  body:         string
  product_id:   number | null
  meta:         Record<string, unknown>
  generated_at: string
  dismissed_at: string | null
}

const TYPE_CONFIG: Record<string, { icon: React.ReactNode }> = {
  VELOCITY_SPIKE: { icon: <TrendingUp  size={14} /> },
  DEAD_STOCK:     { icon: <PackageX    size={14} /> },
  REVENUE_GAP:    { icon: <DollarSign  size={14} /> },
  STOCKOUT_RISK:  { icon: <AlertTriangle size={14} /> },
  DATA_QUALITY:   { icon: <Database    size={14} /> },
}

const SEVERITY_COLOR: Record<string, string> = {
  alert:   '#EF4444',
  warning: '#F59E0B',
  info:    '#6366F1',
}

export default function InsightFeed() {
  const { selectedStore }           = useAppStore()
  const [insights, setInsights]     = useState<Insight[]>([])
  const [loading,  setLoading]      = useState(false)
  const [spinning, setSpinning]     = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = {}
      if (selectedStore?.code && selectedStore.code !== 'ALL') {
        params.store = selectedStore.code
      }
      const r = await client.get('/insights', { params })
      setInsights(r.data)
    } catch {
      // dashboard shouldn't break if insights endpoint errors
    } finally {
      setLoading(false)
      setSpinning(false)
    }
  }, [selectedStore?.code])

  useEffect(() => { load() }, [load])

  async function dismiss(id: number) {
    setInsights(prev => prev.filter(i => i.id !== id))
    await client.post(`/insights/${id}/dismiss`).catch(() => {})
  }

  function handleRefresh() {
    setSpinning(true)
    load()
  }

  if (!loading && insights.length === 0) return null

  const alerts   = insights.filter(i => i.severity === 'alert')
  const warnings = insights.filter(i => i.severity === 'warning')
  const infos    = insights.filter(i => i.severity === 'info')
  const ordered  = [...alerts, ...warnings, ...infos]

  return (
    <Card style={{ marginBottom: 16 }}>
      <CardHeader style={{
        padding:        '12px 16px 4px',
        display:        'flex',
        flexDirection:  'row',
        alignItems:     'center',
        justifyContent: 'space-between',
      }}>
        <CardTitle style={{ fontSize: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
          <AlertTriangle size={16} style={{ color: '#F59E0B' }} />
          Insights
          {alerts.length > 0 && (
            <Badge variant="destructive" style={{ fontSize: 11, padding: '0 6px' }}>
              {alerts.length}
            </Badge>
          )}
          {warnings.length > 0 && alerts.length === 0 && (
            <Badge
              style={{
                fontSize: 11, padding: '0 6px',
                background: '#FEF3C7', color: '#92400E', border: '1px solid #FDE68A',
              }}
            >
              {warnings.length}
            </Badge>
          )}
        </CardTitle>
        <button
          onClick={handleRefresh}
          disabled={loading}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: '#9ca3af', padding: 4, display: 'flex', alignItems: 'center',
          }}
          title="Refresh insights"
        >
          <RefreshCw
            size={14}
            style={{ animation: spinning ? 'spin 0.8s linear infinite' : undefined }}
          />
        </button>
      </CardHeader>

      <CardContent style={{ padding: '4px 0 8px' }}>
        {ordered.slice(0, 10).map(ins => {
          const tc    = TYPE_CONFIG[ins.check_type] ?? { icon: <AlertTriangle size={14} /> }
          const color = SEVERITY_COLOR[ins.severity] ?? '#6366F1'
          const isSpikeDown = ins.check_type === 'VELOCITY_SPIKE' && ins.severity === 'warning'
          const icon  = isSpikeDown ? <TrendingDown size={14} /> : tc.icon

          return (
            <div
              key={ins.id}
              style={{
                padding:      '9px 14px 9px 12px',
                borderBottom: '1px solid #f3f4f6',
                display:      'flex',
                gap:          10,
                alignItems:   'flex-start',
                borderLeft:   `3px solid ${color}`,
              }}
            >
              <div style={{ color, marginTop: 1, flexShrink: 0 }}>{icon}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, color: '#111827' }}>
                  {ins.title}
                  <span style={{
                    fontSize: 10, fontWeight: 400, color: '#9ca3af',
                    marginLeft: 6, background: '#f3f4f6',
                    borderRadius: 4, padding: '1px 5px',
                  }}>
                    {ins.store}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2, lineHeight: 1.5 }}>
                  {ins.body}
                </div>
              </div>
              <button
                onClick={() => dismiss(ins.id)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: '#d1d5db', padding: 2, flexShrink: 0,
                  display: 'flex', alignItems: 'center',
                }}
                title="Dismiss"
              >
                <X size={13} />
              </button>
            </div>
          )
        })}
        {insights.length > 10 && (
          <div style={{ padding: '8px 16px', fontSize: 12, color: '#9ca3af', textAlign: 'center' }}>
            +{insights.length - 10} more — dismiss some to see the rest
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/** Exported count fetcher for use in AppLayout badge. */
export async function fetchInsightCount(storeCode?: string): Promise<number> {
  try {
    const params: Record<string, string> = {}
    if (storeCode && storeCode !== 'ALL') params.store = storeCode
    const r = await client.get('/insights/count', { params })
    return r.data.count ?? 0
  } catch {
    return 0
  }
}
