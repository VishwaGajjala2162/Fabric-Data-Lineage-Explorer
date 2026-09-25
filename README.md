# Fabric Data Lineage Explorer (Streamlit)

Streamlit app for tracing Microsoft Fabric lineage (sources → pipelines / notebooks → Bronze / Silver / Gold → semantic models → measures → reports / visuals). It runs with a clearly labelled **SAMPLE** workspace.

All files are in this one folder:

| File | Purpose |
|---|---|
| `app.py` | Streamlit app (main file) |
| `api.py` | backend client; starts the embedded backend |
| `graph.py` | interactive lineage graph |
| `lineage_backend_bundle.py` | lineage engine + SAMPLE data (packed), unpacked automatically at startup |
| `requirements.txt` | Python packages |

**Run locally:** `pip install -r requirements.txt`, then `streamlit run app.py`.

**Streamlit Community Cloud:** main file path `app.py`, Python 3.12.
