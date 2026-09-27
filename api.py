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

# ---- how people connect to Fabric ------------------------------------------------------------------------
# Nothing sensitive is needed in the app settings: on the first page each person enters their own connection
# (service principal: tenant ID + client ID + secret, or their Microsoft account via a one-time code). It is kept
# only in that browser session's memory. Optional non-secret defaults (FABRIC_TENANT_ID / FABRIC_CLIENT_ID) just
# pre-fill the form. FABRIC_REDIRECT_URI (+ IDs) additionally enables "Sign in with Microsoft" via redirect.
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
        import hashlib
        data = None
        if (here / "lineage_backend_bundle.py").exists():
            sys.path.insert(0, str(here))
            import lineage_backend_bundle  # noqa: E402
            data = base64.b64decode(lineage_backend_bundle.DATA)
        elif (here / "lineage_backend.zip").is_file():
            data = (here / "lineage_backend.zip").read_bytes()
        # one folder per bundle version, so an updated bundle is never shadowed by an older extracted copy
        tag = hashlib.sha1(data).hexdigest()[:12] if data else "none"
        target = Path(tempfile.gettempdir()) / f"fabric-lineage-bundle-{tag}"
        found = next(iter(sorted(target.glob("**/backend/app/main.py"))), None) if target.exists() else None
        if found is None:
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
    # Every Fabric call uses the Fabric token of the person's own connection (passed per request from this UI);
    # without one, only SAMPLE data is visible.
    os.environ["TOKEN_PASSTHROUGH"] = "true"
    os.environ.setdefault("SCANNER_ENABLED", cfg("FABRIC_USE_SCANNER_API", "false"))
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


def _api_msal_app():
    """Legacy AUTH_MODE=device_code (external API): one MSAL client per browser session."""
    import msal
    if "_api_msal_app" not in st.session_state:
        st.session_state._api_msal_app = msal.PublicClientApplication(
            cfg("CLIENT_ID"), authority=f"https://login.microsoftonline.com/{cfg('TENANT_ID') or 'organizations'}")
    return st.session_state._api_msal_app


def token() -> str | None:
    if AUTH_MODE != "device_code":
        return None
    app = _api_msal_app()
    accounts = app.get_accounts()
    if accounts:
        r = app.acquire_token_silent([cfg("API_SCOPE")], account=accounts[0])
        if r and "access_token" in r:
            return r["access_token"]
    return None


def sign_in_widget() -> bool:
    """Legacy device-code sign-in for an external, Entra-protected API. True when signed in (or not used)."""
    if AUTH_MODE != "device_code" or token():
        return True
    app = _api_msal_app()
    st.title("Sign in")
    if "flow" not in st.session_state:
        if st.button("Start sign-in", type="primary"):
            st.session_state.flow = app.initiate_device_flow(scopes=[cfg("API_SCOPE")])
            st.rerun()
        return False
    flow = st.session_state.flow
    st.info(f"Go to **{flow.get('verification_uri')}** and enter code **{flow.get('user_code')}**, then click Continue.")
    if st.button("Continue", type="primary"):
        r = app.acquire_token_by_device_flow(flow)
        del st.session_state.flow
        if "access_token" not in r:
            st.error(r.get("error_description", "Sign-in failed"))
            return False
        st.rerun()
    return False


# ================================================================ Fabric connection (per browser session)
import re as _re  # noqa: E402

FABRIC_SCOPES = cfg("FABRIC_SCOPES", "https://api.fabric.microsoft.com/Workspace.Read.All "
                                     "https://api.fabric.microsoft.com/Item.ReadWrite.All").split()
POWERBI_SCOPES = cfg("POWERBI_SCOPES", "https://analysis.windows.net/powerbi/api/Report.Read.All "
                                       "https://analysis.windows.net/powerbi/api/Dataset.Read.All").split()
