"""Reach on the person profile: the three legs of the lethal trifecta and the cheapest single fix."""


def _reach(client, pid):
    return client.get(f"/admin/people/{pid}/reach")


def test_reach_legs_and_trifecta(client):
    alice = _reach(client, "alice").json()
    assert set(alice["legs"]) == {"private", "untrusted", "egress"}
    assert alice["trifecta"] and alice["weakest"] in alice["legs"] and alice["fix"]
    sources = {(s["id"], s["via"]) for s in alice["legs"]["untrusted"]}
    assert ("read_file", "role:developer") in sources and ("zendesk", "entitled") in sources
    carol = _reach(client, "carol").json()  # an intern reads nothing private
    assert carol["legs"]["private"] == [] and not carol["trifecta"] and carol["fix"] is None
    assert _reach(client, "nobody").status_code == 404


def test_reach_fix_names_a_source_that_breaks_it(make_client):
    agent = _reach(make_client(), "alice-coder").json()  # no grants: only its role's tools
    assert agent["kind"] == "agent" and agent["trifecta"]
    assert agent["fix"].startswith("Remove ") and "developer role" in agent["fix"]

    def drop_http_get(d):
        d["tool_access"]["roles"]["developer"].remove("http_get")

    after = _reach(make_client(mutate=drop_http_get), "alice-coder").json()
    assert after["legs"]["egress"] == [] and not after["trifecta"]
