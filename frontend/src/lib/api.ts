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
  // Fields from backend list endpoint
  category?: string
  file?: string
  todo_total?: number
  todo_done?: number
  todo_percent?: number
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

export type ActiveModel = 'claude-haiku-4-5' | 'claude-sonnet-4-6'

export interface WsTranscriptEvent {
  type: 'transcript'
  content: string
  final: boolean
}

export interface WsActiveModelEvent {
  type: 'active_model'
  model: ActiveModel
}

export interface WsTokenEvent {
  type: 'token'
  text: string
}

export interface WsMessageDoneEvent {
  type: 'message_done'
}

export interface WsTtsStartEvent {
  type: 'tts_start'
}

export interface WsTtsEndEvent {
  type: 'tts_end'
}

export interface WsUsageEvent {
  type: 'usage'
  session_id: string
  model: string
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  turn_cost_cents: number
  session_total_cents: number
}

export interface WsAgentStatusEvent {
  type: 'agent_status'
  agents: Record<string, string>[]
}

export type WsEvent =
  | WsTranscriptEvent
  | WsActiveModelEvent
  | WsTokenEvent
  | WsMessageDoneEvent
  | WsTtsStartEvent
  | WsTtsEndEvent
  | WsUsageEvent
  | WsAgentStatusEvent

export interface SessionUsage {
  session_id: string
  model: string
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  turn_cost_cents: number
  session_total_cents: number
  turn_count: number
  started_at: string
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
    list: async (): Promise<Project[]> => {
      const res = await request<{ projects: Record<string, unknown>[]; memory_index: unknown[] }>('/projects')
      // Map backend shape to frontend Project interface
      return (res.projects || []).map((p) => ({
        slug: (p.slug as string) || '',
        name: (p.name as string) || '',
        description: (p.description as string) || '',
        type: (p.category as string) || 'project',
        status: 'active',
        phases: [],
        todos: [],
        timeline: [],
        builds: [],
        category: (p.category as string) || '',
        file: (p.file as string) || '',
        todo_total: (p.todo_total as number) || 0,
        todo_done: (p.todo_done as number) || 0,
        todo_percent: (p.todo_percent as number) || 0,
      }))
    },
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

export const sessionsApi = {
  getCurrent: () => request<SessionUsage | null>('/sessions/current'),
  list: () => request<SessionUsage[]>('/sessions'),
  get: (id: string) => request<SessionUsage>(`/sessions/${id}`),
  getUsageSummary: () => request<{ total_cents: number; sessions: SessionUsage[] }>('/sessions/usage-summary'),
}

export { ApiError }
