import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Login } from "./pages/Login";
import { ConsoleLayout } from "./pages/console/ConsoleLayout";
import { Overview } from "./pages/console/Overview";
import { Workflows } from "./pages/console/Workflows";
import { People } from "./pages/console/People";
import { PersonPage } from "./pages/console/Person";
import { Organization } from "./pages/console/Organization";
import { OrgUnitPage } from "./pages/console/OrgUnit";
import { ActivityPage } from "./pages/console/Activity";
import { Security } from "./pages/console/Security";
import { IncidentPage } from "./pages/console/Incident";
import { Resources } from "./pages/console/Resources";
import { Requests } from "./pages/console/Requests";
import { PortalLayout } from "./pages/portal/PortalLayout";
import { PortalHome } from "./pages/portal/Home";
import { PortalMenu } from "./pages/portal/Menu";
import { PortalAccess } from "./pages/portal/Access";
import { PortalActivity } from "./pages/portal/Activity";
import { PortalPrivacy } from "./pages/portal/Privacy";
import { NotFound } from "./pages/NotFound";
import type { ReactNode } from "react";

function Guard({ role, children }: { role: "admin" | "employee"; children: ReactNode }) {
  const { session } = useAuth();
  if (!session) return <Navigate to="/login" replace />;
  if (session.role !== role) return <Navigate to={session.role === "admin" ? "/console" : "/portal"} replace />;
  return <>{children}</>;
}

function Home() {
  const { session } = useAuth();
  if (!session) return <Navigate to="/login" replace />;
  return <Navigate to={session.role === "admin" ? "/console" : "/portal"} replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Login />} />
      <Route
        path="/console"
        element={
          <Guard role="admin">
            <ConsoleLayout />
          </Guard>
        }
      >
        <Route index element={<Overview />} />
        <Route path="workflows" element={<Workflows />} />
        <Route path="org" element={<Organization />} />
        <Route path="org/department/:name" element={<OrgUnitPage key="department" kind="department" />} />
        <Route path="org/team/:name" element={<OrgUnitPage key="team" kind="team" />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="people" element={<People />} />
        <Route path="people/:id" element={<PersonPage />} />
        <Route path="security" element={<Security />} />
        <Route path="incidents/:id" element={<IncidentPage />} />
        <Route path="resources" element={<Resources />} />
        <Route path="requests" element={<Requests />} />
        <Route path="*" element={<NotFound />} />
      </Route>
      <Route
        path="/portal"
        element={
          <Guard role="employee">
            <PortalLayout />
          </Guard>
        }
      >
        <Route index element={<PortalHome />} />
        <Route path="menu" element={<PortalMenu />} />
        <Route path="access" element={<PortalAccess />} />
        <Route path="activity" element={<PortalActivity />} />
        <Route path="privacy" element={<PortalPrivacy />} />
        <Route path="*" element={<NotFound />} />
      </Route>
      <Route path="*" element={<NotFound standalone />} />
    </Routes>
  );
}
