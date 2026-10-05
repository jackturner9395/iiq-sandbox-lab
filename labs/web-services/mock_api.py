"""
Acme HR Portal - mock vendor REST API for practicing the IIQ Web Services connector.

Deliberately NOT SCIM. It has the quirks real vendor APIs have:
  - OAuth2 client-credentials token endpoint (form-encoded)
  - Every list response wrapped in {"meta": {...}, "data": [...]}
  - Nested profile object (profile.first, profile.last, profile.email)
  - Entitlements as an array of objects (roles[*].role_id), not strings
  - offset/limit paging with a has_more flag
  - Enable/disable via PATCH status, not separate endpoints
  - Entitlement add/remove via sub-resource endpoints
  - Error bodies in a custom shape: {"error": {"code": ..., "message": ...}}

Debug helpers (no auth):
  GET  /admin/log    - last 50 requests IIQ sent (method, path, query, body)
  POST /admin/reset  - restore seed data

Run:
  pip install fastapi uvicorn python-multipart
  uvicorn mock_api:app --host 0.0.0.0 --port 8085

Optional hard mode (roles NOT returned on the user list; forces a child/parent
endpoint or an After Operation rule):
  HARD_MODE=1 uvicorn mock_api:app --host 0.0.0.0 --port 8085
"""

import copy
import json
import os
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import JSONResponse

CLIENT_ID = os.getenv("CLIENT_ID", "iiq-lab")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "lab-secret-123")
TOKEN_TTL = int(os.getenv("TOKEN_TTL", "3600"))
HARD_MODE = os.getenv("HARD_MODE", "0") == "1"
MAX_LIMIT = 50

app = FastAPI(title="Acme HR Portal (mock)", version="1.0")

# ---------------------------------------------------------------- seed data
SEED_ROLES = [
    {"role_id": "R100", "name": "Viewer", "description": "Read-only access to HR records", "privileged": False},
    {"role_id": "R200", "name": "Editor", "description": "Edit employee records", "privileged": False},
    {"role_id": "R300", "name": "Payroll Admin", "description": "Run and approve payroll", "privileged": True},
    {"role_id": "R400", "name": "System Admin", "description": "Full administrative access", "privileged": True},
    {"role_id": "R500", "name": "Auditor", "description": "Read-only access to audit logs", "privileged": False},
]

_people = [
    ("jsmith", "John", "Smith", "E1001", "ACTIVE", ["R100", "R200"]),
    ("mjones", "Mary", "Jones", "E1002", "ACTIVE", ["R300"]),
    ("bwilliams", "Bob", "Williams", "E1003", "SUSPENDED", ["R100"]),
    ("agarcia", "Ana", "Garcia", "E1004", "ACTIVE", ["R400", "R500"]),
    ("dlee", "David", "Lee", "E1005", "ACTIVE", ["R100"]),
    ("kpatel", "Kiran", "Patel", "E1006", "ACTIVE", ["R200", "R300"]),
    ("tnguyen", "Thao", "Nguyen", "E1007", "ACTIVE", []),
    ("rbrown", "Rachel", "Brown", "E1008", "SUSPENDED", ["R500"]),
    ("cmartin", "Carlos", "Martin", "E1009", "ACTIVE", ["R100", "R500"]),
    ("lwhite", "Laura", "White", "E1010", "ACTIVE", ["R200"]),
    ("svc_reporting", "Reporting", "Service", None, "ACTIVE", ["R100"]),  # service acct, no emp_id -> orphan
    ("JTaylor", "James", "Taylor", "E1012", "ACTIVE", ["R400"]),  # mixed-case login on purpose
]


def _build_seed():
    users = {}
    for i, (login, first, last, emp, status, roles) in enumerate(_people, start=1):
        uid = f"u{1000 + i}"
        users[uid] = {
            "user_id": uid,
            "login": login,
            "emp_id": emp,
            "status": status,
            "profile": {
                "first": first,
                "last": last,
                "email": f"{login.lower()}@acme-lab.local",
                "department": ["Finance", "HR", "IT", "Operations"][i % 4],
            },
            "roles": [_role_ref(r) for r in roles],
            "last_login": f"2026-09-{(i % 28) + 1:02d}T13:00:00Z",
            "created": "2025-01-15T09:00:00Z",
        }
    return users


