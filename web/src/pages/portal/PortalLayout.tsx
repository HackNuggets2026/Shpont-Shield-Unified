import { Shell, type NavItem } from "../../components/Shell";
import { IconActivity, IconEye, IconHome, IconKey, IconMenuList } from "../../components/icons";

const items: NavItem[] = [
  { to: "/portal", label: "Home", icon: <IconHome />, end: true },
  { to: "/portal/menu", label: "Workflow menu", icon: <IconMenuList /> },
  { to: "/portal/access", label: "Access", icon: <IconKey /> },
  { to: "/portal/activity", label: "My activity", icon: <IconActivity /> },
  { to: "/portal/privacy", label: "Privacy", icon: <IconEye /> },
];

export function PortalLayout() {
  return <Shell area="My AI usage" items={items} />;
}
