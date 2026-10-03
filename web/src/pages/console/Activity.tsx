import { useState } from "react";
import { admin } from "../../api";
import { org } from "../../orgApi";
import { ActivityFeed } from "../../components/ActivityFeed";
import { Breadcrumbs } from "../../components/org";
import { Card, PageHeader, Segmented } from "../../components/ui";

/** The live feed, moved off the Overview: high-signal by default, everything on request. */
export function ActivityPage() {
  const [mode, setMode] = useState<"interesting" | "all">("interesting");
  return (
    <div>
      <PageHeader
        back={<Breadcrumbs items={[{ label: "Overview", to: "/console" }, { label: "Activity" }]} />}
        title="Live activity"
        subtitle={
          mode === "interesting"
            ? "Blocks, redactions, incidents, grants, zombie flags and Claude Code rejections. Allowed checks are hidden."
            : "Every check, lease, meter report and detection, newest first."
        }
        actions={
          <Segmented
            value={mode}
            onChange={setMode}
            options={[
              { value: "interesting", label: "Needs a look" },
              { value: "all", label: "Everything" },
            ]}
          />
        }
      />
      <Card flush>
        {mode === "interesting" ? (
          <ActivityFeed
            key="interesting"
            load={(p) => org.activity({ ...p, interesting: true })}
            queryKey={["admin", "activity", "interesting"]}
            linkPeople
            maxH="calc(100vh - 220px)"
            interval={5000}
          />
        ) : (
          <ActivityFeed key="all" load={admin.activity} queryKey={["admin", "activity"]} linkPeople maxH="calc(100vh - 220px)" />
        )}
      </Card>
    </div>
  );
}
