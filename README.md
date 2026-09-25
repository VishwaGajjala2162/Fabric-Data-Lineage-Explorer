# Fabric Data Lineage Explorer (Streamlit)

Traces Microsoft Fabric lineage: sources → pipelines / notebooks → Bronze / Silver / Gold → semantic models → measures → reports / visuals.

| File | Purpose |
|---|---|
| `app.py` | Streamlit app (main file) |
| `api.py` | backend client; starts the embedded backend |
| `graph.py` | interactive lineage graph |
| `lineage_backend_bundle.py` | lineage engine + SAMPLE data (packed), unpacked automatically at startup |
| `requirements.txt` | Python packages |
| `secrets.toml.example` | template for the Fabric connection settings |

**Run locally:** `pip install -r requirements.txt`, then `streamlit run app.py`.

**Streamlit Community Cloud:** main file path `app.py`, Python 3.12.

**Connect your Fabric tenant:** paste the contents of `secrets.toml.example`, with your values filled in, into Manage app → Settings → Secrets. Without them the app shows the labelled SAMPLE data only.
