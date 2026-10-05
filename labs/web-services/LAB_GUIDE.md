# Web Services Connector Lab: Acme HR Portal

Goal: onboard a non-SCIM REST app into your IIQ 8.4 sandbox end to end:
auth → aggregation → entitlements → rules → provisioning.

> **Syntax caveat:** placeholder and paging syntax below (`$plan.x$`, `$getobject.x$`,
> `$response.x$`, `TERMINATE_IF`, `$RECORDS_COUNT$`) is from the 8.x Web Services connector.
> If a field rejects it, check SailPoint's *Web Services Connector Guide*. Your project has the
> SCIM and JDBC guides but not this one, so grab it from Compass.

---

## 0. Run the API (10 min)

On the Linux box (or anywhere your IIQ sandbox can reach):

```bash
# Docker
docker build -t acme-hr-mock . && docker run -d --name acme-hr -p 8085:8085 acme-hr-mock

# or plain Python
pip install fastapi uvicorn python-multipart
uvicorn mock_api:app --host 0.0.0.0 --port 8085
```

Prove it works before touching IIQ. **If curl fails, IIQ will too.**

```bash
TOKEN=$(curl -s -X POST http://HOST:8085/oauth/token \
  -d grant_type=client_credentials -d client_id=iiq-lab -d client_secret=lab-secret-123 \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s "http://HOST:8085/api/v1/users?offset=0&limit=5" -H "Authorization: Bearer $TOKEN"
```

Interactive docs: `http://HOST:8085/docs`. Your debugging best friend: `http://HOST:8085/admin/log`
shows every request IIQ actually sent (method, path, query, body, status).

**Know the data before mapping it.** Read one user and note:
- the wrapper: `data` is the array, `meta` holds paging
- nested `profile.first/last/email/department`
- `roles` is an array of **objects**, not strings
- `status` is `ACTIVE` / `SUSPENDED`
- quirks seeded on purpose: `JTaylor` (mixed case), `svc_reporting` (no emp_id → orphan), two SUSPENDED users

---

## 1. Create the application

Applications → New → type **Web Services**.

| Setting | Value |
|---|---|
| Base URL | `http://HOST:8085` |
| Authentication | OAuth2 |
| Grant Type | Client Credentials |
| Token URL | `http://HOST:8085/oauth/token` |
| Client ID / Secret | `iiq-lab` / `lab-secret-123` |

---

## 2. Schemas

**Account schema** (`account`)

| Attribute | Type | Flags |
|---|---|---|
| user_id | string | **Identity Attribute** |
| login | string | **Display Attribute** |
| firstName, lastName, email, department, empId, status, lastLogin | string | |
| fullName | string | (filled by your customization rule) |
| roles | string | **Multi-valued, Entitlement, Managed** |

**Group schema**: add object type `role`

| Attribute | Flags |
|---|---|
| role_id | Identity |
| name | Display |
| description, privileged | |

Set the account schema's `roles` attribute to reference the `role` object type.

---

## 3. Operations (Connection Settings → Add Operation)

### Test Connection
- `GET /api/v1/users?offset=0&limit=1`, Root Path `$.data`

### Account Aggregation
- `GET /api/v1/users?offset=0&limit=5`
- Root Path: `$.data`
- Response attribute mapping (schema attr → path relative to root):

| Schema | Path |
|---|---|
| user_id | `user_id` |
| login | `login` |
| firstName | `profile.first` |
| lastName | `profile.last` |
| email | `profile.email` |
| department | `profile.department` |
| empId | `emp_id` |
| status | `status` |
| lastLogin | `last_login` |
| roles | `roles[*].role_id` |