def _role_ref(role_id):
    r = next(x for x in SEED_ROLES if x["role_id"] == role_id)
    return {"role_id": r["role_id"], "name": r["name"]}


STATE = {"users": _build_seed(), "roles": copy.deepcopy(SEED_ROLES), "tokens": {}, "next_id": 1100}
REQUEST_LOG = deque(maxlen=50)


# ---------------------------------------------------------------- helpers
def err(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_auth(authorization: Optional[str]):
    if not authorization or not authorization.lower().startswith("bearer "):
        return err(401, "UNAUTHORIZED", "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    exp = STATE["tokens"].get(token)
    if not exp:
        return err(401, "INVALID_TOKEN", "Token not recognized")
    if exp < time.time():
        return err(401, "TOKEN_EXPIRED", "Token expired")
    return None


def user_view(u, include_roles=True):
    out = copy.deepcopy(u)
    if not include_roles:
        out.pop("roles", None)
    return out


@app.middleware("http")
async def log_requests(request: Request, call_next):
    body = await request.body()
    try:
        parsed = json.loads(body) if body else None
    except Exception:
        parsed = body.decode(errors="replace")
    response = await call_next(request)
    if not request.url.path.startswith("/admin"):
        REQUEST_LOG.appendleft({
            "time": now(),
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query),
            "body": parsed,
            "status": response.status_code,
        })
    return response


# ---------------------------------------------------------------- auth
@app.post("/oauth/token")
def token(
    grant_type: str = Form(...),
    client_id: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    # Accept creds in the form body OR as HTTP Basic (connectors do either)
    if (not client_id) and authorization and authorization.lower().startswith("basic "):
        import base64
        try:
            client_id, client_secret = base64.b64decode(authorization.split(" ", 1)[1]).decode().split(":", 1)
        except Exception:
            return err(400, "INVALID_REQUEST", "Bad basic auth header")
    if grant_type != "client_credentials":
        return err(400, "UNSUPPORTED_GRANT_TYPE", "Only client_credentials is supported")
    if client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
        return err(401, "INVALID_CLIENT", "Bad client_id or client_secret")
    tok = secrets.token_urlsafe(24)
    STATE["tokens"][tok] = time.time() + TOKEN_TTL
    return {"access_token": tok, "token_type": "Bearer", "expires_in": TOKEN_TTL}


# ---------------------------------------------------------------- users
@app.get("/api/v1/users")
def list_users(offset: int = 0, limit: int = 5, status: Optional[str] = None,
               authorization: Optional[str] = Header(None)):
    if (e := check_auth(authorization)):
        return e
    limit = max(1, min(limit, MAX_LIMIT))
    users = sorted(STATE["users"].values(), key=lambda u: u["user_id"])
    if status:
        users = [u for u in users if u["status"] == status.upper()]
    page = users[offset:offset + limit]
    return {
        "meta": {"offset": offset, "limit": limit, "total": len(users),
                 "has_more": offset + limit < len(users)},
        "data": [user_view(u, include_roles=not HARD_MODE) for u in page],
    }


@app.get("/api/v1/users/{user_id}")
def get_user(user_id: str, authorization: Optional[str] = Header(None)):
    if (e := check_auth(authorization)):
        return e
    u = STATE["users"].get(user_id)
    if not u:
        return err(404, "USER_NOT_FOUND", f"No user {user_id}")
    return {"data": user_view(u)}


@app.post("/api/v1/users", status_code=201)
async def create_user(request: Request, authorization: Optional[str] = Header(None)):
    if (e := check_auth(authorization)):
        return e
    try:
        body = await request.json()
    except Exception:
        return err(400, "INVALID_JSON", "Body is not valid JSON")
    login = (body.get("login") or "").strip()
    profile = body.get("profile") or {}
    if not login:
        return err(400, "VALIDATION_ERROR", "login is required")
    if not profile.get("email"):
        return err(400, "VALIDATION_ERROR", "profile.email is required")
    if any(u["login"].lower() == login.lower() for u in STATE["users"].values()):
        return err(409, "USER_EXISTS", f"login {login} already exists")
    uid = f"u{STATE['next_id']}"
    STATE["next_id"] += 1
    user = {
        "user_id": uid, "login": login, "emp_id": body.get("emp_id"),
        "status": "ACTIVE",
        "profile": {"first": profile.get("first"), "last": profile.get("last"),
                    "email": profile.get("email"), "department": profile.get("department")},
        "roles": [], "last_login": None, "created": now(),
    }
    for rid in body.get("role_ids") or []:
        if any(r["role_id"] == rid for r in STATE["roles"]):
            user["roles"].append(_role_ref(rid))
    STATE["users"][uid] = user
    return JSONResponse(status_code=201, content={"data": user_view(user)})


@app.patch("/api/v1/users/{user_id}")
async def update_user(user_id: str, request: Request, authorization: Optional[str] = Header(None)):
    if (e := check_auth(authorization)):
        return e
    u = STATE["users"].get(user_id)
    if not u:
        return err(404, "USER_NOT_FOUND", f"No user {user_id}")
    try:
        body = await request.json()
    except Exception:
        return err(400, "INVALID_JSON", "Body is not valid JSON")
    if "status" in body:
        if body["status"] not in ("ACTIVE", "SUSPENDED"):
            return err(400, "VALIDATION_ERROR", "status must be ACTIVE or SUSPENDED")
        u["status"] = body["status"]
    if "emp_id" in body:
        u["emp_id"] = body["emp_id"]
    for k, v in (body.get("profile") or {}).items():
        if k in u["profile"]:
            u["profile"][k] = v
    return {"data": user_view(u)}


@app.delete("/api/v1/users/{user_id}", status_code=204)
def delete_user(user_id: str, authorization: Optional[str] = Header(None)):
    if (e := check_auth(authorization)):
        return e
    if user_id not in STATE["users"]:
        return err(404, "USER_NOT_FOUND", f"No user {user_id}")
    del STATE["users"][user_id]
    return JSONResponse(status_code=204, content=None)


# ---------------------------------------------------------------- roles
@app.get("/api/v1/roles")
def list_roles(authorization: Optional[str] = Header(None)):
    if (e := check_auth(authorization)):
        return e
    return {"meta": {"total": len(STATE["roles"])}, "data": STATE["roles"]}


@app.get("/api/v1/users/{user_id}/roles")
def user_roles(user_id: str, authorization: Optional[str] = Header(None)):
    if (e := check_auth(authorization)):
        return e
    u = STATE["users"].get(user_id)
    if not u:
        return err(404, "USER_NOT_FOUND", f"No user {user_id}")
    return {"data": u["roles"]}


@app.post("/api/v1/users/{user_id}/roles")
async def add_role(user_id: str, request: Request, authorization: Optional[str] = Header(None)):
    if (e := check_auth(authorization)):
        return e
    u = STATE["users"].get(user_id)
    if not u:
        return err(404, "USER_NOT_FOUND", f"No user {user_id}")
    try:
        body = await request.json()
    except Exception:
        return err(400, "INVALID_JSON", "Body is not valid JSON")
    rid = body.get("role_id")
    if not any(r["role_id"] == rid for r in STATE["roles"]):
        return err(400, "ROLE_NOT_FOUND", f"No role {rid}")
    if not any(r["role_id"] == rid for r in u["roles"]):
        u["roles"].append(_role_ref(rid))
    return {"data": u["roles"]}


@app.delete("/api/v1/users/{user_id}/roles/{role_id}")
def remove_role(user_id: str, role_id: str, authorization: Optional[str] = Header(None)):
    if (e := check_auth(authorization)):
        return e
    u = STATE["users"].get(user_id)
    if not u:
        return err(404, "USER_NOT_FOUND", f"No user {user_id}")
    u["roles"] = [r for r in u["roles"] if r["role_id"] != role_id]
    return {"data": u["roles"]}


# ---------------------------------------------------------------- debug
@app.get("/admin/log")
def admin_log():
    return list(REQUEST_LOG)


@app.post("/admin/reset")
def admin_reset():
    STATE["users"] = _build_seed()
    STATE["roles"] = copy.deepcopy(SEED_ROLES)
    STATE["next_id"] = 1100
    REQUEST_LOG.clear()
    return {"reset": True}


@app.get("/health")
def health():
    return {"status": "ok", "hard_mode": HARD_MODE}
