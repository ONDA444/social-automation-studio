import { Component } from 'react'

// Without this, a render error anywhere below unmounts the WHOLE app — the blank
// screen the user hit. This catches it, keeps the shell, and offers a reload.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary caught:', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, color: '#EEEEFF' }}>
          <h2 style={{ fontWeight: 700, marginBottom: 8, fontSize: 18 }}>
            Algo quebrou nesta tela
          </h2>
          <p style={{ color: '#9A9AC0', marginBottom: 16, fontSize: 14 }}>
            O resto do app segue funcionando. Recarregue para tentar de novo.
          </p>
          <button
            onClick={() => { this.setState({ error: null }); window.location.reload() }}
            style={{
              padding: '8px 16px', borderRadius: 10, fontWeight: 600, cursor: 'pointer',
              border: '1px solid rgba(124,106,255,0.4)',
              background: 'rgba(124,106,255,0.15)', color: '#C9BEFF',
            }}
          >
            Recarregar
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