- Paging (limit 5 forces 3 pages across 12 users, so you'll know if paging is broken):
```
TERMINATE_IF $RECORDS_COUNT$ < 5
$offset$ = $offset$ + 5
$endpoint.fullUrl$ = $application.baseUrl$ + "/api/v1/users?offset=" + $offset$ + "&limit=5"
```
Watch `/admin/log` to see exactly which offsets IIQ requested. If the second call isn't
`offset=5`, the offset variable isn't initializing the way you expect, and that's your first
real debugging exercise.

### Get Object (single account refresh)
- `GET /api/v1/users/$getobject.nativeIdentity$`, Root Path `$.data`, same mappings

### Group Aggregation
- `GET /api/v1/roles`, Root Path `$.data`, Object Type `role`
- Map role_id, name, description, privileged

**Checkpoint:** run Account Aggregation then Entitlement Aggregation.
- 12 accounts, 5 roles in the entitlement catalog
- `/admin/log` shows 3 user calls (offset 0, 5, 10)
- Roles on accounts show R100, R200, etc. (not `{role_id=...}` maps, which means the
  `[*].role_id` path is wrong)

---

## 4. Rules (you write the Java)

Import `rules/Rule_AcmeHR_Customization.xml` and `rules/Rule_AcmeHR_BeforeOperation.xml`.
Both import and run unchanged; each has numbered TODOs with hints. **Write them before asking
for an answer.**

1. **Customization rule** (Application → Rules → Customization): disable SUSPENDED accounts via
   `IIQDisabled`, lowercase logins, build `fullName`.
   Verify: bwilliams and rbrown are disabled; JTaylor appears as `jtaylor`.
2. **Before Operation rule** (on Account Aggregation first): log the URL, add an `X-Request-Id`
   header. TODO 3 comes in step 6.

Debug loop: edit the rule in the Debug page → save → rerun aggregation → check logs. Use the
console to see raw connector output with no rules applied:
```
connectorDebug "Acme HR" iterate
```

---

## 5. Correlation

Correlate `empId` → an identity attribute (employee ID). Your sandbox identities won't have
E1001-E1012 out of the box, so either:
- edit `_people` in `mock_api.py` to use real sandbox identity names and employee IDs, then `POST /admin/reset`, or
- correlate on `email` and adjust the seeded emails to match sandbox identities.

Expected: `svc_reporting` stays an **orphan** (no emp_id). Decide how you'd handle it in
production (service-account owner assignment, a separate correlation rule) and write it down.

---

## 6. Provisioning

| Operation | Method / URL | Body |
|---|---|---|
| Create Account | `POST /api/v1/users` | see below |
| Add Entitlement | `POST /api/v1/users/$plan.nativeIdentity$/roles` | `{"role_id": "$plan.roles$"}` |
| Remove Entitlement | `DELETE /api/v1/users/$plan.nativeIdentity$/roles/$plan.roles$` | none |
| Disable Account | `PATCH /api/v1/users/$plan.nativeIdentity$` | `{"status": "SUSPENDED"}` |
| Enable Account | `PATCH /api/v1/users/$plan.nativeIdentity$` | `{"status": "ACTIVE"}` |
| Delete Account | `DELETE /api/v1/users/$plan.nativeIdentity$` | none |

Create Account body:
```json
{
  "login": "$plan.login$",
  "emp_id": "$plan.empId$",
  "profile": {
    "first": "$plan.firstName$",
    "last": "$plan.lastName$",
    "email": "$plan.email$",
    "department": "$plan.department$"
  }
}
```
Create Account response: Root Path `$.data`, map `user_id` → `user_id` so IIQ learns the new
native identity. **This is the most common thing people miss.** Without it, IIQ has no ID for
the next call.

Add a **Create provisioning policy** with fields login, firstName, lastName, email, empId,
department, populated from identity attributes.

**Test sequence** (watch `/admin/log` after each):
1. Request the Acme HR app for a sandbox identity → new user in the API, Link created in IIQ
2. Request role R200 → POST to `/roles`
3. Remove it → DELETE
4. Disable via Manage Accounts → PATCH SUSPENDED
5. Finish Before rule TODO 3, then request R400 → the request fails with your message

---

## 7. Break it on purpose (where the real learning is)

| Break | What you should see | Where |
|---|---|---|
| Wrong client_secret | 401 INVALID_CLIENT on token call | Test Connection error, `/admin/log` |
| Root Path `$.users` | 0 accounts, no error | Aggregation results |
| Remove `TERMINATE_IF` | Endless paging / hung task | `/admin/log` fills with offset 15, 20... |
| `roles` path → `roles` | Entitlements show as maps | Account attributes |
| No Create response mapping | Create succeeds, follow-up ops fail | Provisioning transaction |
| Send create with an existing login | 409 USER_EXISTS | Provisioning error message |

For each one, write a single line: symptom → cause → fix. That list becomes your troubleshooting
runbook and interview material.

---

## Stretch: hard mode

```bash
HARD_MODE=1 uvicorn mock_api:app --host 0.0.0.0 --port 8085
```
The user list stops returning `roles`. You have to fetch them from
`GET /api/v1/users/{id}/roles`. Two ways to solve it:
1. **Child operation**: add an operation with Parent Endpoint = Account Aggregation and URL
   `/api/v1/users/$response.user_id$/roles` (the connector-native approach)
2. **After Operation rule** on aggregation that calls the endpoint per account (works, but is
   N+1 calls; know why that's worse at 50k accounts)

Build #1, understand #2.

---

## Done = portfolio artifact

Export the application and rules (scrub the secret) and commit them here in
`labs/web-services/`, along with your break/fix runbook (`RUNBOOK.md`).
