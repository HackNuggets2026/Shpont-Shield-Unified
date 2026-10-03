"""The workflow menu: what each kind of work may use, who may order it, and what it costs per run."""

from __future__ import annotations

from fnmatch import fnmatch

from ..config import Policy, SemanticControl, Workflow
from ..types import Action, Context, Direction, Finding

UNLABELED = "unlabeled"
CLASSIFIER = "_workflow"  # question name; the leading underscore keeps it apart from policy controls


def availability(policy: Policy, wf_name: str, wf: Workflow, principal_id: str, team: str, role: str) -> str | None:
    """Why this principal cannot order the workflow, or None when they can."""
    if not wf.enabled:
        return "disabled by an administrator"
    if wf.teams and team not in wf.teams:
        return f"not available to team {team!r}"
    if wf.roles and role not in wf.roles:
        return f"not available to role {role!r}"
    if wf.approval == "admin" and wf_name not in policy.principal(principal_id).approved_workflows:
        return "needs admin approval"
    return None


def check(ctx: Context, policy: Policy) -> list[Finding]:
    """Gate for declared workflows. A classified guess is only attribution and never refuses."""
    if not ctx.metered or ctx.direction not in (Direction.INPUT, Direction.TOOL_CALL):
        return []
    menu = policy.menu
    if ctx.workflow_source != "declared":
        if menu.require_label and menu.workflows:
            return [_block("unlabeled", "this gateway needs a workflow label (header x-acl-workflow)")]
        return []
    name = ctx.workflow or ""
    wf = menu.workflows.get(name)
    if wf is None:
        return [_block("not_on_menu", f"workflow {name!r} is not on the menu")]
    p = ctx.principal
    why = availability(policy, name, wf, p.id, p.team, p.role)
    if why:
        category = "approval_required" if why.startswith("needs") else "not_available"
        return [_block(category, f"workflow {name!r}: {why}")]
    if ctx.direction is Direction.INPUT and ctx.model and wf.models:
        if not any(fnmatch(ctx.model, pat) for pat in wf.models):
            return [_block("model_not_in_workflow", f"workflow {name!r} does not use model {ctx.model!r}")]
    # A tool behind an access grant is decided by its grant (scoped to a workflow or not), not the tool list.
    if ctx.direction is Direction.TOOL_CALL and ctx.tool and wf.tools and not policy.grant_resources(ctx.tool):
        # Access-grant resources the workflow includes bring their tools with them.
        included = [t for r in wf.resources if r in policy.catalog for t in policy.catalog[r].tools]
        allowed = any(fnmatch(ctx.tool, pat) for pat in [*wf.tools, *included])
        if not allowed and not policy.covering_grant(p.id, ctx.tool, name):
            return [_block("tool_not_in_workflow", f"workflow {name!r} does not use tool {ctx.tool!r}")]
    return []


def classifier(policy: Policy) -> SemanticControl | None:
    """The question that attributes an unlabeled prompt to a workflow, asked alongside the policy questions."""
    wfs = policy.menu.workflows
    if not (policy.menu.classify_unlabeled and wfs):
        return None
    criteria = {"other": "none of the listed kinds of work"}
    criteria |= {n: w.description or n.replace("_", " ") for n, w in list(wfs.items())[:25]}
    return SemanticControl(
        type="choice",
        mode=Action.ALLOW,
        directions=[Direction.INPUT],
        instructions="Which kind of work is this request part of?",
        criteria=criteria,
        option_keywords={n: w.keywords for n, w in wfs.items() if n in criteria and w.keywords},
    )


def _block(category: str, detail: str) -> Finding:
    return Finding(control="workflow", category=category, action=Action.BLOCK, proposed=Action.BLOCK, detail=detail)
