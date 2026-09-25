"""HTTP client for the FastAPI backend, with optional Entra ID device-code sign-in."""
from __future__ import annotations

import os
from urllib.parse import quote

import requests
import streamlit as st


def cfg(key: str, default: str = "") -> str:
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:  # no secrets.toml
        pass
    return os.environ.get(key, default)


AUTH_MODE = cfg("AUTH_MODE", "none")
# EMBEDDED_BACKEND=true (default when no API_BASE is configured): start the FastAPI backend inside this
# Streamlit process with local SQLite + labelled SAMPLE data. One command, works on Streamlit Community Cloud.
EMBEDDED = cfg("EMBEDDED_BACKEND", "true" if not cfg("API_BASE") else "false").lower() == "true"
EMBEDDED_PORT = int(cfg("EMBEDDED_PORT", "8765"))
API_BASE = (f"http://127.0.0.1:{EMBEDDED_PORT}" if EMBEDDED else cfg("API_BASE", "http://localhost:8000")).rstrip("/")


@st.cache_resource(show_spinner="Starting the lineage backend and loading SAMPLE data…")
def start_embedded_backend() -> str:
    """Run backend/app in a background thread (demo mode: auth off, SQLite, SAMPLE data)."""
    import sys
    import tempfile
    import threading
    import time
    from pathlib import Path

    here = Path(__file__).resolve().parent
    backend = here.parent / "backend"
    if not (backend / "app" / "main.py").exists():
        # Flat deployment (all files in one folder): the backend + SAMPLE files ship inside
        # lineage_backend_bundle.py (a base64-encoded zip), or as lineage_backend.zip / an extracted folder.
        import base64
        import io
        import zipfile
        target = Path(tempfile.gettempdir()) / "fabric-lineage-bundle"
        found = next(iter(sorted(target.glob("**/backend/app/main.py"))), None) if target.exists() else None
        if found is None:
            data = None
            if (here / "lineage_backend_bundle.py").exists():
                sys.path.insert(0, str(here))
                import lineage_backend_bundle  # noqa: E402
                data = base64.b64decode(lineage_backend_bundle.DATA)
            elif (here / "lineage_backend.zip").is_file():
                data = (here / "lineage_backend.zip").read_bytes()
            if data:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    zf.extractall(target)
                found = next(iter(sorted(target.glob("**/backend/app/main.py"))), None)
            else:  # an already-extracted folder next to this file (any nesting depth)
                found = next(iter(sorted(here.glob("**/backend/app/main.py"))), None)
        if found is None:
            raise RuntimeError("Backend files not found: upload lineage_backend_bundle.py next to app.py.")
        backend = found.parents[1]
    sys.path.insert(0, str(backend))
    # The Streamlit main file may itself be called app.py; make sure "app" resolves to the backend package.
    mod = sys.modules.get("app")
    if mod is not None and not hasattr(mod, "__path__"):
        del sys.modules["app"]
    db_file = Path(tempfile.gettempdir()) / "fabric-lineage-demo.db"
    os.environ.update(APP_ENV="local", AUTH_MODE="disabled", DATABASE_URL=f"sqlite:///{db_file}",
                      UPLOAD_LOCAL_DIR=str(Path(tempfile.gettempdir()) / "fabric-lineage-uploads"),
                      LOG_LEVEL="WARNING", CORS_ORIGINS='["*"]')
    # Connect to a real Fabric tenant with a service principal (values from Streamlit Secrets).
    if cfg("FABRIC_TENANT_ID") and cfg("FABRIC_CLIENT_ID") and cfg("FABRIC_CLIENT_SECRET"):
        os.environ.update(SERVICE_PRINCIPAL_MODE="true", TENANT_ID=cfg("FABRIC_TENANT_ID"),
                          API_CLIENT_ID=cfg("FABRIC_CLIENT_ID"), BACKGROUND_CREDENTIAL="client_secret",
                          LINEAGE_API_CLIENT_SECRET=cfg("FABRIC_CLIENT_SECRET"),
                          SCANNER_ENABLED=cfg("FABRIC_USE_SCANNER_API", "true"))
    import uvicorn

    from app.main import app  # noqa: E402  (backend package)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=EMBEDDED_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            if requests.get(f"{API_BASE}/api/health", timeout=1).ok:
                break
        except requests.ConnectionError:
            time.sleep(0.2)
    if cfg("LOAD_SAMPLE_DATA", "true").lower() == "true":
        requests.post(f"{API_BASE}/api/admin/sample", timeout=120).raise_for_status()
    return API_BASE


