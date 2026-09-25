"""Microsoft Fabric Data Lineage Explorer - Streamlit front end.

Talks to the FastAPI backend (backend/). Run:
    streamlit run streamlit_app/app.py
"""
from __future__ import annotations

import json
import time

import pandas as pd
import streamlit as st

import api
from api import ApiError, enc, show_error
from graph import LEGEND, render

st.set_page_config(page_title="Fabric Data Lineage Explorer", page_icon="🔀", layout="wide")

CONF_BADGE = {"Confirmed": "🟢", "Parsed": "🔵", "Inferred": "🟠", "Manual": "🟣", "Unavailable": "🔴"}
NODE_TYPES = ["ExternalSource", "SourceTable", "SourceColumn", "Pipeline", "Dataflow", "Notebook", "Lakehouse",
              "Warehouse", "Table", "Column", "SemanticModel", "SemanticModelTable", "SemanticModelColumn", "Measure",
              "Report", "ReportPage", "Visual", "Unresolved"]
ROLE_RANK = {"Viewer": 1, "LineageAnalyst": 2, "MetadataAdmin": 3, "AppAdmin": 4}


def conf(c: str | None) -> str:
    return f"{CONF_BADGE.get(c or '', '⚪')} {c or '-'}"


@st.cache_data(ttl=300, show_spinner=False)
def me() -> dict:
    return api.get("/api/me")


@st.cache_data(ttl=120, show_spinner=False)
def workspaces() -> list[dict]:
    return api.get("/api/workspaces")["value"]


def has_role(role: str) -> bool:
    try:
        return ROLE_RANK.get(me().get("maxRole") or "", 0) >= ROLE_RANK[role]
    except ApiError:
        return False


def open_lineage(key: str, direction: str = "both", level: str = "table"):
    st.session_state.update(root=key, direction=direction, level=level, extra_nodes={}, extra_edges={})
    st.switch_page(PAGES["lineage"])


# ------------------------------------------------------------------ pages
def page_analyze():
    st.title("Analyze lineage")
    st.caption("Select a workspace and a semantic model or report to trace lineage from source systems through "
               "Bronze, Silver and Gold to measures, pages and visuals.")
    try:
        wss = workspaces()
    except ApiError as e:
        return show_error(e)
    labels = {w["id"]: f"{w['name']}{'  · SAMPLE' if w.get('isSample') else ''}{'  · scanned' if w.get('scanned') else ''}"
              for w in wss}
    c1, c2 = st.columns([2, 1])
    ws_id = c1.selectbox("Workspace (type to search)", options=list(labels), format_func=labels.get, index=None,
                         placeholder="Start typing… e.g. Enterprise Sales")
    ws_free = c2.text_input("…or workspace name / ID", placeholder="paste an ID")
    item_type = st.radio("Item type", ["SemanticModel", "Report"], horizontal=True,
                         format_func=lambda t: "Semantic model" if t == "SemanticModel" else "Report")
    items = []
    target_ws = ws_id or ws_free.strip()
    if ws_id:
        try:
            items = api.get(f"/api/workspaces/{ws_id}/items", type=item_type)["value"]
        except ApiError as e:
            show_error(e)
    c3, c4 = st.columns([2, 1])
    item = c3.selectbox("Report or semantic-model name", options=items, index=None,
                        format_func=lambda i: f"{i['name']}  ({i['id'][:8]}…){'  · scanned' if i['scanned'] else ''}",
                        placeholder="Select a workspace first" if not ws_id else f"{len(items)} available")
    item_name_free = c3.text_input("…or type the name", disabled=bool(item))
    item_id = c4.text_input("Item ID (optional, takes precedence)")
    b1, b2 = st.columns([1, 5])
    if b1.button("Analyze Lineage", type="primary", disabled=not target_ws):
        try:
            with st.spinner("Resolving workspace and item…"):
                r = api.post("/api/lineage/analyze", json={
                    "workspace": target_ws, "itemType": item_type, "itemId": item_id or (item or {}).get("id"),
                    "name": (item or {}).get("name") or item_name_free})
            if r["status"] == "ready":
                open_lineage(r["rootKey"])
            else:
                st.session_state.update(scan_id=r["scanRunId"], then=r["rootKey"])
                st.switch_page(PAGES["refresh"])
        except ApiError as e:
            show_error(e)
    if b2.button("Upload Metadata"):
        st.session_state.upload_ws = ws_id
        st.switch_page(PAGES["upload"])

    scanned = [w for w in wss if w.get("scanned")]
    if scanned:
        st.subheader("Scanned workspaces")
        st.dataframe(pd.DataFrame([{"Workspace": w["name"], "Sample": "SAMPLE DATA" if w.get("isSample") else "",
                                    "Last scanned": w.get("lastScannedAt")} for w in scanned]), hide_index=True)