FABRIC_APP_SCOPE = ["https://api.fabric.microsoft.com/.default"]
POWERBI_APP_SCOPE = ["https://analysis.windows.net/powerbi/api/.default"]
AUTHORITY_HOST = cfg("FABRIC_AUTHORITY_HOST", "https://login.microsoftonline.com").rstrip("/")  # override: tests
_GUID = _re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_DOMAIN = _re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def valid_tenant(v: str) -> bool:
    return bool(_GUID.match(v.strip()) or _DOMAIN.match(v.strip()))


def valid_guid(v: str) -> bool:
    return bool(_GUID.match(v.strip()))


def default_tenant() -> str:
    """Non-secret pre-fill from the app settings (ignored if it is a placeholder such as 'paste ... ID')."""
    v = cfg("FABRIC_TENANT_ID").strip()
    return v if valid_tenant(v) else ""


def default_client_id() -> str:
    v = cfg("FABRIC_CLIENT_ID").strip()
    return v if valid_guid(v) else ""


def saved_service_principal() -> bool:
    """A complete, well-formed service principal is stored in the app settings (optional, not recommended)."""
    return bool(default_tenant() and default_client_id() and cfg("FABRIC_CLIENT_SECRET").strip()
                and not cfg("FABRIC_CLIENT_SECRET").lower().startswith("paste"))


REDIRECT_URI = cfg("FABRIC_REDIRECT_URI").strip()
REDIRECT_SIGNIN = bool(default_tenant() and default_client_id() and REDIRECT_URI)


def _new_app(tenant: str, client_id: str, secret: str | None = None):
    import msal
    extra = {} if AUTHORITY_HOST == "https://login.microsoftonline.com" else {"instance_discovery": False}
    authority = f"{AUTHORITY_HOST}/{tenant.strip()}"
    if secret:
        return msal.ConfidentialClientApplication(client_id.strip(), client_credential=secret,
                                                  authority=authority, **extra)
    return msal.PublicClientApplication(client_id.strip(), authority=authority, **extra)


def _msal_error(desc: str, err: str = "") -> str:
    d = desc or err or "Sign-in failed"
    if "AADSTS7000215" in d or "AADSTS7000222" in d or err == "invalid_client":
        return ("The client secret is wrong or expired. Paste the secret **Value** (not the Secret ID) from "
                "App registrations > your app > Certificates & secrets.")
    if "AADSTS700016" in d or err == "unauthorized_client":
        return "No application with this Client ID exists in this tenant. Check the Application (client) ID."
    if "AADSTS90002" in d or "AADSTS900023" in d:
        return "This tenant was not found. Check the Directory (tenant) ID."
    if "AADSTS65001" in d or err == "consent_required":
        return ("An administrator must grant consent for this app's Power BI Service permissions "
                "(App registrations > your app > API permissions > Grant admin consent).")
    if "AADSTS7000218" in d:
        return "Turn on 'Allow public client flows' (App registrations > your app > Authentication)."
    if "AADSTS50011" in d:
        return f"The reply URL is not registered. Add {REDIRECT_URI} as a Web redirect URI on the app registration."
    if err == "authorization_declined" or err == "access_denied":
        return "Sign-in was cancelled."
    if err == "expired_token":
        return "The sign-in code expired. Start again."
    return d.split("\r\n")[0].split("\n")[0][:300]


def _save_connection(kind: str, app, tenant: str, client_id: str, label: str) -> None:
    for k in ("_conn", "_conn_app", "device_flow", "_sign_in_url"):
        st.session_state.pop(k, None)
    st.session_state._conn = {"kind": kind, "tenant": tenant.strip(), "client_id": client_id.strip(), "label": label}
    st.session_state._conn_app = app


