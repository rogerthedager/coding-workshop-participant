import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom';
import { AuthProvider, useAuth } from './auth';
import { DirectoryProvider } from './directory';
import Layout from './components/Layout';
import { Loading } from './components/ui';
import { ChangePassword, Login, Register } from './pages/Auth';
import Board from './pages/Board';
import Dashboard from './pages/Dashboard';
import Detail from './pages/Detail';
import Engineers from './pages/Engineers';
import Facilities from './pages/Facilities';
import IncidentList from './pages/IncidentList';
import MyTickets from './pages/MyTickets';
import Notifications from './pages/Notifications';
import Overview from './pages/Overview';
import Report from './pages/Report';
import Team from './pages/Team';
import TeamsAdmin from './pages/TeamsAdmin';
import TeamSummary from './pages/TeamSummary';
import Triage from './pages/Triage';
import Users from './pages/Users';

/** Who may open each protected area; the API enforces the same rules. */
const ACCESS = {
  admin: (auth) => auth.isAdmin,
  engineer: (auth) => auth.isEngineer,
  board: (auth) => auth.isEngineer || auth.isAdmin,
  lead: (auth) => auth.isLead || auth.isAdmin,
};

/** Signed-in area; role-specific pages also check roles. */
function Protected({ need }) {
  const auth = useAuth();
  const location = useLocation();
  if (!auth.ready) return <Loading />;
  if (!auth.user) return <Navigate to="/login" replace state={{ from: location }} />;
  if (auth.user.must_change_password) return <Navigate to="/change-password" replace state={{ from: location }} />;
  if (need && !ACCESS[need](auth)) return <Navigate to="/" replace />;
  return <Layout />;
}

function Alias({ to }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

/** A fresh Detail per ticket, so moving from one ticket to another never reuses its state. */
function TicketDetail() {
  const { id } = useParams();
  return <Detail key={id} />;
}

function PublicOnly({ children }) {
  const { ready } = useAuth();
  return ready ? children : <Loading />;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <DirectoryProvider>
          <Routes>
            <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
            <Route path="/register" element={<PublicOnly><Register /></PublicOnly>} />
            <Route path="/change-password" element={<PublicOnly><ChangePassword /></PublicOnly>} />
            {/* Design paths; they redirect to the existing pages, keeping the query string. */}
            <Route path="/tickets" element={<Alias to="/" />} />
            <Route path="/tickets/new" element={<Alias to="/report" />} />
            <Route path="/search" element={<Alias to="/incidents" />} />
            <Route element={<Protected />}>
              <Route index element={<MyTickets />} />
              <Route path="/report" element={<Report />} />
              <Route path="/incidents" element={<IncidentList />} />
              <Route path="/incidents/:id" element={<TicketDetail />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/notifications" element={<Notifications />} />
            </Route>
            <Route element={<Protected need="engineer" />}>
              <Route path="/queue" element={(
                <IncidentList preset={{ scope: 'assigned', sort: 'sla_due', direction: 'asc' }} title="My queue"
                  intro="Tickets assigned to you, next SLA due first. Final tickets stay here for reference." />
              )} />
            </Route>
            <Route element={<Protected need="board" />}>
              <Route path="/board" element={<Board />} />
            </Route>
            <Route element={<Protected need="lead" />}>
              <Route path="/team" element={<TeamSummary />} />
            </Route>
            <Route element={<Protected need="admin" />}>
              <Route path="/triage" element={<Triage />} />
              <Route path="/workload" element={<Team />} />
              <Route path="/overview" element={<Overview />} />
              <Route path="/engineers" element={<Engineers />} />
              <Route path="/admin/teams" element={<TeamsAdmin />} />
              <Route path="/admin/facilities" element={<Facilities />} />
              <Route path="/admin/users" element={<Users />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </DirectoryProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
