import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button, Result } from 'antd'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
          <Result
            status="error"
            title="页面出现错误"
            subTitle="Something went wrong. Reload the page or contact your admin."
            extra={[
              <Button key="reload" type="primary" onClick={() => window.location.reload()}>
                Reload Page
              </Button>,
              <Button key="home" onClick={() => { this.setState({ error: null }); window.location.href = '/' }}>
                Go Home
              </Button>,
            ]}
          />
        </div>
      )
    }
    return this.props.children
  }
}
