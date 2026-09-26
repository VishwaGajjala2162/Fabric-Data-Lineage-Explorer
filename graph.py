"""Interactive lineage graph rendered with pyvis (vis-network) inside Streamlit."""
from __future__ import annotations

import html as _html
import json
import re
from functools import lru_cache
from pathlib import Path

import pyvis
import streamlit.components.v1 as components
from pyvis.network import Network

NODE_COLOR = {
    "ExternalSource": "#5c2e91", "SourceDatabase": "#5c2e91", "SourceTable": "#8764b8", "SourceColumn": "#a58bd1",
    "Pipeline": "#0f6cbd", "Dataflow": "#0e7a0d", "Notebook": "#c19c00", "Lakehouse": "#117865",
    "Warehouse": "#005b70", "Table": "#038387", "Column": "#4fa3a5", "SemanticModel": "#ca5010",
    "SemanticModelTable": "#da7b3a", "SemanticModelColumn": "#e8a06a", "Measure": "#b146c2", "Report": "#c50f1f",
    "ReportPage": "#d13438", "Visual": "#e3565b", "Unresolved": "#8a8886",
}
NODE_SHAPE = {"ExternalSource": "database", "SourceDatabase": "database", "Pipeline": "hexagon", "Dataflow": "hexagon",
              "Notebook": "hexagon", "Lakehouse": "diamond", "Warehouse": "diamond", "SemanticModel": "star",
              "Measure": "triangle", "Report": "square", "Unresolved": "text"}
CONF_COLOR = {"Confirmed": "#107c10", "Parsed": "#0f6cbd", "Inferred": "#bc4b09", "Manual": "#6b69d6",
              "Unavailable": "#a4262c"}
LAYER_BORDER = {"Bronze": "#a0522d", "Silver": "#8a8a8a", "Gold": "#c9a227"}


