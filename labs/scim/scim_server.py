"""
Mock SCIM 2.0 server for IdentityIQ connector testing (stand-in for a vendor like Copperleaf).
Standard library only - no pip installs.

  python scim_server.py                      # http://localhost:8443/scim/v2, bearer token below
  python scim_server.py --port 9000 --token mytoken --reset

Auth: Bearer <token>  OR  Basic iiq:iiq
State persists to scim_data.json next to this file (use --reset to reseed).
Every request is logged to the console and scim_requests.log so you can see exactly what IIQ sends.
"""
import argparse, base64, csv, json, os, re, threading, uuid, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "scim_data.json")
LOG_FILE = os.path.join(HERE, "scim_requests.log")
BASE = "/scim/v2"
U_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
G_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENT = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERR = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCHOP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
MAX_PAGE = 200
LOCK = threading.Lock()
CFG = {"token": "copperleaf-test-token", "basic": "iiq:iiq", "host": "http://localhost:8443"}
DB = {"Users": {}, "Groups": {}}

def now(): return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def save():
    with open(DATA_FILE, "w") as f: json.dump(DB, f, indent=1)

# ------------------------------------------------------------------ seed
GROUPS = {
 "Copperleaf Administrator": "Full system administration",
 "Portfolio Manager": "Create and manage investment portfolios",
 "Investment Planner": "Build and edit investment plans",
 "Asset Analyst": "Asset risk and condition analysis",
 "Finance Approver": "Approve capital investment decisions",
 "Executive Viewer": "Read-only executive dashboards",
 "Report Viewer": "Read-only reporting",
}
def seed():
    DB["Users"].clear(); DB["Groups"].clear()
    rows = []
    for p in [r"C:\IIQSandbox\data\hr_employees.csv", os.path.join(HERE, "hr_employees.csv")]:
        if os.path.exists(p):
            with open(p, newline="") as f: rows = list(csv.DictReader(f)); break
    if not rows:  # fallback if HR feed not found
        rows = [dict(employeeId=f"E{2000+i}", firstName=f"Test{i}", lastName="User", email=f"test{i}@sandbox.local",
                     department="Finance", title="Analyst", status="Active") for i in range(1, 21)]
    gids = {}
    for name, desc in GROUPS.items():
        gid = str(uuid.uuid4()); gids[name] = gid
        DB["Groups"][gid] = {"schemas": [G_SCHEMA], "id": gid, "displayName": name, "description": desc, "members": [],
                             "meta": {"resourceType": "Group", "created": now(), "lastModified": now()}}
    keep = {"Finance", "Grid Operations", "Information Technology", "Supply Chain", "Executive", "Field Services"}
    for r in rows:
        if r.get("department") not in keep: continue
        if r.get("department") == "Field Services" and "Supervisor" not in r.get("title", ""): continue
        uid = str(uuid.uuid4())
        u = {"schemas": [U_SCHEMA, ENT], "id": uid, "externalId": r["employeeId"], "userName": r["email"],
             "name": {"givenName": r["firstName"], "familyName": r["lastName"], "formatted": f'{r["firstName"]} {r["lastName"]}'},
             "displayName": f'{r["firstName"]} {r["lastName"]}', "title": r.get("title", ""),
             "emails": [{"value": r["email"], "type": "work", "primary": True}],
             "active": r.get("status", "Active") == "Active",
             ENT: {"employeeNumber": r["employeeId"], "department": r.get("department", "")},
             "meta": {"resourceType": "User", "created": now(), "lastModified": now()}}
        DB["Users"][uid] = u
        d, t = r.get("department"), r.get("title", "")
        mine = ["Report Viewer"]
        if d == "Executive" or t.startswith("VP"): mine = ["Executive Viewer"]
        elif d == "Finance": mine += ["Finance Approver"] if "Manager" in t else ["Investment Planner"]
        elif d == "Grid Operations": mine += ["Asset Analyst"] + (["Portfolio Manager"] if "Manager" in t else [])
        elif d == "Supply Chain": mine += ["Investment Planner"]
        elif d == "Information Technology" and t in ("Application Developer", "Systems Administrator"): mine += ["Copperleaf Administrator"]
        elif d == "Field Services": mine += ["Asset Analyst"]
        for g in mine: DB["Groups"][gids[g]]["members"].append({"value": uid, "display": u["userName"]})
    # orphan / service accounts IIQ should NOT correlate
    for un in ["svc_copperleaf_integration", "cl_admin_breakglass", "former.contractor@vendor.com"]:
        uid = str(uuid.uuid4())
        DB["Users"][uid] = {"schemas": [U_SCHEMA], "id": uid, "userName": un, "displayName": un, "active": True,
                            "name": {"givenName": "", "familyName": un}, "emails": [],
                            "meta": {"resourceType": "User", "created": now(), "lastModified": now()}}
        DB["Groups"][gids["Copperleaf Administrator"]]["members"].append({"value": uid, "display": un})
    save()

