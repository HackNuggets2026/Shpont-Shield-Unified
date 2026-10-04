import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Login } from "./pages/Login";
import { ConsoleLayout } from "./pages/console/ConsoleLayout";
import { Overview } from "./pages/console/Overview";
import { Workflows } from "./pages/console/Workflows";
import { WorkflowPage } from "./pages/console/WorkflowPage";
import { People } from "./pages/console/People";
import { PersonPage } from "./pages/console/Person";
import { Organization } from "./pages/console/Organization";
import { OrgUnitPage } from "./pages/console/OrgUnit";
import { ActivityPage } from "./pages/console/Activity";
import { Security } from "./pages/console/Security";
import { TrapsPage } from "./pages/console/Traps";
import { ControlsPage } from "./pages/console/Controls";
import { IncidentPage } from "./pages/console/Incident";
import { Resources } from "./pages/console/Resources";
import { Requests } from "./pages/console/Requests";
import { NotFound } from "./pages/NotFound";
import type { ReactNode } from "react";

// The console is for admins only; an employee key signs in to nothing and the login page says so.
function Guard({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  if (session?.role !== "admin") return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function Home() {
  const { session } = useAuth();
  return <Navigate to={session?.role === "admin" ? "/console" : "/login"} replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Login />} />
      <Route
        path="/console"
        element={
          <Guard>
            <ConsoleLayout />
          </Guard>
        }
      >
        <Route index element={<Overview />} />
        <Route path="workflows" element={<Workflows />} />
        <Route path="workflows/:name" element={<WorkflowPage />} />
        <Route path="org" element={<Organization />} />
        <Route path="org/department/:name" element={<OrgUnitPage key="department" kind="department" />} />
        <Route path="org/team/:name" element={<OrgUnitPage key="team" kind="team" />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="people" element={<People />} />
        <Route path="people/:id" element={<PersonPage />} />
        <Route path="security" element={<Security />} />
        <Route path="traps" element={<TrapsPage />} />
        <Route path="controls" element={<ControlsPage />} />
        <Route path="incidents/:id" element={<IncidentPage />} />
        <Route path="resources" element={<Resources />} />
        <Route path="requests" element={<Requests />} />
        <Route path="*" element={<NotFound />} />
      </Route>
      <Route path="*" element={<NotFound standalone />} />
    </Routes>
  );
}