def page_workspaces():
    st.title("Workspace explorer")
    try:
        wss = workspaces()
    except ApiError as e:
        return show_error(e)
    names = {w["id"]: w["name"] + (" (SAMPLE)" if w.get("isSample") else "") for w in wss}
    ws_id = st.selectbox("Workspace", list(names), format_func=names.get, index=0 if wss else None)
    if not ws_id:
        return
    w = next(x for x in wss if x["id"] == ws_id)
    c = st.columns(3)
    c[0].metric("Workspace ID", w["id"][:13] + "…")
    c[1].metric("Capacity", w.get("capacityId") or "-")
    c[2].metric("Last scanned", (w.get("lastScannedAt") or "never")[:16])
    try:
        s = api.get(f"/api/workspaces/{ws_id}/summary")
        st.write("**Edges by confidence:** " + " · ".join(f"{conf(k)}: {v}" for k, v in s["edgesByConfidence"].items()))
        if s["notCollected"]:
            st.warning("Metadata that could not be collected in the last scan")
            st.dataframe(pd.DataFrame(s["notCollected"]), hide_index=True, use_container_width=True)
    except ApiError as e:
        if e.status != 404:
            show_error(e)
    try:
        items = api.get(f"/api/workspaces/{ws_id}/items")["value"]
    except ApiError as e:
        return show_error(e)
    df = pd.DataFrame([{"Name": i["name"], "Type": i["type"], "Layer": i.get("layer") or "",
                        "Definition": f"{i.get('definitionStatus') or 'not scanned'} {i.get('definitionFormat') or ''}",
                        "ID": i["id"]} for i in items])
    st.dataframe(df, hide_index=True, use_container_width=True)
    pick = st.selectbox("Open lineage for", [i for i in items if i["scanned"]], format_func=lambda i: f"{i['type']}: {i['name']}",
                        index=None)
    if pick and st.button("Open lineage graph", type="primary"):
        open_lineage(pick["nodeKey"])


def _merged_graph(root: str, direction: str, level: str, depth: int) -> dict:
    g = api.get("/api/lineage/graph", root=root, direction=direction, level=level, depth=depth, maxNodes=400)
    nodes = {n["key"]: n for n in g["nodes"]}
    edges = {e["id"]: e for e in g["edges"]}
    nodes.update(st.session_state.get("extra_nodes", {}))
    edges.update(st.session_state.get("extra_edges", {}))
    g["nodes"], g["edges"] = list(nodes.values()), list(edges.values())
    return g