# ------------------------------------------------------------------ helpers
def render_user(u):
    u = json.loads(json.dumps(u))
    u["groups"] = [{"value": g["id"], "display": g["displayName"], "$ref": f'{CFG["host"]}{BASE}/Groups/{g["id"]}', "type": "direct"}
                   for g in DB["Groups"].values() if any(m["value"] == u["id"] for m in g["members"])]
    u["meta"]["location"] = f'{CFG["host"]}{BASE}/Users/{u["id"]}'
    return u
def render_group(g):
    g = json.loads(json.dumps(g))
    for m in g["members"]: m["$ref"] = f'{CFG["host"]}{BASE}/Users/{m["value"]}'
    g["meta"]["location"] = f'{CFG["host"]}{BASE}/Groups/{g["id"]}'
    return g

def get_path(obj, path):
    """resolve attr path incl. enterprise URN and multi-valued sub-attrs (returns list of values)"""
    if path.startswith(ENT + ":"):
        obj, path = obj.get(ENT, {}), path[len(ENT) + 1:]
    cur = [obj]
    for part in path.split("."):
        nxt = []
        for c in cur:
            if isinstance(c, dict):
                v = next((c[k] for k in c if k.lower() == part.lower()), None)
                if isinstance(v, list): nxt.extend(v)
                elif v is not None: nxt.append(v)
        cur = nxt
    return cur

FILTER_RE = re.compile(r'^\s*([\w.:\-]+)\s+(eq|ne|co|sw|ew|pr)\s*(?:"((?:[^"\\]|\\.)*)"|(true|false|\d+))?\s*$', re.I)
def match(obj, flt):
    if not flt: return True
    for clause in re.split(r'\s+and\s+', flt, flags=re.I):
        m = FILTER_RE.match(clause)
        if not m: raise ValueError(clause)
        attr, op, sval, lit = m.groups(); op = op.lower()
        val = sval if sval is not None else ({"true": True, "false": False}.get(lit.lower(), lit) if lit else None)
        vals = get_path(obj, attr)
        vals = [v.get("value") if isinstance(v, dict) else v for v in vals]
        if op == "pr": ok = len(vals) > 0
        else:
            def cmp(v):
                if isinstance(val, bool) or isinstance(v, bool): return v == val
                a, b = str(v).lower(), str(val).lower()
                return {"eq": a == b, "ne": a != b, "co": b in a, "sw": a.startswith(b), "ew": a.endswith(b)}[op]
            ok = any(cmp(v) for v in vals) if op != "ne" else all(cmp(v) for v in vals)
        if not ok: return False
    return True

def set_attr(obj, path, value, op):
    """apply a PATCH op to a User/Group for a (possibly dotted / urn / filtered) path"""
    if path.startswith(ENT + ":"):
        sub = obj.setdefault(ENT, {}); key = path[len(ENT) + 1:]
        if op == "remove": sub.pop(key, None)
        else: sub[key] = value
        return
    fm = re.match(r'^(\w+)\[(.+)\](?:\.(\w+))?$', path)
    if fm:  # e.g. emails[type eq "work"].value  /  members[value eq "x"]
        attr, flt, subattr = fm.groups()
        arr = obj.setdefault(attr, [])
        hits = [e for e in arr if match(e, flt)]
        if op == "remove":
            obj[attr] = [e for e in arr if e not in hits]; return
        if not hits:
            e = {}; fm2 = FILTER_RE.match(flt)
            if fm2: e[fm2.group(1)] = fm2.group(3)
            arr.append(e); hits = [e]
        for e in hits:
            if subattr: e[subattr] = value
            elif isinstance(value, dict): e.update(value)
        return
    parts = path.split(".")
    tgt = obj
    for p in parts[:-1]: tgt = tgt.setdefault(p, {})
    k = parts[-1]
    if op == "remove": tgt.pop(k, None)
    elif op == "add" and isinstance(tgt.get(k), list):
        add = value if isinstance(value, list) else [value]
        for v in add:
            if v not in tgt[k]: tgt[k].append(v)
    else: tgt[k] = value

