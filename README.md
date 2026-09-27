# Fabric Data Lineage Explorer (Streamlit)

Sign in to Microsoft Fabric, pick a **semantic model** or a **report**, and see every table behind it:
GOLD tables the model reads → SILVER tables that load Gold → BRONZE tables that load Silver → source systems,
with the process (notebook, pipeline, shortcut, dataflow) that loads each table.

* **Table lineage** – one table per layer plus the end-to-end Gold → Silver → Bronze → Source paths
* **Lineage graph** – Gold (gold), Silver (silver), Bronze (yellow) columns; click a box to trace its lineage
* **Download** – Excel workbook (paths, tables by layer, hop-by-hop loads) or CSV files

| File | Purpose |
|---|---|
| `app.py` | Streamlit app (main file) |
| `api.py` | Fabric sign-in and backend client; starts the embedded backend |
| `graph.py` | lineage graphs |
| `lineage_backend_bundle.py` | lineage engine + SAMPLE data (packed), unpacked automatically at startup |
| `requirements.txt` | Python packages |

**Run locally:** `pip install -r requirements.txt`, then `streamlit run app.py`.

**Streamlit Community Cloud:** main file path `app.py`.

**Connect Fabric:** nothing to configure. On the first page choose **App credentials** (tenant ID, client ID,
client secret of an app registration) or **My Microsoft account**, and click Connect. The details stay only in
that browser session's memory. The app then lists your Fabric capacities, workspaces, semantic models and reports.