def node_detail(key: str):
    try:
        d = api.get(f"/api/lineage/nodes/{enc(key)}")
    except ApiError as e:
        return show_error(e)
    n, c = d["node"], d["catalog"]
    st.subheader(f"{n['type']} · {n['name']}" + ("  🔖 SAMPLE DATA" if d.get("isSample") else ""))
    st.caption(" › ".join(b["name"] for b in d["breadcrumb"]) or n.get("qualifiedName", ""))
    b = st.columns(3)
    if b[0].button("Trace to source", key=f"up{key}"):
        open_lineage(key, "up", "column")
    if b[1].button("Forward impact", key=f"dn{key}"):
        open_lineage(key, "down", "column")
    if b[2].button("Impact analysis", key=f"im{key}"):
        st.session_state.impact_node = key
        st.switch_page(PAGES["impact"])
    t1, t2, t3, t4 = st.tabs(["Summary", "Definition", "Transformations", "Metadata"])
    with t1:
        if c.get("expression"):
            st.code(c["expression"], language="sql")
        if d["referenced"]:
            st.write("**Referenced columns / measures:** " + ", ".join(r.get("qualifiedName") or r["key"]
                                                                          for r in d["referenced"]))
        up = d["upstream"]
        rows = [(label, ", ".join(x.get("qualifiedName") or x["name"] for x in up[k]))
                for k, label in [("goldTables", "Gold"), ("silverTables", "Silver"), ("bronzeTables", "Bronze"),
                                 ("otherTables", "Other tables"), ("processes", "Pipelines / notebooks / dataflows"),
                                 ("sourceTables", "Source tables"), ("sourceSystems", "Source systems")] if up.get(k)]
        if rows:
            st.write("**Upstream**")
            st.table(pd.DataFrame(rows, columns=["Layer", "Objects"]).set_index("Layer"))
        use = d["downstream"]["visualUsage"]
        if use:
            st.write("**Used by reports / visuals**")
            st.dataframe(pd.DataFrame([{"Report": (u["report"] or {}).get("name"), "Page": (u["page"] or {}).get("name"),
                                        "Visual": u["visual"]["name"],
                                        "Usage": ", ".join(sorted({x.get("usage") or "" for x in u["usages"]}))}
                                       for u in use]), hide_index=True, use_container_width=True)
        lc = d["lineageConfidence"]
        st.write(f"**Lineage confidence (weakest upstream link):** {conf(lc.get('upstreamWeakest') or 'Confirmed')}")
        if lc["partialPaths"]:
            with st.expander(f"{len(lc['partialPaths'])} inferred / unavailable link(s)"):
                st.dataframe(pd.DataFrame(lc["partialPaths"]), hide_index=True)
        if d["unresolved"]:
            st.error("Unresolved dependencies: " + "; ".join(f"{u.get('name') or u['key']}: "
                                                            f"{u.get('reason') or (u.get('properties') or {}).get('reason', '')}"
                                                            for u in d["unresolved"]))
        st.caption(f"Last metadata refresh: {d.get('lastMetadataRefresh') or 'never'}")
    with t2:
        for p in c.get("partitions", []) or []:
            st.write(f"**Partition {p['name']}** - {p.get('mode') or p['sourceType']}")
            if p.get("entityName"):
                st.write(f"Direct Lake entity `{p.get('schemaName') or 'dbo'}.{p['entityName']}` via `{p['expressionSource']}`")
            else:
                st.code(p.get("expression") or "", language="powerquery")
        for k in ("columns", "measures", "relationships", "hierarchies", "roles", "calculationItems", "fields"):
            if c.get(k):
                st.write(f"**{k[0].upper() + k[1:]}**")
                st.dataframe(pd.DataFrame(c[k]), hide_index=True, use_container_width=True)
        if not any(c.get(k) for k in ("partitions", "columns", "measures", "fields", "expression")):
            st.info("No definition details for this object type.")
    with t3:
        if not d["transformations"]:
            st.info("No transformation recorded on incoming or outgoing relationships.")
        for t in d["transformations"]:
            st.write(f"**{t['kind']} · {t['name']}**" + (f"  _({t['sourceRef']})_" if t.get("sourceRef") else ""))
            if t.get("expression"):
                st.code(t["expression"], language="sql")
            det = {k: v for k, v in (t.get("details") or {}).items() if v not in (None, [], {}, "")}
            if det:
                with st.expander("Details (mappings, joins, filters, derived columns, aggregations)"):
                    st.json(det)
        st.write("**Incoming relationships**")
        if d["incoming"]:
            st.dataframe(pd.DataFrame([{"From": e["source"], "Confidence": conf(e["confidence"]), "Status": e["status"],
                                        "Evidence": e["evidenceSource"], "Method": e["extractionMethod"],
                                        "Expression": e.get("transformationExpression"), "Reason": e.get("reason")}
                                       for e in d["incoming"]]), hide_index=True, use_container_width=True)
    with t4:
        st.json({"node": n, "catalog": c})