# ------------------------------------------------------------------ discovery docs
def attr(name, typ="string", multi=False, req=False, sub=None, mut="readWrite", uniq="none"):
    a = {"name": name, "type": typ, "multiValued": multi, "required": req, "caseExact": False,
         "mutability": mut, "returned": "default", "uniqueness": uniq}
    if sub: a["subAttributes"] = sub
    return a
USER_SCHEMA = {"id": U_SCHEMA, "name": "User", "description": "User Account", "attributes": [
    attr("userName", req=True, uniq="server"), attr("externalId"), attr("displayName"), attr("title"),
    attr("name", "complex", sub=[attr("givenName"), attr("familyName"), attr("formatted")]),
    attr("emails", "complex", True, sub=[attr("value"), attr("type"), attr("primary", "boolean")]),
    attr("active", "boolean"),
    attr("groups", "complex", True, mut="readOnly", sub=[attr("value", mut="readOnly"), attr("display", mut="readOnly"), attr("$ref", "reference", mut="readOnly")]),
], "meta": {"resourceType": "Schema", "location": f"{BASE}/Schemas/{U_SCHEMA}"}}
ENT_SCHEMA = {"id": ENT, "name": "EnterpriseUser", "description": "Enterprise User", "attributes": [
    attr("employeeNumber"), attr("department"),
    attr("manager", "complex", sub=[attr("value"), attr("displayName", mut="readOnly")])],
    "meta": {"resourceType": "Schema", "location": f"{BASE}/Schemas/{ENT}"}}
GROUP_SCHEMA = {"id": G_SCHEMA, "name": "Group", "description": "Group", "attributes": [
    attr("displayName", req=True), attr("description"),
    attr("members", "complex", True, sub=[attr("value", mut="immutable"), attr("display"), attr("$ref", "reference", mut="immutable")])],
    "meta": {"resourceType": "Schema", "location": f"{BASE}/Schemas/{G_SCHEMA}"}}
RESOURCE_TYPES = [
    {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "User", "name": "User", "endpoint": "/Users",
     "schema": U_SCHEMA, "schemaExtensions": [{"schema": ENT, "required": False}], "meta": {"resourceType": "ResourceType", "location": f"{BASE}/ResourceTypes/User"}},
    {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "Group", "name": "Group", "endpoint": "/Groups",
     "schema": G_SCHEMA, "meta": {"resourceType": "ResourceType", "location": f"{BASE}/ResourceTypes/Group"}}]
SPC = {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
       "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
       "filter": {"supported": True, "maxResults": MAX_PAGE}, "changePassword": {"supported": False},
       "sort": {"supported": False}, "etag": {"supported": False},
       "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer Token", "description": "Static bearer token", "primary": True},
                                 {"type": "httpbasic", "name": "HTTP Basic", "description": "Basic auth"}],
       "meta": {"resourceType": "ServiceProviderConfig", "location": f"{BASE}/ServiceProviderConfig"}}

