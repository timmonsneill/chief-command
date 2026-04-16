const API_BASE = '/api'

class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

function getToken(): string | null {
  return localStorage.getItem('chief_token')
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })

  if (res.status === 401) {
    localStorage.removeItem('chief_token')
    window.location.href = '/login'
    throw new ApiError('Unauthorized', 401)
  }

  if (!res.ok) {
    const body = await res.text()
    throw new ApiError(body || res.statusText, res.status)
  }

  if (res.status === 204) return undefined as T

  return res.json()
}

// --- Types ---

export interface AuthResponse {
  token: string
}

export interface Agent {
  id: string
  name: string
  role: string
  model: string
  status: 'idle' | 'working' | 'complete'
  task: string
  started_at: string | null
  duration_seconds: number | null
}

export interface ReviewFinding {
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
  agent: string
  message: string
  file?: string
  line?: number
}

export interface ReviewSweep {
  id: string
  timestamp: string
  findings: ReviewFinding[]
}

export interface Project {
  slug: string
  name: string
  description: string
  type: string
  status: string
  phases: Phase[]
  todos: Todo[]
  timeline: TimelineEntry[]
  builds: Build[]
}

export interface Phase {
  name: string
  progress: number
  total: number
  completed: number
}

export interface Todo {
  id: string
  category: string
  text: string
  done: boolean
}

export interface TimelineEntry {
  id: string
  date: string
  description: string
}

export interface Build {
  id: string
  timestamp: string
  findings_count: {
    CRITICAL: number
    HIGH: number
    MEDIUM: number
    LOW: number
  }
}

export interface VoiceMessage {
  id: string
  role: 'user' | 'chief' | 'assistant'
  content: string
  timestamp: string
}

export interface TerminalOutput {
  type: 'command' | 'output' | 'error'
  content: string
  timestamp: string
}

// --- API methods ---

export const api = {
  auth: {
    login: (password: string) =>
      request<AuthResponse>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ password }),
      }),
    verify: () => request<{ valid: boolean }>('/auth/verify'),
  },
  agents: {
    list: () => request<Agent[]>('/agents'),
    recentReviews: () => request<ReviewSweep[]>('/agents/reviews'),
  },
  projects: {
    list: () => request<Project[]>('/projects'),
    get: (slug: string) => request<Project>(`/projects/${slug}`),
  },
  voice: {
    send: (audio: Blob) => {
      const formData = new FormData()
      formData.append('audio', audio)
      const token = getToken()
      return fetch(`${API_BASE}/voice/send`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      })
    },
    sendText: (text: string) =>
      request<VoiceMessage>('/voice/send-text', {
        method: 'POST',
        body: JSON.stringify({ text }),
      }),
    sendImage: (image: Blob) => {
      const formData = new FormData()
      formData.append('image', image)
      const token = getToken()
      return fetch(`${API_BASE}/voice/send-image`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      })
    },
  },
}

export { ApiError }