def page_lineage():
    st.title("Lineage graph")
    root = st.session_state.get("root") or st.query_params.get("root")
    q = st.text_input("Find an object to start from (table, column, measure, report…)")
    if q:
        try:
            hits = api.get("/api/search", q=q, limit=25)["value"]
            pick = st.selectbox("Matches", hits, format_func=lambda n: f"{n['type']}: {n.get('qualifiedName') or n['name']}")
            if pick and st.button("Show lineage"):
                open_lineage(pick["key"])
        except ApiError as e:
            show_error(e)
    if not root:
        st.info("Pick an item on **Analyze lineage**, or search above.")
        return
    st.query_params["root"] = root
    c = st.columns([1.2, 1.2, 1, 1, 1.6])
    view = c[0].radio("View", ["Source → report", "Backtrack (right → left)"],
                      index=1 if st.session_state.get("direction") == "up" else 0)
    direction = c[1].selectbox("Direction", ["both", "up", "down"],
                               index=["both", "up", "down"].index(st.session_state.get("direction", "both")),
                               format_func={"both": "Both", "up": "Upstream", "down": "Downstream"}.get)
    level = c[2].selectbox("Level", ["item", "table", "column"],
                           index=["item", "table", "column"].index(st.session_state.get("level", "table")))
    depth = c[3].slider("Depth", 1, 25, 25 if direction != "both" else 4)
    highlight = c[4].text_input("Search & highlight")
    with st.expander("Filters"):
        f1, f2, f3 = st.columns(3)
        types = f1.multiselect("Item types", NODE_TYPES)
        layers = f2.multiselect("Layers", ["Bronze", "Silver", "Gold"])
        confs = f3.multiselect("Confidence", list(CONF_BADGE), default=list(CONF_BADGE))
    try:
        g = _merged_graph(root, direction, level, depth)
    except ApiError as e:
        return show_error(e)
    keys = {n["key"] for n in g["nodes"] if (not types or n["type"] in types) and
            (not layers or not n.get("layer") or n["layer"] in layers)}
    view_g = {"nodes": [n for n in g["nodes"] if n["key"] in keys],
              "edges": [e for e in g["edges"] if e["source"] in keys and e["target"] in keys and e["confidence"] in confs]}
    s = g["summary"]
    st.write(" · ".join(f"{conf(k)}: {v}" for k, v in s["edgesByConfidence"].items()) +
             (f" · 🔴 {s['brokenOrUnresolved']} broken / unresolved" if s["brokenOrUnresolved"] else "") +
             ("  ·  ⚠️ partial view - load more below" if g["truncated"] else ""))
    render(view_g, "RL" if view.startswith("Backtrack") else "LR", height=640, highlight=highlight)
    st.markdown(LEGEND, unsafe_allow_html=True)

    e1, e2, e3, e4 = st.columns(4)
    e1.download_button("Export view (JSON)", json.dumps(view_g, indent=2), "lineage.json", "application/json")
    rows = [{"source": e["source"], "target": e["target"], "relationship": e["relationshipType"],
             "level": e["objectLevel"], "confidence": e["confidence"], "status": e["status"],
             "evidence": e["evidenceSource"], "method": e["extractionMethod"], "reason": e.get("reason"),
             "expression": e.get("transformationExpression")} for e in view_g["edges"]]
    e2.download_button("Export view (CSV)", pd.DataFrame(rows).to_csv(index=False), "lineage.csv", "text/csv")
    more = [n for n in view_g["nodes"] if n.get("hasMore")]
    if more:
        m = e3.selectbox("Load more around", more, format_func=lambda n: n["name"], index=None, label_visibility="collapsed",
                         placeholder="＋ Load more around…")
        if m:
            extra = api.get("/api/lineage/graph", root=m["key"], direction=direction, level=level, depth=2, maxNodes=200)
            st.session_state.setdefault("extra_nodes", {}).update({n["key"]: n for n in extra["nodes"]})
            st.session_state.setdefault("extra_edges", {}).update({e["id"]: e for e in extra["edges"]})
            st.session_state.extra_nodes[m["key"]] = {**m, "hasMore": False}
            st.rerun()
    e4.caption("PNG: use the camera / right-click in the graph, or your browser's print-to-PDF for PDF.")

    st.divider()
    sel = st.selectbox("Select a node for details", sorted(view_g["nodes"], key=lambda n: (n["type"], n["name"])),
                       format_func=lambda n: f"{n['type']}: {n.get('qualifiedName') or n['name']}",
                       index=next((i for i, n in enumerate(sorted(view_g["nodes"], key=lambda n: (n["type"], n["name"])))
                                   if n["key"] == root), None))
    if sel:
        node_detail(sel["key"])
    with st.expander("Relationships in this view (click-free edge inspection)"):
        names = {n["key"]: n.get("qualifiedName") or n["name"] for n in view_g["nodes"]}
        st.dataframe(pd.DataFrame([{**r, "source": names.get(r["source"], r["source"]),
                                    "target": names.get(r["target"], r["target"]),
                                    "confidence": conf(r["confidence"])} for r in rows]),
                     hide_index=True, use_container_width=True)