# ------------------------------------------------------------------ HTTP
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _log(self, code, body=None):
        line = f'{now()} {self.command} {self.path} -> {code}'
        print(line, flush=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
            if body: f.write("   body: " + json.dumps(body)[:2000] + "\n")
    def send(self, code, obj=None, reqbody=None):
        data = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/scim+json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data: self.wfile.write(data)
        self._log(code, reqbody)
    def err(self, code, detail, stype=None, reqbody=None):
        e = {"schemas": [ERR], "status": str(code), "detail": detail}
        if stype: e["scimType"] = stype
        self.send(code, e, reqbody)
    def authed(self):
        a = self.headers.get("Authorization", "")
        if a == f'Bearer {CFG["token"]}': return True
        if a.startswith("Basic "):
            try: return base64.b64decode(a[6:]).decode() == CFG["basic"]
            except Exception: return False
        return False
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n: return {}
        return json.loads(self.rfile.read(n) or b"{}")
    def route(self):
        u = urlparse(self.path); p = u.path.rstrip("/")
        if not p.startswith(BASE): return None, None, None, u
        parts = p[len(BASE):].strip("/").split("/", 1)
        return parts[0], (parts[1] if len(parts) > 1 else None), parse_qs(u.query), u

    def do_GET(self):
        if not self.authed(): return self.err(401, "Unauthorized")
        res, rid, qs, _ = self.route()
        if res == "ServiceProviderConfig": return self.send(200, SPC)
        if res == "ResourceTypes":
            if rid: return self.send(200, next((r for r in RESOURCE_TYPES if r["id"] == rid), None) or {}) 
            return self.send(200, {"schemas": [LIST], "totalResults": 2, "itemsPerPage": 2, "startIndex": 1, "Resources": RESOURCE_TYPES})
        if res == "Schemas":
            allS = [USER_SCHEMA, ENT_SCHEMA, GROUP_SCHEMA]
            if rid:
                s = next((x for x in allS if x["id"] == rid), None)
                return self.send(200, s) if s else self.err(404, "Schema not found")
            return self.send(200, {"schemas": [LIST], "totalResults": 3, "itemsPerPage": 3, "startIndex": 1, "Resources": allS})
        if res in ("Users", "Groups"):
            rend = render_user if res == "Users" else render_group
            with LOCK:
                if rid:
                    o = DB[res].get(rid)
                    return self.send(200, rend(o)) if o else self.err(404, f"{res[:-1]} {rid} not found")
                flt = (qs.get("filter") or [""])[0]
                try:
                    items = [rend(o) for o in DB[res].values()]
                    items = [o for o in items if match(o, flt)]
                except ValueError as e:
                    return self.err(400, f"Unsupported filter: {e}", "invalidFilter")
            start = max(1, int((qs.get("startIndex") or ["1"])[0]))
            count = min(MAX_PAGE, max(0, int((qs.get("count") or [str(MAX_PAGE)])[0])))
            page = items[start - 1:start - 1 + count]
            excl = (qs.get("excludedAttributes") or [""])[0]
            if "members" in excl:
                for g in page: g.pop("members", None)
            return self.send(200, {"schemas": [LIST], "totalResults": len(items), "itemsPerPage": len(page), "startIndex": start, "Resources": page})
        return self.err(404, "Not found")

    def do_POST(self):
        if not self.authed(): return self.err(401, "Unauthorized")
        res, rid, _, _ = self.route()
        b = self.body()
        if res == "Users":
            if not b.get("userName"): return self.err(400, "userName is required", "invalidValue", b)
            with LOCK:
                if any(u["userName"].lower() == b["userName"].lower() for u in DB["Users"].values()):
                    return self.err(409, f'userName {b["userName"]} already exists', "uniqueness", b)
                uid = str(uuid.uuid4())
                u = {k: v for k, v in b.items() if k not in ("id", "meta", "groups")}
                u.update({"id": uid, "schemas": list({U_SCHEMA, *(b.get("schemas") or [])}), "active": b.get("active", True),
                          "meta": {"resourceType": "User", "created": now(), "lastModified": now()}})
                DB["Users"][uid] = u; save()
                return self.send(201, render_user(u), b)
        if res == "Groups":
            if not b.get("displayName"): return self.err(400, "displayName is required", "invalidValue", b)
            with LOCK:
                gid = str(uuid.uuid4())
                g = {"schemas": [G_SCHEMA], "id": gid, "displayName": b["displayName"], "description": b.get("description", ""),
                     "members": [{"value": m["value"]} for m in b.get("members", [])],
                     "meta": {"resourceType": "Group", "created": now(), "lastModified": now()}}
                DB["Groups"][gid] = g; save()
                return self.send(201, render_group(g), b)
        return self.err(404, "Not found", reqbody=b)

    def do_PUT(self):
        if not self.authed(): return self.err(401, "Unauthorized")
        res, rid, _, _ = self.route()
        b = self.body()
        if res not in ("Users", "Groups") or not rid: return self.err(404, "Not found", reqbody=b)
        with LOCK:
            o = DB[res].get(rid)
            if not o: return self.err(404, f"{rid} not found", reqbody=b)
            keep = {"id": rid, "meta": o["meta"]}
            o.clear(); o.update({k: v for k, v in b.items() if k not in ("groups",)}); o.update(keep)
            if res == "Groups": o["members"] = [{"value": m["value"]} for m in b.get("members", [])]
            o["meta"]["lastModified"] = now(); save()
            return self.send(200, (render_user if res == "Users" else render_group)(o), b)

    def do_PATCH(self):
        if not self.authed(): return self.err(401, "Unauthorized")
        res, rid, _, _ = self.route()
        b = self.body()
        if res not in ("Users", "Groups") or not rid: return self.err(404, "Not found", reqbody=b)
        with LOCK:
            o = DB[res].get(rid)
            if not o: return self.err(404, f"{rid} not found", reqbody=b)
            for opx in b.get("Operations", []):
                op = opx.get("op", "").lower(); path = opx.get("path"); val = opx.get("value")
                if op not in ("add", "replace", "remove"): return self.err(400, f"Bad op {op}", "invalidSyntax", b)
                if path in (None, ""):
                    if not isinstance(val, dict): return self.err(400, "value must be an object when path is omitted", "invalidSyntax", b)
                    for k, v in val.items():
                        if k == "members" and res == "Groups":
                            set_attr(o, "members", [{"value": m["value"]} for m in v], op)
                        elif k == ENT and isinstance(v, dict):
                            o.setdefault(ENT, {}).update(v)
                        else: set_attr(o, k, v, op)
                elif res == "Groups" and path.lower() == "members":
                    vals = val if isinstance(val, list) else [val] if val else []
                    if op == "remove" and not vals: o["members"] = []
                    elif op == "remove":
                        ids = {v["value"] for v in vals}; o["members"] = [m for m in o["members"] if m["value"] not in ids]
                    else:
                        if op == "replace": o["members"] = []
                        have = {m["value"] for m in o["members"]}
                        for v in vals:
                            if v["value"] not in DB["Users"]: return self.err(400, f'User {v["value"]} not found', "invalidValue", b)
                            if v["value"] not in have: o["members"].append({"value": v["value"]})
                elif res == "Users" and path.lower() == "groups":
                    return self.err(400, "groups is readOnly on User - PATCH /Groups/{id} members instead", "mutability", b)
                else:
                    set_attr(o, path, val, op)
            o["meta"]["lastModified"] = now(); save()
            return self.send(200, (render_user if res == "Users" else render_group)(o), b)

    def do_DELETE(self):
        if not self.authed(): return self.err(401, "Unauthorized")
        res, rid, _, _ = self.route()
        if res not in ("Users", "Groups") or not rid: return self.err(404, "Not found")
        with LOCK:
            if rid not in DB[res]: return self.err(404, f"{rid} not found")
            del DB[res][rid]
            if res == "Users":
                for g in DB["Groups"].values(): g["members"] = [m for m in g["members"] if m["value"] != rid]
            save()
        return self.send(204)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--token", default=CFG["token"])
    ap.add_argument("--reset", action="store_true", help="reseed from the HR feed")
    a = ap.parse_args()
    CFG["token"] = a.token; CFG["host"] = f"http://localhost:{a.port}"
    if a.reset or not os.path.exists(DATA_FILE): seed()
    else:
        with open(DATA_FILE) as f: DB.update(json.load(f))
    print(f"Mock SCIM 2.0 (Copperleaf stand-in) on http://localhost:{a.port}{BASE}")
    print(f"  Bearer token: {CFG['token']}   |   Basic: {CFG['basic']}")
    print(f"  {len(DB['Users'])} users, {len(DB['Groups'])} groups. Requests logged to {LOG_FILE}")
    ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()