if EMBEDDED:
    start_embedded_backend()


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


@st.cache_resource
def _msal_app():
    import msal
    return msal.PublicClientApplication(cfg("CLIENT_ID"), authority=f"https://login.microsoftonline.com/{cfg('TENANT_ID')}")


def token() -> str | None:
    if AUTH_MODE != "device_code":
        return None
    app = _msal_app()
    accounts = app.get_accounts()
    if accounts:
        r = app.acquire_token_silent([cfg("API_SCOPE")], account=accounts[0])
        if r and "access_token" in r:
            return r["access_token"]
    return None


def sign_in_widget() -> bool:
    """Render the device-code sign-in. Returns True when signed in (or auth is off)."""
    if AUTH_MODE != "device_code" or token():
        return True
    app = _msal_app()
    st.title("Sign in")
    st.write("Sign in with your Microsoft Entra ID work account. You will only see workspaces you can access in Fabric.")
    if "flow" not in st.session_state:
        if st.button("Start sign-in", type="primary"):
            st.session_state.flow = app.initiate_device_flow(scopes=[cfg("API_SCOPE")])
            st.rerun()
        return False
    flow = st.session_state.flow
    if "user_code" not in flow:
        st.error(f"Could not start sign-in: {flow.get('error_description', flow)}")
        del st.session_state.flow
        return False
    st.info(f"Go to **{flow['verification_uri']}** and enter code **{flow['user_code']}**, then click Continue.")
    if st.button("Continue", type="primary"):
        with st.spinner("Waiting for sign-in…"):
            r = app.acquire_token_by_device_flow(flow)  # blocks until done or expired
        del st.session_state.flow
        if "access_token" not in r:
            st.error(r.get("error_description", "Sign-in failed"))
            return False
        st.rerun()
    return False


def _req(method: str, path: str, **kw):
    headers = kw.pop("headers", {})
    t = token()
    if t:
        headers["Authorization"] = f"Bearer {t}"
    try:
        r = requests.request(method, f"{API_BASE}{path}", headers=headers, timeout=120, **kw)
    except requests.ConnectionError as e:
        raise ApiError(0, "BACKEND_UNREACHABLE", f"Cannot reach the backend at {API_BASE}. Is it running?") from e
    if r.status_code >= 400:
        try:
            e = r.json().get("error", {})
        except ValueError:
            e = {}
        raise ApiError(r.status_code, e.get("code", f"HTTP_{r.status_code}"), e.get("message", r.text[:300]),
                       e.get("details"))
    return r


def get(path: str, **params):
    return _req("GET", path, params={k: v for k, v in params.items() if v not in (None, "")}).json()


def post(path: str, json=None, files=None, data=None):
    return _req("POST", path, json=json, files=files, data=data).json()


def put(path: str, json=None):
    return _req("PUT", path, json=json).json()


def delete(path: str):
    return _req("DELETE", path).json()


def raw(path: str, **params) -> bytes:
    return _req("GET", path, params=params).content


def enc(key: str) -> str:
    return quote(key, safe="")


def show_error(e: Exception):
    if isinstance(e, ApiError):
        st.error(f"**{e.code.replace('_', ' ').title()}** - {e.message}")
        cands = (e.details or {}).get("candidates") if isinstance(e.details, dict) else None
        if cands:
            st.write("Matching items - choose one by ID:")
            st.dataframe([{"name": c.get("name"), "id": c.get("id")} for c in cands], hide_index=True)
    else:
        st.exception(e)