def page_details():
    st.title("Table · column · measure details")
    q = st.text_input("Search", placeholder="e.g. Total Sales, SalesAmount, Gold.dbo.sales")
    if not q:
        return
    try:
        hits = api.get("/api/search", q=q, limit=30,
                       types="Table,SourceTable,SemanticModelTable,Column,SourceColumn,SemanticModelColumn,Measure")["value"]
    except ApiError as e:
        return show_error(e)
    pick = st.selectbox("Object", hits, format_func=lambda n: f"{n['type']}: {n.get('qualifiedName') or n['name']}")
    if pick:
        node_detail(pick["key"])


def page_impact():
    st.title("Impact analysis")
    try:
        qs = api.get("/api/lineage/impact/questions")["value"]
    except ApiError as e:
        return show_error(e)
    qmap = {x["id"]: x["label"] for x in qs}
    question = st.selectbox("Question", list(qmap), format_func=qmap.get,
                            index=list(qmap).index("rename_remove"))
    params = {"question": question}
    if question in ("incomplete", "unused"):
        wss = workspaces()
        params["workspaceId"] = st.selectbox("Workspace", [w["id"] for w in wss],
                                             format_func={w["id"]: w["name"] for w in wss}.get)
    else:
        node_key = st.session_state.get("impact_node")
        q = st.text_input("Object", placeholder="Search a table, column, measure or source")
        if q:
            hits = api.get("/api/search", q=q, limit=25)["value"]
            pick = st.selectbox("Matches", hits, format_func=lambda n: f"{n['type']}: {n.get('qualifiedName') or n['name']}")
            node_key = pick["key"] if pick else None
        if not node_key:
            return st.info("Search for an object.")
        st.caption(f"Object: `{node_key}`")
        params["nodeKey"] = node_key
    try:
        r = api.get("/api/lineage/impact", **params)
    except ApiError as e:
        return show_error(e)
    st.subheader(f"{r['question']} - {len(r['rows'])} result(s)")
    if r["rows"]:
        df = pd.DataFrame(r["rows"]).drop(columns=[c for c in ("details", "expression") if c in r["rows"][0]], errors="ignore")
        flt = st.text_input("Filter results")
        if flt:
            df = df[df.astype(str).apply(lambda row: row.str.contains(flt, case=False)).any(axis=1)]
        st.dataframe(df.astype(str), hide_index=True, use_container_width=True)
        st.download_button("Download CSV", df.to_csv(index=False), f"impact-{question}.csv", "text/csv")
    if r.get("graph"):
        st.subheader("Dependency graph")
        render(r["graph"], "RL" if question in ("source_tables", "transformation") else "LR", height=520)