def _levels(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    """Longest-path layering (sources left). Back edges of cycles (possible after table-level roll-up) are ignored."""
    keys = [n["key"] for n in nodes]
    out: dict[str, list[str]] = {k: [] for k in keys}
    for e in edges:
        if e["source"] in out and e["target"] in out and e["source"] != e["target"]:
            out[e["source"]].append(e["target"])
    state: dict[str, int] = {}  # 1 = on stack, 2 = done
    dag: dict[str, list[str]] = {k: [] for k in keys}
    for root in keys:
        if root in state:
            continue
        stack = [(root, iter(out[root]))]
        state[root] = 1
        while stack:
            k, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[k] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                continue  # back edge -> cycle, skip for layout
            else:
                dag[k].append(nxt)
                if nxt not in state:
                    state[nxt] = 1
                    stack.append((nxt, iter(out[nxt])))
    indeg = {k: 0 for k in keys}
    for k in keys:
        for t in dag[k]:
            indeg[t] += 1
    level = {k: 0 for k in keys}
    queue = [k for k in keys if indeg[k] == 0]
    while queue:
        k = queue.pop()
        for t in dag[k]:
            level[t] = max(level[t], level[k] + 1)
            indeg[t] -= 1
            if indeg[t] == 0:
                queue.append(t)
    return level


def render(g: dict, rankdir: str = "LR", height: int = 620, highlight: str = "") -> None:
    levels = _levels(g["nodes"], g["edges"])
    net = Network(height=f"{height}px", width="100%", directed=True, bgcolor="#ffffff", cdn_resources="in_line")
    for n in g["nodes"]:
        label = n["name"] if len(n["name"]) < 34 else n["name"][:32] + "…"
        tip = (f"{n['type']}\n{n.get('qualifiedName') or n['name']}"
               f"{chr(10) + 'Layer: ' + n['layer'] if n.get('layer') else ''}"
               f"{chr(10) + 'SAMPLE DATA' if n.get('isSample') else ''}"
               f"{chr(10) + '＋ more lineage available' if n.get('hasMore') else ''}")
        hit = highlight and highlight.lower() in (n.get("qualifiedName") or n["name"]).lower()
        net.add_node(n["key"], level=levels.get(n["key"], 0), label=("＋ " if n.get("hasMore") else "") + label, title=tip,
                     shape=NODE_SHAPE.get(n["type"], "box"),
                     color={"background": NODE_COLOR.get(n["type"], "#605e5c"),
                            "border": "#ffd335" if hit else LAYER_BORDER.get(n.get("layer") or "", "#444")},
                     borderWidth=5 if hit else (3 if n.get("layer") else 1),
                     font={"color": "#fff" if NODE_SHAPE.get(n["type"], "box") == "box" else "#222", "size": 15},
                     widthConstraint={"maximum": 180})
    for e in g["edges"]:
        broken = e["status"] != "Active" or e["confidence"] == "Unavailable"
        dashes = [2, 4] if broken else [8, 4] if e["confidence"] == "Inferred" else \
            [10, 4, 2, 4] if e.get("isCrossWorkspace") else False
        color = "#a4262c" if broken else "#8764b8" if e.get("isCrossWorkspace") else CONF_COLOR.get(e["confidence"],
                                                                                                    "#666")
        width = 3 if e["relationshipType"] == "transformation" else 1.5
        label = f"{e['rolledUp']} links" if e.get("rolledUp", 0) > 1 else (e["status"] if broken else "")
        net.add_edge(e["source"], e["target"], color=color, dashes=dashes, width=width, label=label,
                     title=f"{e['confidence']} · {e['relationshipType']} · {e['objectLevel']}\n"
                           f"{e['evidenceSource']} - {e['extractionMethod']}"
                           f"{chr(10) + e['reason'] if e.get('reason') else ''}", font={"size": 9})
    net.set_options(f"""{{
      "layout": {{"hierarchical": {{"enabled": true, "direction": "{rankdir}", "sortMethod": "directed",
                                    "levelSeparation": 210, "nodeSpacing": 60, "treeSpacing": 80,
                                    "blockShifting": true, "edgeMinimization": true, "parentCentralization": true}}}},
      "physics": {{"enabled": false}},
      "interaction": {{"hover": true, "navigationButtons": true, "keyboard": true, "tooltipDelay": 150}},
      "edges": {{"arrows": {{"to": {{"enabled": true, "scaleFactor": 0.6}}}}, "smooth": {{"type": "cubicBezier"}}}}
    }}""")
    html = net.generate_html()
    # vis-network is embedded inline (works offline / behind proxies); drop pyvis' unused bootstrap CDN tags
    html = re.sub(r'<(script|link)[^>]+cdn\.jsdelivr\.net/npm/bootstrap[^>]*>(</script>)?', "", html)
    components.html(html, height=height + 20, scrolling=False)


LEGEND = ("**Edges:** <span style='color:#107c10'>━ Confirmed</span> · <span style='color:#0f6cbd'>━ Parsed</span> · "
          "<span style='color:#bc4b09'>╌ Inferred</span> · <span style='color:#6b69d6'>━ Manual</span> · "
          "<span style='color:#8764b8'>─·─ Cross-workspace</span> · <span style='color:#a4262c'>··· Unavailable / "
          "broken</span> · thick = transformation. **Node border:** Bronze / Silver / Gold layer.")


# ------------------------------------------------------------------ medallion (Gold -> Silver -> Bronze) view
LAYER_STYLE = {
    "Model": {"bg": "#CA5010", "fg": "#FFFFFF", "level": 0},
    "Gold": {"bg": "#D4A017", "fg": "#1A1A1A", "level": 1},
    "Silver": {"bg": "#C0C0C0", "fg": "#1A1A1A", "level": 2},
    "Bronze": {"bg": "#FFE14D", "fg": "#1A1A1A", "level": 3},
    "Source": {"bg": "#8764B8", "fg": "#FFFFFF", "level": 4},
}


MEDALLION_COLUMNS = ["Gold", "Silver", "Bronze", "Source"]
COL_GAP, ROW_GAP, NODE_W = 285, 106, 180
FRAME_W = 1000  # typical width of the main column; used only to size the frame height


@lru_cache(maxsize=1)
def _vis_js() -> str:
    """vis-network shipped inside the pyvis package, embedded inline (no CDN needed)."""
    base = Path(pyvis.__file__).parent / "templates" / "lib"
    for ver in ("vis-9.1.2", "vis-9.0.4"):
        f = base / ver / "vis-network.min.js"
        if f.exists():
            return f.read_text(encoding="utf-8")
    raise FileNotFoundError("vis-network.min.js not found in the pyvis package")


def _short(qualified: str, layer: str) -> tuple[str, str]:
    """('dbo.sales', 'Gold') from 'Gold.dbo.sales'; ('dbo.Agent', 'SalesDB') from 'server/SalesDB.dbo.Agent'."""
    tail = qualified.rsplit("/", 1)[-1]
    parts = tail.rsplit(".", 2)
    if len(parts) == 3:
        return f"{parts[1]}.{parts[2]}", parts[0]
    return tail, layer


def _place(columns: list[list[str]], downstream: dict[str, set[str]]) -> dict[str, tuple[float, float]]:
    """x = column, y = barycentre of the (already placed) downstream nodes, spread so boxes never overlap."""
    pos: dict[str, tuple[float, float]] = {}
    for ci, col in enumerate(columns):
        want = []
        for i, k in enumerate(col):
            ys = [pos[x][1] for x in downstream.get(k, ()) if x in pos]
            want.append((sum(ys) / len(ys) if ys else i * ROW_GAP, k))
        want.sort()
        ys, prev = [], None
        for y, _ in want:
            y = y if prev is None else max(y, prev + ROW_GAP)
            ys.append(y)
            prev = y
        shift = (ys[0] + ys[-1]) / 2 if ys and ci == 0 else 0  # centre the first column on 0
        for (_, k), y in zip(want, ys):
            pos[k] = (ci * COL_GAP, y - shift)
    return pos


def _loader(t: dict) -> str:
    """Third line of a table box: the process that loads it."""
    if t["layer"] == "Source":
        return "source system"
    by = (t.get("loadedBy") or "").strip()
    if not by or by.lower().startswith("not found"):
        return "⚠ loader not found"
    by = by if len(by) <= 30 else by[:28] + "…"
    return f"⚙ {by}"


def medallion_height(d: dict) -> int:
    """Frame height that matches the drawing once it is scaled to fit the frame width (no big empty bands)."""
    counts: dict[str, int] = {}
    for t in d["tables"]:
        counts[t["layer"]] = counts.get(t["layer"], 0) + 1
    cols = 1 + max(1, len(counts))
    content_w = (cols - 1) * COL_GAP + NODE_W + 60
    content_h = max(counts.values(), default=1) * ROW_GAP + 110
    scale = min(1.0, FRAME_W / content_w)
    return int(max(340, min(1400, content_h * scale + 40)))


def render_medallion(d: dict, height: int | None = None) -> None:
    """Left to right: report / semantic model -> GOLD -> SILVER -> BRONZE -> source system.

    Each arrow reads "is loaded from"; its label is the process that performs the load. Click a box to highlight
    its full lineage; click the background to clear.
    """
    height = height or medallion_height(d)
    report = d.get("report")
    nodes, edges = [], []
    lead = [["__model__"]]  # the selected report / semantic model, left of GOLD
    by_layer: dict[str, list[str]] = {c: [] for c in MEDALLION_COLUMNS}
    tinfo = {t["key"]: t for t in d["tables"]}
    for t in sorted(d["tables"], key=lambda t: t["qualifiedName"].lower()):
        by_layer.get(t["layer"], by_layer["Source"]).append(t["key"])
    layers = [c for c in MEDALLION_COLUMNS if by_layer[c]] or ["Gold"]
    columns = lead + [by_layer[c] for c in layers]

    downstream: dict[str, set[str]] = {}
    keys = set(tinfo)
    model_loads = {}
    for r in d["relations"]:
        if r["from"] in keys and r["to"] in keys:
            downstream.setdefault(r["from"], set()).add(r["to"])
        elif r["from"] in keys and r["toLayer"] == "Model":
            model_loads[r["from"]] = r
    for t in d["tables"]:
        if t["depth"] == 0:
            downstream.setdefault(t["key"], set()).add("__model__")
    # place GOLD..SOURCE first (centred), then pin report / model to the middle of GOLD
    pos = _place(columns[len(lead):], downstream)
    gold_y = [pos[k][1] for k in columns[len(lead)]] or [0]
    mid = (min(gold_y) + max(gold_y)) / 2
    pos = {k: (x + len(lead) * COL_GAP, y - mid) for k, (x, y) in pos.items()}
    for i, col in enumerate(lead):
        pos[col[0]] = (i * COL_GAP, 0)
    top = min(y for _, y in pos.values()) - 80

    def box(key, label, bg, fg, tip, bold=False):
        x, y = pos[key]
        nodes.append({"id": key, "x": x, "y": y, "label": label, "title": tip, "shape": "box", "margin": 10,
                      "widthConstraint": {"minimum": NODE_W, "maximum": NODE_W},
                      "color": {"background": bg, "border": "#4a4a4a",
                                "highlight": {"background": bg, "border": "#0f6cbd"},
                                "hover": {"background": bg, "border": "#0f6cbd"}},
                      "font": {"color": fg, "size": 16, "multi": "html", "face": "Segoe UI, Arial",
                               "bold": {"color": fg, "size": 18},
                               "ital": {"color": fg, "size": 13, "face": "Segoe UI, Arial"}}, "borderWidth": 2 if bold else 1})

    model = d["model"]["name"]
    if report:
        box("__model__", f"<b>{_html.escape(report['name'])}</b>\nReport\n<i>model: {_html.escape(model)}</i>", "#C50F1F",
            "#FFFFFF", f"Report: {report['name']}\nSemantic model: {model}", True)
    else:
        box("__model__", f"<b>{_html.escape(model)}</b>\nSemantic model", "#CA5010", "#FFFFFF",
            f"Semantic model: {model}", True)
    for k, t in tinfo.items():
        st_ = LAYER_STYLE.get(t["layer"], LAYER_STYLE["Source"])
        name, where = _short(t["qualifiedName"], t["layer"])
        tip = (f"{t['layer']} table: {t['qualifiedName']}\nLoaded by: {t['loadedBy']}\nLoaded from: {t['loadedFrom']}"
               f"\nConfidence: {t.get('confidence') or '-'}\nLayer basis: {t['layerBasis']}")
        box(k, f"<b>{_html.escape(name)}</b>\n{_html.escape(where)}\n<i>{_html.escape(_loader(t))}</i>",
            st_["bg"], st_["fg"], tip)
        if t["depth"] == 0:
            load = model_loads.get(k)
            edges.append({"from": "__model__", "to": k, "label": load["process"] if load else "",
                          "color": "#CA5010", "width": 2,
                          "title": f"{load['toName'] if load else model} reads {t['qualifiedName']}"
                                   f"{' via ' + load['process'] if load else ''}"})
    for r in d["relations"]:
        if r["to"] in keys and r["from"] in keys:
            inferred = r["confidence"] in ("Inferred", "Unavailable")
            edges.append({"from": r["to"], "to": r["from"], "width": 2.5,
                          "dashes": [8, 5] if inferred else False, "color": "#bc4b09" if inferred else "#107c10",
                          "title": f"{r['toName']} is loaded from {r['fromName']}\nProcess: {r['process']} "
                                   f"({r['processType']})\nConfidence: {r['confidence']}"
                                   f"{chr(10) + r['reason'] if r.get('reason') else ''}"})
    for i, c in enumerate(layers):  # column headers
        st_ = LAYER_STYLE[c]
        nodes.append({"id": f"__hdr_{c}", "x": (len(lead) + i) * COL_GAP, "y": top, "label": f"<b>{c.upper()}</b>",
                      "shape": "box", "margin": {"top": 6, "bottom": 6, "left": 14, "right": 14},
                      "widthConstraint": {"minimum": NODE_W, "maximum": NODE_W}, "chosen": False, "fixed": True,
                      "color": {"background": st_["bg"], "border": st_["bg"]}, "shapeProperties": {"borderRadius": 14},
                      "font": {"color": st_["fg"], "size": 17, "multi": "html", "bold": {"size": 19}},
                      "group": "header"})
    for e in edges:
        e.setdefault("font", {"size": 13, "background": "#FFFFFF", "strokeWidth": 0, "color": "#333"})
        e["arrows"] = {"to": {"enabled": True, "scaleFactor": 0.7}}
        e["smooth"] = {"type": "cubicBezier", "forceDirection": "horizontal", "roundness": 0.45}

    options = {"physics": {"enabled": False}, "layout": {"hierarchical": False},
               "interaction": {"hover": True, "navigationButtons": True, "keyboard": False, "tooltipDelay": 120,
                               "dragNodes": True, "zoomView": True},
               "nodes": {"fixed": {"x": False, "y": False}}}
    html = _MEDALLION_HTML.format(js=_vis_js(), height=height, nodes=json.dumps(nodes), edges=json.dumps(edges),
                                  options=json.dumps(options))
    components.html(html, height=height + 8, scrolling=False)


_MEDALLION_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;padding:0;background:#fff;font-family:'Segoe UI',Arial,sans-serif}}
#g{{width:100%;height:{height}px;border:1px solid #e1dfdd;border-radius:8px;box-sizing:border-box}}
div.vis-tooltip{{position:absolute;white-space:pre-line;font-size:13px;background:#fff;border:1px solid #c8c6c4;
padding:6px 9px;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.15);max-width:420px}}
#hint{{position:absolute;right:12px;top:8px;font-size:12px;color:#605e5c}}
.vis-navigation .vis-button{{background-color:#f3f2f1;border-radius:4px;opacity:.85}}</style>
<script>{js}</script></head><body><div id="hint">Click a box to trace its lineage · scroll to zoom · drag to pan</div>
<div id="g"></div><script>
const nodes = new vis.DataSet({nodes}); const edges = new vis.DataSet({edges});
const network = new vis.Network(document.getElementById('g'), {{nodes, edges}}, {options});
const base = {{}}; nodes.forEach(n => base[n.id] = {{color: n.color, font: n.font}});
const ebase = {{}}; edges.forEach(e => ebase[e.id] = {{color: e.color, font: e.font}});
function fit() {{
  network.fit({{animation: false}});
  const s = network.getScale();
  if (s > 1.0) network.moveTo({{scale: 1.0, position: network.getViewPosition()}});
}}
network.once('afterDrawing', fit); setTimeout(fit, 250); window.addEventListener('resize', fit);
function walk(start, dir) {{  // dir 'to' = upstream (loaded from), 'from' = downstream
  const seen = new Set([start]), q = [start], es = new Set();
  while (q.length) {{ const k = q.pop();
    edges.get({{filter: e => (dir === 'to' ? e.from : e.to) === k}}).forEach(e => {{ es.add(e.id);
      const n = dir === 'to' ? e.to : e.from; if (!seen.has(n)) {{ seen.add(n); q.push(n); }} }}); }}
  return [seen, es];
}}
function reset() {{
  nodes.update(Object.keys(base).map(id => ({{id, color: base[id].color, font: base[id].font, opacity: 1}})));
  edges.update(Object.keys(ebase).map(id => ({{id, color: ebase[id].color, font: ebase[id].font}})));
}}
network.on('click', p => {{
  reset(); const id = p.nodes[0]; if (!id || String(id).startsWith('__hdr_')) return;
  const [up, eu] = walk(id, 'to'), [down, ed] = walk(id, 'from');
  const keepN = new Set([...up, ...down]), keepE = new Set([...eu, ...ed]);
  nodes.update(nodes.get().filter(n => !keepN.has(n.id) && !String(n.id).startsWith('__hdr_'))
    .map(n => ({{id: n.id, opacity: 0.18}})));
  edges.update(edges.get().filter(e => !keepE.has(e.id))
    .map(e => ({{id: e.id, color: {{color: '#e6e6e6'}}, font: {{size: 14, color: '#d0d0d0', background: '#fff'}}}})));
}});
</script></body></html>"""


MEDALLION_LEGEND = (
    "<span style='background:#D4A017;padding:2px 8px;border-radius:4px'>GOLD</span> &nbsp;"
    "<span style='background:#C0C0C0;padding:2px 8px;border-radius:4px'>SILVER</span> &nbsp;"
    "<span style='background:#FFE14D;padding:2px 8px;border-radius:4px'>BRONZE</span> &nbsp;"
    "<span style='background:#8764B8;color:#fff;padding:2px 8px;border-radius:4px'>SOURCE</span> &nbsp; "
    "Arrows read <b>“is loaded from”</b>; ⚙ = the process that loads the table. "
    "<span style='color:#107c10'>━ confirmed / parsed</span> · <span style='color:#bc4b09'>╌ inferred</span>")
