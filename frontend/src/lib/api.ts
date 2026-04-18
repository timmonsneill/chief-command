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
  status: 'running' | 'completed' | 'failed' | 'working'
  // New fields from agent_tracker
  subagent_type?: string
  started_at?: string | null
  completed_at?: string | null
  elapsed_seconds?: number | null
  summary?: string
  worktree_path?: string
  last_active?: string | null
  // Legacy fields used by VoicePage agent_status events
  role?: string
  model?: string
  task?: string
  duration_seconds?: number | null
}

export interface Session {
  id: string
  started_at: string
  ended_at: string | null
  total_cost_cents: number
  turn_count: number
  duration_s: number | null
}

export interface Turn {
  id: number
  session_id: string
  created_at: string
  model: string
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  cost_cents: number
  user_text: string
  assistant_text: string
}

export interface SessionDetail extends Session {
  turns: Turn[]
}

export interface UsageSummary {
  today_cents: number
  week_cents: number
  month_cents: number
  alert_level: 'none' | 'warning' | 'critical'
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
  todo_total?: number
  todo_done?: number
  todo_percent?: number
  last_activity?: string | null
  recent_activity?: { hash: string; date: string; message: string }[]
  milestones?: { date: string; label: string }[]
  todo_progress?: { total: number; done: number; percent: number }
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

export type ActiveModel = 'claude-haiku-4-5' | 'claude-sonnet-4-6' | 'claude-opus-4-7'

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

export interface WsTurnCancelledEvent {
  type: 'turn_cancelled'
  reason: string
}

export interface WsContextSwitchedEvent {
  type: 'context_switched'
  project: string
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
  | WsTurnCancelledEvent
  | WsContextSwitchedEvent
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

// --- v3 Team / Memory / Context types ---

export interface AgentProfile {
  name: string
  role: string
  lean: string
  model: 'opus' | 'sonnet'
  tier: 'chief' | 'opus' | 'sonnet'
  memory_path: string
  last_active: string | null
  invocations_total: number
  description: string
}

export interface AgentMemoryResponse {
  name: string
  content: string
  updated_at: string
}

export interface MemoryEntry {
  filename: string
  title: string
  type: 'user' | 'feedback' | 'project' | 'reference'
  description: string
  content: string
  updated_at: string
}

export interface ProjectMemory {
  project: string
  status: 'active' | 'done'
  entries: MemoryEntry[]
}

export interface AgentMemory {
  name: string
  content: string
  updated_at: string | null
}

export interface AuditEntry {
  timestamp: string
  action: 'removed' | 'updated' | 'promoted' | 'demoted' | 'created'
  target: string
  reason: string
}

export interface MemoryListResponse {
  global: MemoryEntry[]
  per_project: ProjectMemory[]
  per_agent: AgentMemory[]
  audit_log: AuditEntry[]
}

export interface ProjectContextState {
  current: string
  available: string[]
}

export type ProjectContext = ProjectContextState

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
      const res = await request<{ projects: Record<string, unknown>[] }>('/projects')
      return (res.projects || []).map((p) => ({
        slug: ((p.id as string) || (p.slug as string) || ''),
        name: (p.name as string) || '',
        description: (p.description as string) || '',
        type: 'project',
        status: (p.status as string) || 'active',
        phases: [],
        todos: [],
        timeline: [],
        builds: [],
        todo_total: (p.todo_total as number) || 0,
        todo_done: (p.todo_done as number) || 0,
        todo_percent: (p.todo_percent as number) || 0,
        last_activity: (p.last_activity as string) || null,
      }))
    },
    get: (id: string) => request<Project>(`/projects/${id}`),
  },
  team: {
    list: () => request<{ agents: AgentProfile[] }>('/team'),
    getMemory: (name: string) =>
      request<{ name: string; content: string; updated_at: string }>(`/team/${encodeURIComponent(name)}/memory`),
    updateMemory: (name: string, content: string) =>
      request<{ name: string; content: string; updated_at: string }>(`/team/${encodeURIComponent(name)}/memory`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
      }),
  },
  memory: {
    getAll: () =>
      request<{
        global: MemoryEntry[]
        per_project: ProjectMemory[]
        per_agent: AgentMemory[]
        audit_log: AuditEntry[]
      }>('/memory'),
    get: (filename: string) =>
      request<MemoryEntry>(`/memory/${encodeURIComponent(filename)}`),
    update: (filename: string, content: string) =>
      request<MemoryEntry>(`/memory/${encodeURIComponent(filename)}`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
      }),
  },
  context: {
    get: () => request<ProjectContextState>('/context'),
    set: (project: string) =>
      request<{ current: string }>('/context', {
        method: 'POST',
        body: JSON.stringify({ project }),
      }),
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

export interface ModelUsageStats {
  cost_cents: number
  input_tokens: number
  output_tokens: number
  turns: number
}

export interface UsageByModel {
  today: Record<string, ModelUsageStats>
  week: Record<string, ModelUsageStats>
  month: Record<string, ModelUsageStats>
}

export interface UsageDayPoint {
  date: string
  cost_cents: number
  turns: number
}

export const sessionsApi = {
  list: (project?: string) => {
    const qs = project && project !== 'All' ? `?project=${encodeURIComponent(project)}` : ''
    return request<Session[]>(`/sessions${qs}`)
  },
  get: (id: string) => request<SessionDetail>(`/sessions/${id}`),
  getCurrent: () => request<SessionUsage | null>('/sessions/current'),
  usageSummary: () => request<UsageSummary>('/usage/summary'),
  byModel: () => request<UsageByModel>('/usage/by_model'),
  daily: (days: number = 30) => request<{ days: UsageDayPoint[] }>(`/usage/daily?days=${days}`),
}

export { ApiError }