def connect_service_principal(tenant: str, client_id: str, secret: str) -> str | None:
    """Connect this browser session with app credentials. Returns an error message, or None on success.

    The secret is used only here, in memory, to build the MSAL client; it is never written anywhere."""
    tenant, client_id, secret = tenant.strip(), client_id.strip(), secret.strip()
    if not valid_tenant(tenant):
        return ("That is not a Directory (tenant) ID. It looks like 72f988bf-86f1-41af-91ab-2d7cd011db47 - copy it "
                "from Microsoft Entra ID > Overview > Tenant ID (a domain such as contoso.onmicrosoft.com also works).")
    if not valid_guid(client_id):
        return ("That is not an Application (client) ID. Copy the GUID from App registrations > your app > Overview.")
    if not secret or secret.lower().startswith("paste"):
        return "Enter the client secret Value."
    try:
        app = _new_app(tenant, client_id, secret)
        r = app.acquire_token_for_client(FABRIC_APP_SCOPE)
    except ValueError as e:
        return _msal_error(str(e)) if "AADSTS" in str(e) else "This tenant was not found. Check the Directory (tenant) ID."
    except Exception as e:  # network / proxy
        return f"Could not reach Microsoft Entra ID ({type(e).__name__}). Try again in a moment."
    if "access_token" not in r:
        return _msal_error(r.get("error_description", ""), r.get("error", ""))
    _save_connection("sp", app, tenant, client_id, f"Service principal {client_id[:8]}…")
    return None


def start_device_sign_in(tenant: str, client_id: str) -> dict:
    """Start 'sign in with my Microsoft account'. Returns the flow, or {"error_description": message}."""
    tenant, client_id = tenant.strip() or "organizations", client_id.strip()
    if tenant != "organizations" and not valid_tenant(tenant):
        return {"error_description": "That is not a Directory (tenant) ID or domain."}
    if not valid_guid(client_id):
        return {"error_description": "That is not an Application (client) ID (a GUID from App registrations)."}
    try:
        app = _new_app(tenant, client_id)
        flow = app.initiate_device_flow(scopes=FABRIC_SCOPES)
    except ValueError:
        return {"error_description": "This tenant was not found. Check the Directory (tenant) ID."}
    except Exception as e:
        return {"error_description": f"Could not reach Microsoft Entra ID ({type(e).__name__})."}
    if "user_code" not in flow:
        return {"error_description": _msal_error(flow.get("error_description", ""), flow.get("error", ""))}
    st.session_state._pending_device = (app, tenant, client_id)
    return flow


def finish_device_sign_in(flow: dict) -> str | None:
    """Blocks until the person finished signing in on the Microsoft page. Returns an error message or None."""
    pending = st.session_state.pop("_pending_device", None)
    if not pending:
        return "The sign-in expired. Start again."
    app, tenant, client_id = pending
    flow = {**flow, "expires_at": min(flow.get("expires_at", 0), __import__("time").time() + 90)}
    try:
        r = app.acquire_token_by_device_flow(flow)
    except Exception as e:
        return f"Sign-in failed: could not reach Microsoft Entra ID ({type(e).__name__})."
    if "access_token" not in r:
        if r.get("error") in ("authorization_pending", "slow_down", "expired_token"):
            return "Sign-in was not completed on the Microsoft page yet. Start again and finish it before Continue."
        return _msal_error(r.get("error_description", ""), r.get("error", ""))
    acct = (app.get_accounts() or [{}])[0]
    _save_connection("user", app, tenant, client_id, acct.get("username", "Microsoft account"))
    return None


# ---- redirect sign-in (only when FABRIC_REDIRECT_URI is configured) ------------------------------------------
_FLOW_TTL = 900


@st.cache_resource
def _pending_flows() -> dict:
    """Redirect sign-ins in progress, keyed by the OAuth "state" (Microsoft returns into a NEW browser session)."""
    import threading
    return {"lock": threading.Lock(), "flows": {}}


def _redirect_app():
    secret = cfg("FABRIC_CLIENT_SECRET").strip()
    return _new_app(default_tenant(), default_client_id(),
                    secret if secret and not secret.lower().startswith("paste") else None)


def sign_in_url() -> tuple[str, str]:
    import time
    now = time.time()
    cached = st.session_state.get("_sign_in_url")
    if cached and now - cached[0] < _FLOW_TTL - 120:
        return cached[1], ""
    try:
        flow = _redirect_app().initiate_auth_code_flow(FABRIC_SCOPES, redirect_uri=REDIRECT_URI,
                                                        prompt="select_account")
    except ValueError:
        return "", "The Directory (tenant) ID in the app settings was not found."
    except Exception as e:
        return "", f"Could not reach Microsoft Entra ID ({type(e).__name__}). Try again in a moment."
    store = _pending_flows()
    with store["lock"]:
        for k in [k for k, (t, _) in store["flows"].items() if now - t > _FLOW_TTL]:
            del store["flows"][k]
        store["flows"][flow["state"]] = (now, flow)
    st.session_state._sign_in_url = (now, flow["auth_uri"])
    return flow["auth_uri"], ""


