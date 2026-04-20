import { Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import Layout from './components/Layout'
import { ErrorBoundary } from './components/ErrorBoundary'
import VoicePage from './pages/VoicePage'
import AgentsPage from './pages/AgentsPage'
import TerminalPage from './pages/TerminalPage'
import ProjectsPage from './pages/ProjectsPage'
import ProjectDashboard from './pages/ProjectDashboard'
import UsagePage from './pages/UsagePage'
import TeamPage from './pages/TeamPage'
import MemoryPage from './pages/MemoryPage'
import LoginPage from './pages/LoginPage'
import SharePage from './pages/SharePage'
import { AuthProvider, useAuth } from './hooks/useAuth'
import { ProjectContextProvider } from './contexts/ProjectContextProvider'

function ProtectedRoutes() {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-surface">
        <div className="font-display text-primary text-lg">Connecting to Chief...</div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return (
    <ProjectContextProvider>
    <Layout>
      <Routes>
        <Route path="/voice" element={<ErrorBoundary label="Voice page"><VoicePage /></ErrorBoundary>} />
        <Route path="/team" element={<ErrorBoundary label="Team page"><TeamPage /></ErrorBoundary>} />
        <Route path="/agents" element={<ErrorBoundary label="Agents page"><AgentsPage /></ErrorBoundary>} />
        <Route path="/terminal" element={<ErrorBoundary label="Terminal page"><TerminalPage /></ErrorBoundary>} />
        <Route path="/projects" element={<ErrorBoundary label="Projects page"><ProjectsPage /></ErrorBoundary>} />
        <Route path="/projects/:slug" element={<ErrorBoundary label="Project dashboard"><ProjectDashboard /></ErrorBoundary>} />
        <Route path="/memory" element={<ErrorBoundary label="Memory page"><MemoryPage /></ErrorBoundary>} />
        <Route path="/usage" element={<ErrorBoundary label="Usage page"><UsagePage /></ErrorBoundary>} />
        <Route path="/sessions" element={<Navigate to="/usage" replace />} />
        <Route path="*" element={<Navigate to="/voice" replace />} />
      </Routes>
    </Layout>
    </ProjectContextProvider>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Toaster
        position="top-center"
        toastOptions={{
          style: {
            background: '#ffffff',
            color: '#15171c',
            border: '1px solid #e3e7ec',
            boxShadow:
              '0 2px 5px rgba(21,23,28,.07), 0 10px 24px rgba(21,23,28,.07)',
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
