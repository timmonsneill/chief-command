import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  label?: string
}

interface State {
  error: Error | null
  info: ErrorInfo | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null }

  static getDerivedStateFromError(error: Error): State {
    return { error, info: null }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info)
    this.setState({ error, info })
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-[60vh] flex flex-col items-center justify-center p-4 gap-3 text-center">
          <p className="text-red-600 font-medium">Something broke on this page</p>
          <p className="text-ink/60 text-xs max-w-md">
            {this.props.label ?? 'Unknown section'}
          </p>
          <pre className="text-[11px] font-mono text-ink/70 bg-surface-raised border border-surface-border rounded-lg p-3 max-w-full overflow-auto max-h-64 whitespace-pre-wrap break-words">
            {this.state.error.name}: {this.state.error.message}
            {'\n\n'}
            {this.state.error.stack?.split('\n').slice(0, 8).join('\n')}
          </pre>
          <button
            onClick={() => this.setState({ error: null, info: null })}
            className="px-4 py-2 rounded-lg bg-chief text-white text-sm"
          >
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
