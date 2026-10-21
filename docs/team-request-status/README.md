# My Team's Requests

A read-only QuickLink that lets managers see where their direct reports' access requests are, so they don't
have to call the helpdesk.

- **Who sees it:** managers only (the OOTB **Manager** dynamic scope, `managerStatus = true`).
- **What it shows:** requests for the manager's direct reports from the last 60 days (at most 50), newest first.
  The manager can narrow it to one team member. Each request shows its number, person, date, requester,
  an overall status, and a status per item.
- **Changes nothing.** The workflow is transient, so it leaves no workflow case behind.
- **Scope:** only the launcher's own direct reports are ever queried. A team member picked on the form is checked
  against that list.

## Statuses

| Shown as | When |
|---|---|
| In progress: being set up manually by *owner* | An open manual work item. This includes requests IIQ marks **Failed** because the app is read-only or disconnected and fell back to manual fulfillment |
| Done (set up manually by *owner*) | The manual work item was completed |
| Waiting on approval from *approver* | An open approval work item |
| Sent to *app*, waiting for it to confirm (usually after the next sync) | Provisioned; IIQ is waiting for aggregation to confirm it |
| In progress: the application didn't respond, IdentityIQ will retry automatically | An item is in retry |
| Done | All items finished |
| Rejected by *approver* | Every item was rejected (if only some were, the status says "some items were rejected") |
| Failed / Partly done: contact the helpdesk with request #*N* | An item failed and there is **no** manual work item, so it's a real failure |
| Cancelled | The request was terminated |

Account attribute lines (the "Set" items on a new account) are hidden; the "New account" line covers them.
Entitlements show their display name when one exists.

**Limitation:** the manual-fulfillment wording relies on the manual work item having the request ID
(`WorkItem.identityRequestId`). LCM Provisioning's manual actions set it. A custom workflow that creates its own
manual work item must set it too, or those requests fall back to the request's raw status.

## Import

The bundle has no environment-specific values, so it imports as-is into any environment.

**From the UI:** Global Settings → Import from File → choose `config/Team-Request-Status-Import.xml` → Import.

**From the console** (on the IIQ server, from `WEB-INF/bin`):

```
iiq console
> import /path/to/Team-Request-Status-Import.xml
```

It creates three objects:

| Type | Name |
|---|---|
| Rule | Team Request Status Library |
| Workflow | Team Request Status Workflow |
| QuickLink | My Team's Requests |

**Before importing,** check that the **Manager** dynamic scope exists and still matches `managerStatus = true`
(Global Settings → Quicklink Populations). If your environment uses a different manager population, change the
`DynamicScopeRef` in `config/QuickLink/QuickLink-Team-Request-Status.xml` and rebuild.

To remove it, delete those three objects (QuickLink first).

## Settings

At the top of `config/Rule/Rule-Team-Request-Status-Library.xml`:

| Setting | Default | Meaning |
|---|---|---|
| `TRS_DAYS` | 60 | How far back to look |
| `TRS_MAX` | 50 | Most requests shown at once |

After editing a source file, rebuild the bundle: concatenate the rule, workflow and QuickLink (in that order)
inside one `<sailpoint>` element, without their XML declaration and DOCTYPE lines.

## Test results (sandbox, 2026-10-21)

- Status logic was checked against the existing sandbox requests for five managers: a manual-fulfillment request
  showed as "being set up manually", real failures showed as Failed, and provisioned-but-unconfirmed requests showed
  as "Sent…".
- Filtering: a direct report returns only their requests; a person who isn't a direct report returns nothing.
- Logged in as a manager, the QuickLink opened and the form rendered.
- Timing: 48 requests with about 5 items each took 0.2 to 0.6 s to build. Every lookup uses an indexed column
  (request target and created date, work item request ID, managed attribute application/attribute/value).
