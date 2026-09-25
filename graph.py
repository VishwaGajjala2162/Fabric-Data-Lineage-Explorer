"""Interactive lineage graph rendered with pyvis (vis-network) inside Streamlit."""
from __future__ import annotations

import re

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
