# Security console: modules and API contract

The redesigned console is opt-in at `/security?ui=next` until it covers everything the classic console (`core.js`) does; then it becomes the default and the classic security code is removed. `/me` stays on `core.js`.

## Front-end modules

| File | Owns |
|---|---|
| `controllayer/dashboard/app.js` | `window.ACL`: data access, URL state and router, shell (header, tabs, period filter), Primer building blocks (`ACL.h`), SVG charts (`ACL.charts`), shared tooltip |
| `views/overview.js` | `?view=overview` (default) |
| `views/people.js` | `?view=people` |
| `views/person.js` | `?person=<id>` (agents too) |
| `views/admin.js` | `?view=risk`, `resources`, `controls`, `audit`, `playground` |
| `core.css` | all styles; console styles under "security console" |
| `panel.html` | loads app.js, the views, then core.js |

Each view file touches only itself. A view not ready yet registers with `hidden: true` (no tab; still reachable by URL).

### Registering a view

```js
window.ACL.register({
  id: "people",          // ?view=people; "person" is chosen whenever ?person= is set
  title: "People",       // tab label and document title
  tab: true, order: 20,  // shown in the tab bar, sorted by order
  hidden: false,         // true: no tab
  parent: "people",      // optional: which tab to highlight (person -> people)
  periodic: true,        // false hides the period filter
  load: (ctx) => Promise<data>,      // ctx = {params, period, data}; use ACL.get / ACL.send
  render: (data, ctx) => "<html>",  // pure: string in, string out
  actions: { name: async (el, ctx) => {} },   // for <button data-act="name" data-x="..."> (el.dataset)
  inputs: { name: (value, ctx, el) => {} },   // for <input data-input="name">, debounced 250 ms
  onKey: (event, ctx) => {},                  // optional keydown hook
});
```

