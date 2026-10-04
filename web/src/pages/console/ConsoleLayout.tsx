import { useQuery } from "@tanstack/react-query";
import { admin } from "../../api";
import { Shell, type NavItem } from "../../components/Shell";
import { GlobalSearch, useOrg } from "../../components/org";
import { IconActivity, IconAlert, IconBox, IconBuilding, IconFlow, IconGauge, IconInbox, IconLock, IconShield, IconTerminal } from "../../components/icons";

export function ConsoleLayout() {
  const ov = useQuery({ queryKey: ["admin", "overview"], queryFn: admin.overview, refetchInterval: 10_000 });
  // Same definition as the Overview tile, so the badge and the tile never disagree.
  const atRisk = useOrg(30).data?.totals.people_at_risk ?? ov.data?.at_risk;
  const traps = useQuery({ queryKey: ["admin", "decoys"], queryFn: admin.decoys, refetchInterval: 10_000 });
  const items: NavItem[] = [
    { to: "/console", label: "Overview", icon: <IconGauge />, end: true },
    { to: "/console/activity", label: "Activity", icon: <IconActivity /> },
    { to: "/console/org", label: "Organization", icon: <IconBuilding />, badge: atRisk, badgeTone: "warn", also: ["/console/people"] },
    { to: "/console/workflows", label: "Workflows", icon: <IconFlow /> },
    { to: "/console/security", label: "Security", icon: <IconAlert />, badge: ov.data?.incidents_open, badgeTone: "bad" },
    { to: "/console/traps", label: "Traps", icon: <IconLock />, badge: traps.data?.open, badgeTone: "bad" },
    { to: "/console/controls", label: "Controls", icon: <IconShield /> },
    { to: "/console/playground", label: "Playground", icon: <IconTerminal /> },
    { to: "/console/resources", label: "Resources", icon: <IconBox />, badge: ov.data?.zombies, badgeTone: "warn" },
    { to: "/console/requests", label: "Requests", icon: <IconInbox />, badge: ov.data?.requests_pending, badgeTone: "warn" },
  ];
  const area = ov.data?.org_name ? `${ov.data.org_name} · Admin console` : "Admin console";
  return <Shell area={area} items={items} search={(close) => <GlobalSearch onNavigate={close} />} />;
}
