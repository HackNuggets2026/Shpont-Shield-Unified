// Person drill-down (?person=<id>). Owner: see docs/console-contract.md.
(function () {
  "use strict";
  window.ACL.register({
    id: "person",
    title: "Person",
    parent: "people",
    load: () => null,
    render: () => window.ACL.h.empty("Person view: not built yet."),
  });
})();
