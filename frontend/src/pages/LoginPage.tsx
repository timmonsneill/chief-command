import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { Lock } from 'lucide-react'

export default function LoginPage() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { login, isAuthenticated } = useAuth()
  const navigate = useNavigate()

  // If already authenticated, redirect
  if (isAuthenticated) {
    navigate('/voice', { replace: true })
    return null
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!password.trim()) return

    setError('')
    setLoading(true)

    try {
      await login(password)
      navigate('/voice', { replace: true })
    } catch {
      setError('Wrong password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="h-[100dvh] flex items-center justify-center bg-surface px-6">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-chief/10 mb-4">
            <Lock className="text-chief" size={28} />
          </div>
          <h1 className="text-2xl font-bold text-white">Chief</h1>
          <p className="text-sm text-white/40 mt-1">Command Center</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              autoFocus
              autoComplete="current-password"
              className="w-full h-12 px-4 rounded-xl bg-surface-raised border border-surface-border text-white placeholder-white/30 text-sm focus:outline-none focus:border-chief transition-colors"
            />
          </div>

          {error && (
            <p className="text-status-offline text-sm text-center">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading || !password.trim()}
            className="w-full h-12 rounded-xl bg-chief text-white font-medium text-sm transition-all hover:bg-chief-dark active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? 'Connecting...' : 'Login'}
          </button>
        </form>
      </div>
    </div>
  )
}