A table row with `data-href` (e.g. from `h.table`'s `rowAttrs`) is a link, except for clicks on a control inside it; ctrl/cmd-click opens a tab.

After an action the page re-renders (an exception becomes a red flash). The router re-renders every 15 s unless an input has focus, and keeps focus and caret in the `data-input` element across renders.

### URL state

The URL is the state; every link is `<a href data-nav>` and the router intercepts it (ctrl/cmd-click opens a tab).
- `ACL.href(patch, reset = true)`: a URL with `patch` applied. `reset` keeps only `token`, `period` and `ui`; `reset = false` keeps every current param. `null` or `""` removes a param.
- `ACL.go(patch, {reset, replace})`: navigate and render.
- `<select data-param="team">` sets that param (and clears `page`).
- `ACL.params()`, `ACL.period()` (1..31, default 30).
- The token: `ACL.get`/`ACL.send` send `x-admin-token` only when the page was opened with `?token=`; `ACL.withToken(url)` adds it to download links only then. In demo mode there is none.

### Helpers (`ACL.h`)

`esc`, `usd`, `num`, `pct(fraction)`, `ago(ts)`, `dur(hours)`, `periodLabel(days)`, `tone(action|level|sensitivity)`, `muted`, `empty`, `badge(tone, text)`, `note(html, tone)`, `button(label, attrs, kind)`, `link(label, href)`, `person(id, name)` (link to the person page), `card(title, body, tools)`, `table(cols, rows, {sort, rowAttrs, detail})` (sortable headers link to `?sort=`; `detail(i)` returning html adds a full-width row under row i), `segmented`, `navSegmented(param, options, current)`, `navSelect(param, options, current)`, `pill(on, label, attrs, {disabled, title})` (the colour-coded on/off toggle; disabled = greyed and inert), `meter(fraction)` (budget bar: blue, amber from 80%, red over 100%), `tiles([{label, value, sub, tone, href, meter}])`, `pager(peopleResponse)`.

Layout: `<div class="acl-cols">` with children `acl-c3`..`acl-c12` (12-column grid). Greyed table row: `<tr class="acl-greyed">`.

### Charts (`ACL.charts`)

All inline SVG, one palette (`--viz-1`..`--viz-5`, `--viz-other`, status colours in core.css), bars at most 24 px with 2 px gaps, a tooltip from `data-tip` on every mark, and a drill-down `href` where given.
- `columns({labels, series: [{label, color, values}], ref: {value, label}, href: (i) => url, fmt})`: stacked columns over time, legend when 2+ series.
- `hbars([{label, value, color, sub, href}], {fmt, max})`: ranked horizontal bars.
- `histogram(bins: [{count, edge, end, tip, href}], markers: [{at, label}])`.
- `split([{label, value, color, href}])`: a 100% bar with a labelled legend.
- `line({labels, values, color, thresholds: [{value, label}], fmt})`.
- `legend`, `bucketLabels(series, n)`, colours `PALETTE` (5 categorical, fixed order), `OTHER`, `KIND` (model / service), `STATUS` (normal / watch / restricted, budget bands).

Colour follows the entity: give each item the same colour in every chart of a page (person page: by rank of the person's own spend, top five then Other).

## API

All under `/admin` (admin token unless demo mode). `period` = days ending with `end` (UTC, `YYYY-MM-DD`, default today), 1..31, default 30. Money is USD. A person's spend and allowance include their agents'. Allowance = the daily caps (`budgets.per_person`, else `per_principal`) of the person and their agents x days; `null` when any of them is uncapped. Bands of used = spend / allowance: `under` < 0.5 <= `half` < 0.8 <= `near` < 1 <= `over`; `nocap`.

### `GET /admin/analytics/overview?period=&end=`

```
{period, first, last,                       // days, "YYYY-MM-DD"
 totals: {usd, usd_models, usd_services, prev_usd|null, budget, people, agents, active,
          decisions, block, redact, warn, watched},
 series: {unit: "day"|"hour", start: epoch, models: [usd...], services: [usd...]},  // hourly = last 24 h when period=1 and no end
 daily_budget,                               // sum of all allowances per day
 items: [{key: "model:<name>"|"service:<key>", kind: "model"|"service", name, requests, tokens, usd}],  // by usd desc
 spend: {histogram: [{lo, hi, count}], p50, p90, p99, top: [{id, name, team, usd}]},  // humans with spend > 0
 budget_bands: {under, half, near, over, nocap},
 risk: {people: {normal, watch, restricted}, agents: {...}, overrides, signals},
 alerts: [{ts, principal, name, level, score, reason, ...}],   // 8 newest
 teams: [{team, people, active, usd, budget, blocks}]}         // by usd desc
```

### `GET /admin/analytics/people`

Params: `q` (id, name or team, case-insensitive), `team`, `risk` (`normal|watch|restricted|elevated|flagged`; flagged = score > 0, an override or a signal), `budget` (band), `item` (only people who used it; fills `item_usd`), `kind` (`human` default, `agent`), `min_usd`, `max_usd` (`min <= usd < max`), `sort` (`usd|used|name|team|risk|score|last|item|blocks`, `-` prefix = descending, default `-usd`; ties by name), `page` (1-based), `per_page` (<= 200, default 50), `period`, `end`. An unknown `risk`/`budget`/`kind`/`sort` is a 400 naming the options.

```
{total, page, pages, per_page, period, first, last, sort, teams: [team...],
 summary: {usd, budget, blocks,                 // over every matched row (all pages); budget sums non-null allowances
           bands: {under, half, near, over, nocap},   // counted with every filter but `budget`
           levels: {normal, watch, restricted}},      // counted with every filter but `risk`
 items: [{key, kind, name, usd}],               // everything with spend in the period, usd desc (for an item filter)
 rows: [{id, name, team, role, owner, agents: n, usd, requests, budget|null, used|null,
         top_item: {key, kind, name, usd}|null, item_usd|null, blocks, redacts, last_seen|null,
         score, level, manual: {level, reason, at}|null, signals: n}]}
```

### `GET /admin/analytics/person/{pid}?period=&end=`

404 for an unknown principal. For an agent, `agents` holds the agent itself.

```
{person: {id, name, team, role, kind, owner, owner_name, cap},
 period, first, last,
 spend: {usd, requests, tokens, budget|null, daily_cap|null, today_usd, today_left|null},
 decisions: {decisions, block, redact, warn},
 series: {unit, start, items: [{key, kind, name}], values: [[usd per bucket]...]},  // top 5 items + "other"
 items: [{key, kind, name, usd, by_principal: {pid: usd}}],     // by usd desc
 agents: [{id, name, usd, cap, level, grants: {rid: {scopes, expires_at, active}}}],
 resources: [{id, name, type, sensitivity, scopes, max_grant_hours, suspended}],  // the owner's entitlement
 risk: {score, level, auto, computed, manual, signals: [{source, level, reason, expires_at, ...}],
        history: [daily max score...], levels: {watch, restricted}},
 alerts: [...], events: [audit events of the person and their agents, newest first, <= 25]}
```

### `GET /admin/analytics/resources?period=&end=`

```
{period, resources: [{id, name, type, service, sensitivity, scopes, suspended, suspended_in_policy,
  usd_per_call, calls, usd, grants: [{agent, owner, scopes, granted_by, granted_at, expires_at, active}]}]}
```

### Actions

| Call | Body | Does |
|---|---|---|
| `POST /admin/grants` | `{agent, resource, scopes, hours}` | grants as the agent's owner could (entitlement, max hours, suspension apply); 400 with the reason, 404 unknown agent |
| `PATCH /admin/grants/{agent}/{rid}` | `{scopes}` | changes an active grant's scopes, keeps its expiry |
| `DELETE /admin/grants/{agent}/{rid}` | | revokes |
| `POST /admin/resources/{rid}/suspend` | `{suspended: bool, reason}` | |
| `POST /admin/risk/{pid}` | `{level: auto\|normal\|watch\|restricted, reason?}` | override |
| `POST /admin/risk/{pid}/reset` | | clears the score |
| `DELETE /admin/risk/{pid}/signal/{source}` | | dismisses an external signal |
| `POST /admin/try` | `{principal, text, direction}` | playground verdict |

Existing reads: `/admin/summary` (controls, threats, budgets today, latency, policy, `demo_mode`), `/admin/events?limit=&action=&control=&principal=&channel=&direction=&q=` (principal matches the actor or the agent's owner; q is a case-insensitive substring of principal, owner, team, tool, model, reason or a finding's control, category or detail), `/admin/alerts`, `/admin/risk`, `/admin/grants`, `/admin/audit/export?format=jsonl|csv|ocsf|ecs`, `/metrics`.

## Data

- `controllayer/analytics.py`: `UsageHistory` (daily buckets for 62 days, hourly for 48 h, per principal and item; decision counts; daily max risk score) fed by budget-ledger and audit listeners, and the aggregations above.
- `controllayer/gateway/console_api.py`: the endpoints above.
- `python -m controllayer.seed --people 2000 --days 30`: writes `data/org.json` (the policy's `identity.directory`), `data/history.json` (read at start) and grants, overrides, signals and a suspension into `data/state.json`.
- Tests: `tests/test_analytics.py` (API), `tests/test_console_js.py` (the scripts in QuickJS against a seeded 2,000-person org; helper `tests/jsconsole.py`).