def page_upload():
    st.title("Upload metadata")
    if not has_role("LineageAnalyst"):
        return st.warning("Uploading requires the Lineage Analyst role.")
    wss = workspaces()
    opts = [""] + [w["id"] for w in wss]
    names = {"": "All accessible workspaces", **{w["id"]: w["name"] for w in wss}}
    pre = st.session_state.get("upload_ws") or ""
    ws = st.selectbox("Compare with workspace", opts, format_func=names.get, index=opts.index(pre) if pre in opts else 0)
    files = st.file_uploader("Drag and drop files here", accept_multiple_files=True,
                             type=["json", "csv", "xlsx", "xml", "yaml", "yml", "tmdl", "bim", "tmsl", "pbip", "zip", "sql",
                                   "pq", "m", "ipynb", "py", "md", "txt", "docx"],
                             help="Files are validated and virus-scanned before parsing. Max 25 MB.")
    if files and st.button("Upload and analyze", type="primary"):
        for f in files:
            try:
                d = api.post("/api/uploads", files={"file": (f.name, f.getvalue(), f.type or "application/octet-stream")},
                             data={"compareWorkspaceId": ws} if ws else None)
                st.session_state.doc = d["id"]
                st.success(f"{f.name}: {d['status']}")
            except ApiError as e:
                show_error(e)
    try:
        docs = api.get("/api/uploads")["value"]
    except ApiError as e:
        return show_error(e)
    if not docs:
        return
    st.subheader("Documents")
    st.dataframe(pd.DataFrame([{"File": d["fileName"], "Version": d["version"], "Status": d["status"],
                                "Uploader": d["uploadedBy"], "Uploaded": d["uploadedAt"][:19],
                                "Security scan": d.get("scanResult")} for d in docs]), hide_index=True, use_container_width=True)
    ids = [d["id"] for d in docs]
    cur = st.session_state.get("doc")
    doc_id = st.selectbox("Review document", ids, format_func={d["id"]: f"{d['fileName']} v{d['version']}" for d in docs}.get,
                          index=ids.index(cur) if cur in ids else 0)
    doc = next(d for d in docs if d["id"] == doc_id)
    st.write("Extracted: " + ", ".join(f"{v} {k}" for k, v in doc["counts"].items() if v) or "nothing")
    for e in doc["errors"]:
        st.error(e)
    for u in doc["unsupportedSections"]:
        st.warning(u)
    c1, c2 = st.columns(2)
    c1.download_button("Extracted lineage (JSON)", api.raw(f"/api/uploads/{doc_id}/export", format="json"),
                       f"{doc['fileName']}.lineage.json")
    c2.download_button("Extracted lineage (CSV)", api.raw(f"/api/uploads/{doc_id}/export", format="csv"),
                       f"{doc['fileName']}.lineage.csv")
    maps = api.get(f"/api/uploads/{doc_id}/mappings")
    st.subheader("Comparison with live metadata")
    s = maps["summary"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Matched", s.get("matched", 0))
    m2.metric("Ambiguous", s.get("ambiguous", 0))
    m3.metric("Unmatched", s.get("unmatched", 0))
    st.caption("Nothing is merged automatically. Approving a mapping adds a **Manual** lineage relationship.")
    for m in maps["value"]:
        icon = {"matched": "✅", "ambiguous": "⚠️", "unmatched": "❌"}[m["matchStatus"]]
        with st.expander(f"{icon} [{m['kind']}] {m['name']} - {m['decision']}"):
            p = m["payload"]
            if p.get("transformation"):
                st.write(f"Transformation: `{p['transformation']}`")
            if m["decision"] != "pending":
                st.write(f"Decision: **{m['decision']}** by {m.get('decidedBy')}")
                continue
            tgt_opts = p.get("targetCandidates") or m["candidates"]
            tgt = st.selectbox("Target object", tgt_opts, key=f"t{m['id']}",
                               index=tgt_opts.index(p["targetSuggested"]) if p.get("targetSuggested") in tgt_opts else None)
            a, r = st.columns(2)
            if a.button("Approve", key=f"a{m['id']}", disabled=m["kind"] == "mapping" and not tgt):
                try:
                    api.post(f"/api/uploads/mappings/{m['id']}/decision", json={"approve": True, "targetKey": tgt})
                    st.rerun()
                except ApiError as e:
                    show_error(e)
            if r.button("Reject", key=f"r{m['id']}"):
                api.post(f"/api/uploads/mappings/{m['id']}/decision", json={"approve": False})
                st.rerun()


def page_refresh():
    st.title("Metadata refresh")
    wss = [w for w in workspaces() if not w.get("isSample")]
    with st.form("scan"):
        chosen = st.multiselect("Workspaces", [w["id"] for w in wss], format_func={w["id"]: w["name"] for w in wss}.get)
        c1, c2 = st.columns(2)
        mode = c1.selectbox("Mode", ["full", "incremental"])
        sp_only = bool(me().get("isLocalDev"))
        ident = c2.selectbox("Identity", ["service_principal"] if sp_only else ["delegated", "service_principal"],
                             help="Scans run as the Fabric service principal configured in Secrets." if sp_only else
                             "Service-principal scans need the Metadata Administrator role.")
        if st.form_submit_button("Start scan", type="primary") and chosen:
            try:
                r = api.post("/api/scans", json={"workspaceIds": chosen, "mode": mode, "identity": ident})
                st.session_state.scan_id = r["id"]
            except ApiError as e:
                show_error(e)
    scan_id = st.session_state.get("scan_id")
    if scan_id:
        try:
            s = api.get(f"/api/scans/{scan_id}")
        except ApiError as e:
            return show_error(e)
        st.subheader(f"Scan {scan_id[:8]} - {s['status']}")
        if s["status"] in ("Queued", "Running"):
            st.info("Scanning… this page refreshes every 3 seconds.")
            time.sleep(3)
            st.rerun()
        if s.get("error"):
            st.error(s["error"])
        if s["status"] in ("Succeeded", "PartiallySucceeded") and st.session_state.get("then"):
            if st.button("Open lineage", type="primary"):
                open_lineage(st.session_state.pop("then"))
        st.write("**Metadata that could not be collected**")
        st.dataframe(pd.DataFrame(s.get("issues") or [{"message": "Everything requested was collected."}]),
                     hide_index=True, use_container_width=True)
        if s.get("apiFailures"):
            st.write("**Failed API calls**")
            st.dataframe(pd.DataFrame(s["apiFailures"]), hide_index=True, use_container_width=True)
    st.subheader("Scan history")
    hist = api.get("/api/scans")["value"]
    if hist:
        st.dataframe(pd.DataFrame([{"Started": h.get("createdAt", "")[:19], "Workspaces": len(h["workspaceIds"]),
                                    "Mode": h["mode"], "Identity": h["identityType"], "By": h.get("requestedBy"),
                                    "Status": h["status"], "ID": h["id"]} for h in hist]), hide_index=True,
                     use_container_width=True)


def page_admin():
    st.title("Administration & configuration")
    if not has_role("MetadataAdmin"):
        return st.warning("Requires the Metadata Administrator or Application Administrator role.")
    try:
        cfg = api.get("/api/admin/config")
    except ApiError as e:
        return show_error(e)
    st.subheader("Environment")
    st.dataframe(pd.DataFrame([{"Setting": k, "Value": str(v)} for k, v in cfg.items() if k not in ("retention", "settings")]),
                 hide_index=True)
    if st.button("Run tenant / permission checks"):
        for ch in api.get("/api/admin/diagnostics")["checks"]:
            (st.success if ch["ok"] else st.error)(f"**{ch['name']}** - {ch['message']}")
    admin = has_role("AppAdmin")
    st.subheader("Metadata retention (days)")
    r = cfg["retention"]
    c = st.columns(4)
    vals = {k: c[i].number_input(k, value=int(v), min_value=1, disabled=not admin) for i, (k, v) in enumerate(r.items())}
    if c[3].button("Save", disabled=not admin):
        api.put("/api/admin/config/retention", json=vals)
        st.success("Saved")
    if admin:
        st.subheader("SAMPLE data (demonstration only)")
        s1, s2 = st.columns(2)
        if s1.button("Load SAMPLE workspace"):
            st.write(api.post("/api/admin/sample"))
            workspaces.clear()
        if s2.button("Remove SAMPLE data"):
            api.delete("/api/admin/sample")
            workspaces.clear()
            st.success("Removed")
        st.subheader("Audit log")
        st.dataframe(pd.DataFrame(api.get("/api/admin/audit")["value"]), hide_index=True, use_container_width=True)


# ------------------------------------------------------------------ shell
if not api.sign_in_widget():
    st.stop()

PAGES = {
    "analyze": st.Page(page_analyze, title="Analyze lineage", icon="🔍", default=True),
    "workspaces": st.Page(page_workspaces, title="Workspace explorer", icon="🗂️", url_path="workspaces"),
    "lineage": st.Page(page_lineage, title="Lineage graph", icon="🔀", url_path="lineage"),
    "details": st.Page(page_details, title="Table / column / measure", icon="📋", url_path="details"),
    "impact": st.Page(page_impact, title="Impact analysis", icon="⚠️", url_path="impact"),
    "upload": st.Page(page_upload, title="Upload metadata", icon="📤", url_path="upload"),
    "refresh": st.Page(page_refresh, title="Metadata refresh", icon="🔄", url_path="refresh"),
    "admin": st.Page(page_admin, title="Administration", icon="⚙️", url_path="admin"),
}

with st.sidebar:
    st.markdown("### 🔀 Fabric Data Lineage Explorer")
    try:
        u = me()
        if u.get("isLocalDev"):
            st.caption(f"Developer · **{api.cfg('DEVELOPER_NAME', 'Vishwa Gajjala')}**")
        else:
            st.caption(f"{u.get('name') or u.get('upn')} · **{u.get('maxRole')}**")
        if u.get("fabricConnected"):
            st.success("🟢 Connected to Microsoft Fabric")
        else:
            st.info("SAMPLE data only - add Fabric credentials in Secrets to connect your tenant.")
    except ApiError as e:
        show_error(e)
        st.stop()

st.navigation(list(PAGES.values())).run()
