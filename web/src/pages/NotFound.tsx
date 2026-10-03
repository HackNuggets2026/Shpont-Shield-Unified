import { Link } from "react-router-dom";
import { Empty } from "../components/ui";

export function NotFound({ standalone }: { standalone?: boolean }) {
  const body = <Empty title="Page not found" hint={<Link className="text-accent underline" to="/">Go to the start page</Link>} />;
  return standalone ? <div className="flex min-h-screen items-center justify-center bg-page">{body}</div> : body;
}
