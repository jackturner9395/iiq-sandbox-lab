# Certification Detail Report

A custom IdentityIQ report that lists everything about certifications in one place. It covers every certification
type, and every sub-certification under them (reassigned certifications and a manager's subordinate certifications).

**One row per certification item**, which is one piece of access for one user. Each row has:

| Group | Columns |
|---|---|
| Certification | Campaign, Certification, Parent certification, Level (Top level / Sub-certification), Certification type, Phase, Certifier, Created, Due, Signed off, Signed off by |
| Who's certified | User certified (ID), User certified (name), User's manager |
| Access | Item type (Role / Entitlement / Account / Policy violation / ...), Application, Account, Role, Entitlement attribute, Entitlement value, Entitlement display name, Access (full description) |
| Decision | Item status, Decision (Open / Approved / Revoked / Allowed (exception) / Acknowledged / Reassigned), Decided by, Decision date, Decision comments, Allowed until, Revocation status, Item delegated to, User delegated to |

The **Certifier** column shows who the certification is assigned to, as "Display Name (ID)". **Decided by** shows
who actually made the decision, which differs when an item was delegated. For a sub-certification, **Parent
certification** names the certification it came from.

The summary shows the number of campaigns, certifications, sub-certifications, identities certified, items, and items
decided. The chart shows decisions by item type.

## Filters (all optional)

Campaigns, certification types, certifiers, identities certified, applications, certification status (All / Open /
Signed), sub-certifications (All / Top level only / Sub-certifications only), decision (multi-select, includes
Open), and created / signed off / due date ranges.

## Import

| File | Contains |
|---|---|
| `config/Certification-Detail-Report-Import.xml` | Form "Certification Detail Report Form" and report "Certification Detail Report" |

Use **Global Settings → Import from File**, or run `import Certification-Detail-Report-Import.xml` in the IIQ
console. It has no environment-specific values, so the same file works in every environment, and re-importing
it is safe.

To run it, go to **Intelligence → Reports → Access Review and Certification Reports → Certification Detail
Report**. Set the filters on the **Certification filters** tab, then click **Save and Execute** (or **Execute**)
and download the CSV or PDF from the result. CSV is the better format for this many columns. To trim columns, use the report's **Layout** tab.

**Who can see it:** System Administrators, plus anyone with the OOTB right `FullAccessCertificationDecisionReport`
(the same right as the OOTB Access Review Decision Report). To change that, edit `<RequiredRights>` in
`config/TaskDefinition/Report-Certification-Detail.xml`.

## Source files

- `config/Form/Form-Certification-Detail-Report.xml`
- `config/TaskDefinition/Report-Certification-Detail.xml`

After editing either file, rebuild the import file. It's the form followed by the report, inside one `<sailpoint>`
element, without their XML declaration and DOCTYPE lines.

## Notes

- **Revocation status** fills in once revocations are kicked off after sign-off. It comes from the same OOTB
  helper as the Access Review Decision Report.
- **Certifier**, **Signed off by**, and **Entitlement display name** do a small lookup per row. That's fine for
  tens of thousands of rows. For a very large history, filter by campaign or date.
- Archived (deleted) certifications aren't included, the same as the OOTB live reports.

## Test results (sandbox, 2026-10-21)

The test campaign was a manager certification for Kimberly Anderson with subordinate certifications turned on. It
produced 1 top-level certification and 3 sub-certifications (one for each of her managers), with 34 users and 301
items. The test decisions were approvals, 3 revocations, and 1 allowed exception, and one sub-certification was
signed off.

| Check | Result |
|---|---|
| No filters | 301 rows (30 top level + 271 sub-certification) |
| Sub-certifications only / Top level only | 271 / 30 |
| Certifier = Sarah Allen | 106 (her sub-certification) |
| Decision = Open / Open + Revoked | 142 / 145 |
| Certification status Signed / Open | 106 / 195 |
| Certification type Manager / Application Owner | 301 / 0 |
| Campaign, identity, application filters | Correct counts per application, 12 rows for one user |
| Summary | 1 campaign, 4 certifications, 3 sub-certifications, 34 identities, 301 items, 159 decided |
| Full report task | Success; CSV has a header plus 301 rows, and the PDF was generated |

Entitlement GUIDs (Copperleaf) show their display name, for example "Report Viewer". Allowed exceptions show the
"Allowed until" date and the comment. The data source takes 0.3 s for all 301 rows, and the full task, including
the PDF, takes about 12 s.
