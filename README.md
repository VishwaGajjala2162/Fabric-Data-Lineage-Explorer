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

**Connect Fabric:** add `FABRIC_TENANT_ID` and `FABRIC_CLIENT_ID` in Manage app → Settings → Secrets (see
`secrets.toml.example`, which is not uploaded to GitHub). Without them, only the labelled SAMPLE data is available.
