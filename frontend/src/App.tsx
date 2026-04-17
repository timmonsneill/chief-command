import { Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import Layout from './components/Layout'
import VoicePage from './pages/VoicePage'
import AgentsPage from './pages/AgentsPage'
import TerminalPage from './pages/TerminalPage'
import ProjectsPage from './pages/ProjectsPage'
import ProjectDashboard from './pages/ProjectDashboard'
import SessionsPage from './pages/SessionsPage'
import TeamPage from './pages/TeamPage'
import MemoryPage from './pages/MemoryPage'
import LoginPage from './pages/LoginPage'
import SharePage from './pages/SharePage'
import { AuthProvider, useAuth } from './hooks/useAuth'

function ProtectedRoutes() {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-surface">
        <div className="text-chief text-lg">Connecting to Chief...</div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return (
    <Layout>
      <Routes>
        <Route path="/voice" element={<VoicePage />} />
        <Route path="/team" element={<TeamPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/terminal" element={<TerminalPage />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/:slug" element={<ProjectDashboard />} />
        <Route path="/memory" element={<MemoryPage />} />
        <Route path="/sessions" element={<SessionsPage />} />
        <Route path="*" element={<Navigate to="/voice" replace />} />
      </Routes>
    </Layout>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Toaster
        position="top-center"
        toastOptions={{
          style: {
            background: '#1a1a1a',
            color: '#fff',
            border: '1px solid #2e2e2e',
          },
        }}
      />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/share/:slug" element={<SharePage />} />
        <Route path="/*" element={<ProtectedRoutes />} />
      </Routes>
    </AuthProvider>
  )
}