def is_sign_in_callback() -> bool:
    q = st.query_params
    return REDIRECT_SIGNIN and "state" in q and ("code" in q or "error" in q)


def complete_sign_in_callback() -> str | None:
    params = st.query_params.to_dict()
    st.query_params.clear()
    if params.get("error"):
        return _msal_error(params.get("error_description", ""), params["error"])
    store = _pending_flows()
    with store["lock"]:
        entry = store["flows"].pop(params.get("state", ""), None)
    if not entry:
        return "This sign-in link has expired or was already used. Sign in again."
    app = _redirect_app()
    try:
        r = app.acquire_token_by_auth_code_flow(entry[1], params)
    except ValueError as e:
        return f"Sign-in could not be verified ({str(e)[:150]}). Please try again."
    except Exception as e:
        return f"Sign-in failed: could not reach Microsoft Entra ID ({type(e).__name__})."
    if "access_token" not in r:
        return _msal_error(r.get("error_description", ""), r.get("error", ""))
    acct = (app.get_accounts() or [{}])[0]
    _save_connection("user", app, default_tenant(), default_client_id(), acct.get("username", "Microsoft account"))
    return None


# ---- the current connection -----------------------------------------------------------------------------------
def connection() -> dict:
    return st.session_state.get("_conn") or {}


def _token(kind: str) -> str:
    conn, app = connection(), st.session_state.get("_conn_app")
    if not conn or app is None:
        return ""
    try:
        if conn["kind"] == "sp":  # MSAL caches app tokens in memory and renews them before they expire
            r = app.acquire_token_for_client(FABRIC_APP_SCOPE if kind == "fabric" else POWERBI_APP_SCOPE)
        else:
            accounts = app.get_accounts()
            if not accounts:
                return ""
            r = app.acquire_token_silent(FABRIC_SCOPES if kind == "fabric" else POWERBI_SCOPES, account=accounts[0])
    except Exception:
        return ""
    return r["access_token"] if r and "access_token" in r else ""


def fabric_signed_in() -> bool:
    """This browser session is connected to Fabric (service principal or Microsoft account)."""
    return bool(_token("fabric"))


def signed_in_account() -> dict:
    conn = connection()
    return {"username": conn.get("label", ""), "kind": conn.get("kind", ""),
            "home_account_id": f"{conn.get('kind')}:{conn.get('tenant')}:{conn.get('client_id')}:{conn.get('label')}"} \
        if conn else {}


def fabric_sign_out() -> None:
    for k in list(st.session_state.keys()):
        del st.session_state[k]


def user_key() -> str:
    """Cache partition key: one per connection (or one for anonymous / sample sessions)."""
    return signed_in_account().get("home_account_id", "anonymous")


def auth_headers() -> dict:
    """Headers that identify this session's Fabric connection to the backend (call on the Streamlit thread)."""
    headers = {}
    t = token()
    if t:
        headers["Authorization"] = f"Bearer {t}"
    if EMBEDDED:
        ft = _token("fabric")
        if ft:
            headers["X-Fabric-Token"] = ft
            pt = _token("powerbi")
            if pt:
                headers["X-PowerBI-Token"] = pt
    return headers


def _req(method: str, path: str, **kw):
    headers = {**(kw.pop("_auth", None) or auth_headers()), **kw.pop("headers", {})}
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


def get_many(paths: list[str], workers: int = 8) -> dict[str, dict | ApiError]:
    """GET several backend paths in parallel with the current person's identity."""
    from concurrent.futures import ThreadPoolExecutor
    h = auth_headers()

    def one(p):
        try:
            return p, _req("GET", p, _auth=h).json()
        except ApiError as e:
            return p, e
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(one, paths))


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
