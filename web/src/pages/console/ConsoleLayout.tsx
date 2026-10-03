import { useQuery } from "@tanstack/react-query";
import { admin } from "../../api";
import { Shell, type NavItem } from "../../components/Shell";
import { IconAlert, IconBox, IconFlow, IconGauge, IconInbox, IconUsers } from "../../components/icons";

export function ConsoleLayout() {
  const ov = useQuery({ queryKey: ["admin", "overview"], queryFn: admin.overview, refetchInterval: 10_000 });
  const items: NavItem[] = [
    { to: "/console", label: "Overview", icon: <IconGauge />, end: true },
    { to: "/console/workflows", label: "Workflows", icon: <IconFlow /> },
    { to: "/console/people", label: "People", icon: <IconUsers />, badge: ov.data?.at_risk, badgeTone: "warn" },
    { to: "/console/security", label: "Security", icon: <IconAlert />, badge: ov.data?.incidents_open, badgeTone: "bad" },
    { to: "/console/resources", label: "Resources", icon: <IconBox />, badge: ov.data?.zombies, badgeTone: "warn" },
    { to: "/console/requests", label: "Requests", icon: <IconInbox />, badge: ov.data?.requests_pending, badgeTone: "warn" },
  ];
  return <Shell area="Admin console" items={items} />;
}
