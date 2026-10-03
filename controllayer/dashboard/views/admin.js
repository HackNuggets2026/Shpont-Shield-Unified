// Secondary tabs: risk, resources and grants, controls, audit trail, playground. Owner: see docs/console-contract.md.
(function () {
  "use strict";
  const stub = (id, title, order) => window.ACL.register({
    id, title, tab: true, order, hidden: true, // not built yet
    load: () => null,
    render: () => window.ACL.h.empty(title + ": not built yet."),
  });
  stub("risk", "Risk", 30);
  stub("resources", "Resources", 40);
  stub("controls", "Controls", 50);
  stub("audit", "Audit", 60);
  stub("playground", "Try a prompt", 70);
})();
