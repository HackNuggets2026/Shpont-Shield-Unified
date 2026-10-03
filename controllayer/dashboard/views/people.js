// People: the searchable, sortable, paginated list (server-side). Owner: see docs/console-contract.md.
(function () {
  "use strict";
  window.ACL.register({
    id: "people",
    title: "People",
    tab: true,
    order: 20,
    hidden: true, // not built yet
    load: () => null,
    render: () => window.ACL.h.empty("People view: not built yet."),
  });
})();
