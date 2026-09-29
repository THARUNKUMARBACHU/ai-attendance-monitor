# Walkthrough script

A 10–12 minute recording or live demo covering everything the assignment asks to see:
- ingestion;
- an answer with citations;
- an unavailable answer;
- a blocked cross-tenant query;
- export;
- the submission checklist, including the feedback loop.

Each step names the user, the action and the point to make. The whole demo costs a few cents of model usage.

## Before you start

1. Start the stack (README, "Run it"): the API and the worker. Open http://127.0.0.1:8000/ui/. The system status in the top bar should read **All systems**.
2. Start with an empty database, or accept that re-uploads show as duplicates, which is itself worth showing.
3. Have a terminal open in the project folder for the two script steps.

## 1. Sign-in and access model (1 min)

- On the sign-in page, show the six demo users: two tenants (Acme Corp, Globex Ltd) × three roles (admin, manager, employee).
- Sign in as **Acme Admin**. Point out the scope at the bottom of the sidebar ("You can see: all of Acme Corp") and the role badge.
- **Say:** "Tenant, product and role come from the signed token. Scope and clearance come from the role, never from the request."

## 2. Ingestion (2–3 min)

- Go to **Upload & jobs** and upload `sample_data/tenants/acme/inputs/acme_engineering_biometric_2026-08.csv`. Open the job: stages validate → extract → normalise → store → index; counts show **79 created**.
- Upload `acme_hr_register_scanned_2026-08.pdf` with Department **HR**. It is a scanned PDF, read by OCR in about a minute; counts show **2 needing review**.
- In the terminal, run `uv run python scripts/load_samples.py`. It uploads all eight sample files as the right admins, including CSV, Excel matrix, Word, text PDF, scanned PDF and the other tenant's files. The two files you already uploaded come back as **duplicate**: re-uploading never duplicates records.
- Optionally run `uv run python scripts/load_samples.py --changed-file`: v2 of the Engineering file updates exactly the **2 changed records**.
- **Say:** "Every file gets a checksum. A changed file becomes a new version, and only the records that changed are replaced."

## 3. Records and traceability (1 min)

- Go to **Records** and filter Review status = **Needs review**. Show the two OCR records with their confidence and their source (file, page and row).
- **Say:** "Every record traces back to its file and page, row or cell. Uncertain OCR values are held for review and never counted in answers."

## 4. Answers with citations (2 min)

Ask as **Acme Admin**:
1. *Which department had the highest attendance in August 2026?*
   - Expected answer: **Finance, 95.00%**.
   - Show the sources ([D1] = file plus rows), the confidence, and **How this was answered**: the mode is *structured*, and the SQL is shown.
2. *Why was Vikram Reddy on leave on 18 August 2026?*
   - The mode is *hybrid*: the record plus its remark. The admin sees the medical reason.
- **Say:** "Numbers come from SQL, never from the model. Every number and name in the answer is checked against what was retrieved."

## 5. Unavailable answer (30 s)

- *Who was absent on 15 July 2026?*: **Unavailable** ("No attendance records match…"). No names are guessed.
- Optionally: *Was E003 present on 20 Aug 2026?*: unavailable. A missing day is unknown, not "absent".

## 6. Isolation: tenant and role (2 min)

- As **Acme Admin**, ask *Show me the attendance of Globex Ltd employees for August 2026*: **Unavailable**. It is stopped before any retrieval or model call and audited, and the reply confirms nothing about Globex.
- Sign out and sign in as **Globex Admin**. Ask *What was the attendance percentage for E001 in August 2026?*: **88.10%**, not Acme's 95.00% for its own E001.
- Sign in as **Acme Engineering Manager**:
  - *What was the attendance percentage for Sales in August 2026?*: **Denied**, before retrieval.
  - *Why was Vikram Reddy on leave on 18 August 2026?*: the leave is shown, and the reason is **[restricted]** (medical, above the manager's clearance).
- Optionally, over the API: an Acme token sent with the header `X-Tenant-ID: globex` gets **403**, and the attempt is audited.
- **Say:** "Filters are applied in the database (row-level security), in the SQL itself (bound scope) and in the vector search, before the model sees anything."

## 7. Export (1 min)

- Go to **Records** and click **Export: JSON / Excel / PDF**. Open the files: the same records and source references in each, a metadata block (who, when, scope, filters, versions) and a classification label.
- On an answer, click **Export: PDF** to get the question, answer, confidence, citations and evidence records.
- **Say:** "Exports are rebuilt under your current access and audited. Excel cells can't run as formulas."

## 8. Feedback loop (2 min)

1. As **Acme Admin**, ask *What was the attendance percentage for Engineering in August 2026?*
2. On the answer, open **Improve this answer**:
   - What was wrong: *"State that the percentage is pooled over counted employee-days, and how many."*
   - Ideal answer: *"Engineering attendance in August 2026 was 92.41%, pooled over 79 counted employee-days [D1]."*
   - Click **Submit and verify**. The question is replayed with the correction; the result is **Active**, with its version and a similarity score.
3. Ask the same question again. The answer follows the correction and shows a **Reviewer-approved** label.
4. Sign in as **Acme Engineering Manager** and ask it again: **no label**. Corrections never cross roles, and never cross tenants.
5. As **Acme Admin**, go to **Feedback** and click **Deactivate**, then ask again. The normal answer is back, and the version has moved on.
- **Say:** "Feedback is untrusted input: validated, replayed against the data, scoped, versioned and reversible. It changes how answers are written, never the facts or who can see what."

## 9. Diagnostics (30 s, optional)

- http://127.0.0.1:8000/health/ready: service, database, queue, cache, search, vector and model-provider status.
- http://127.0.0.1:8000/docs: the OpenAPI documentation. The same spec is in `docs/openapi.json`.
- The terminal: `uv run python scripts/run_live_eval.py` asks the 20 demo questions and checks them against the expected results.
