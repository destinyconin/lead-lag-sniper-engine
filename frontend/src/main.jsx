import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

class RootErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center p-6 bg-slate-950 text-slate-100">
          <div className="max-w-md w-full rounded-2xl border border-slate-800 bg-slate-900 p-6 space-y-3 text-xs">
            <div className="text-sm font-bold text-rose-400">界面渲染异常提示</div>
            <div className="font-mono text-slate-300 break-all">
              {String(this.state.error?.message || this.state.error)}
            </div>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 rounded-full bg-white text-slate-900 font-semibold"
            >
              点击刷新恢复
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </React.StrictMode>,
)

