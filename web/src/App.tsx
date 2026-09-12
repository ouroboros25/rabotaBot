import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import Queue from './pages/Queue';
import SearchPage from './pages/Search';
import JobDetail from './pages/JobDetail';
import Drafts from './pages/Drafts';
import DraftDetail from './pages/DraftDetail';
import Applications from './pages/Applications';
import Sources from './pages/Sources';
import ProfilePage from './pages/Profile';

const NAV = [
  { to: '/', label: 'Обзор', end: true },
  { to: '/queue', label: 'Очередь' },
  { to: '/search', label: 'Поиск' },
  { to: '/drafts', label: 'Черновики' },
  { to: '/applications', label: 'Отклики' },
  { to: '/sources', label: 'Источники' },
  { to: '/profile', label: 'Профиль' },
];

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-panel/60 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-2">
          <span className="font-semibold tracking-tight text-accent">rabotaBot</span>
          <nav className="flex flex-wrap gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `px-3 py-1.5 rounded-md text-sm transition-colors ${
                    isActive ? 'bg-accent/15 text-accent' : 'text-slate-300 hover:bg-panel2'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <span className="ml-auto label hidden sm:block">
            бот не отправляет · отправляете вы
          </span>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/queue/:id" element={<JobDetail />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/drafts" element={<Drafts />} />
          <Route path="/drafts/:id" element={<DraftDetail />} />
          <Route path="/applications" element={<Applications />} />
          <Route path="/sources" element={<Sources />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
