# Test summary

The assignment's 14 mandatory scenarios (section 7) are mapped below to the automated tests that prove them. The **live evaluation** runs the same scenarios end to end with the real model.

- **Automated tests** use a scripted (mock) model. They are deterministic and cost nothing.
- **Unit tests** need no servers.
- **Integration tests** provision a throwaway PostgreSQL database with its own roles, and drop it afterwards.
- **OCR tests** run when Tesseract is installed and are skipped otherwise.
- **Live evaluation:** `scripts/run_live_eval.py` asks the 20 demo questions in `sample_data/expected/demo_questions.json` as the right users, and checks each answer's outcome, key facts, required sources and forbidden text. It uses the real model, so it costs about 1–3 cents.

## How to run

```powershell
# Unit tests: no servers needed
uv run pytest tests/unit

# Everything, including integration tests against a PostgreSQL superuser URL (a throwaway database is created and dropped)
$env:TEST_DATABASE_ADMIN_URL = "postgresql+psycopg://postgres:<password>@127.0.0.1:5432/postgres"
uv run pytest

# Live, end to end: API and worker running, samples loaded (see README)
uv run python scripts/load_samples.py
uv run python scripts/run_live_eval.py
```

## Mandatory scenarios

| # | Scenario (pass condition) | Automated tests (`tests/…`) | Live demo questions |
|---|---|---|---|
| 1 | **Mixed-format ingestion.** At least four input types reach the canonical schema. | `integration/test_ingestion_pipeline.py::test_native_files_match_the_ground_truth` (CSV, XLSX, DOCX and text PDF, for both tenants, compared record by record with the ground truth); `::test_scanned_register_flags_uncertain_cells_instead_of_guessing` (scanned PDF); `unit/test_parsers.py::test_native_files_match_the_ground_truth_row_for_row` | all (8 sample files loaded) |
| 2 | **Extraction traceability.** Each record traces to its file and page, row or cell. | `test_ingestion_pipeline.py::test_native_files_match_the_ground_truth` (checks `source_page_or_row` on every record); `unit/test_parsers.py` (every locator: CSV physical line, XLSX sheet and cell, DOCX table and row, PDF page and row) | q06 |
| 3 | **OCR handling.** OCR confidence or review status is kept; uncertain values are never presented as facts. | `test_ingestion_pipeline.py::test_scanned_register_flags_uncertain_cells_instead_of_guessing`; `unit/test_parsers.py::test_scanned_register_flags_the_degraded_cells_for_review`, `::test_confident_ocr_values_are_correct`; `unit/test_normalize.py::test_low_confidence_ocr_values_become_needs_review_not_facts`; `integration/test_query_view.py::test_records_pending_review_are_left_out`; `integration/test_answer_service.py::test_records_waiting_for_review_are_excluded_and_disclosed` | q18 (a late arrival from a degraded cell is disclosed) |
| 4 | **Idempotency.** Re-uploading the same file adds no records; a changed file is detected and versioned. | `test_ingestion_pipeline.py::test_reupload_is_idempotent_and_a_changed_version_updates_only_what_changed` (duplicate recorded; v2 updates exactly the 2 changed records); `::test_a_failed_upload_is_processed_again_when_uploaded_again`, `::test_a_job_cut_short_is_shown_as_failed_and_can_be_retried` | `load_samples.py --changed-file` |
| 5 | **Structured answers.** Counts, averages and highest/lowest comparisons use structured data and are correct. | `test_answer_service.py::test_structured_answer_with_citations_confidence_and_log`; `test_query_view.py::test_view_applies_tenant_formula_and_lateness`; `unit/test_retrieval.py::test_guard_allows_analytics_queries_and_derives_evidence` | q01–q04, q10, q11, q14, q17, q19, q20 |
| 6 | **Evidence answers.** Narrative and source questions return grounded citations that exist in the source metadata. | `test_answer_service.py::test_ungrounded_drafts_are_repaired_or_replaced`; `unit/test_governance.py::test_invented_numbers_citations_and_names_are_caught`; `unit/test_retrieval.py::test_pack_context_labels_records_and_documents` | q05, q06, q07, q16 |
| 7 | **Unavailable answer.** A question outside the evidence gets a controlled unavailable or insufficient-evidence response. | `test_answer_service.py::test_other_tenants_data_is_simply_not_there`; `unit/test_retrieval.py::test_empty_aggregate_counts_as_no_data` | q12 (July 2026), q13 (a missing day is unknown, not absent) |
| 8 | **Tenant isolation.** Tenant A cannot return or cite Tenant B's records, including through aggregates or exports. | `integration/test_row_level_security.py::test_tenants_never_see_each_others_records`, `::test_missing_access_context_fails_closed`, `::test_cannot_write_rows_for_another_tenant`; `unit/test_vector_store.py::test_tenants_only_find_their_own_chunks`, `::test_a_result_outside_the_callers_access_fails_closed`; `test_answer_service.py::test_other_tenants_data_is_simply_not_there`, `::test_bound_scope_holds_even_if_session_settings_change`; `unit/test_orchestration.py::test_questions_naming_another_tenant_are_unavailable`, `::test_cache_keys_never_mix_access_scopes`; `unit/test_auth.py::test_tenant_header_cannot_override_token`; `integration/test_exports.py::test_exports_never_contain_another_tenants_records` | q14 (Globex E001 is 88.10%, never Acme's 95%), q15 |
| 9 | **RBAC and entity isolation.** A role or entity without access is denied, or gets a filtered result, before generation. | `unit/test_orchestration.py::test_guard_denies_out_of_scope_mentions_before_retrieval`; `test_answer_service.py::test_out_of_scope_questions_are_denied_before_any_model_call`, `::test_managers_only_ever_count_their_departments`; `test_row_level_security.py::test_manager_sees_only_their_departments`, `::test_employee_sees_only_their_own_records`, `::test_managers_cannot_write_records`; `unit/test_vector_store.py::test_managers_see_their_departments_below_restricted`, `::test_employees_see_only_their_own_remarks`; `test_exports.py::test_exports_apply_the_callers_role_entity_and_clearance` | q09 (manager asks about Sales: denied), q10, q11, q19, q20 |
| 10 | **Prompt injection.** Instructions embedded in an uploaded file cannot change policy or reveal restricted data. | `test_ingestion_pipeline.py::test_injected_instructions_are_quarantined`; `unit/test_vector_store.py::test_quarantined_chunks_are_never_returned`; `unit/test_governance.py::test_detects_the_sample_injection_and_not_ordinary_remarks`; `unit/test_orchestration.py::test_guard_refuses_instruction_like_questions`; `unit/test_parsers.py::test_a_memo_without_a_table_gives_narrative_only` | q16 (the memo's instructions are ignored; no invented "100%") |
| 11 | **PII and data leakage.** Sensitive fields are masked or restricted by the classification policy. | `test_query_view.py::test_restricted_remarks_are_masked_below_admin`; `test_answer_service.py::test_restricted_remarks_stay_masked_for_managers`; `test_row_level_security.py::test_clearance_hides_restricted_records`; `unit/test_governance.py::test_classifies_and_masks_personal_data`; `unit/test_normalize.py::test_sensitive_remarks_are_restricted`; `test_exports.py::test_exports_apply_the_callers_role_entity_and_clearance`; `unit/test_config.py::test_secrets_never_appear_in_repr` | q08 (the manager sees the leave, not the medical reason) |
| 12 | **Provider failure.** The failure is recorded, and the service falls back safely or returns a clear, controlled error. | `test_answer_service.py::test_fallback_model_then_controlled_failure`; `unit/test_orchestration.py::test_router_falls_back_then_fails_cleanly`, `::test_circuit_breaker_skips_a_failing_model`; `unit/test_health.py::test_model_provider_reported_as_configured` | — (run without `OPENROUTER_API_KEY` to see the controlled answer) |
| 13 | **Feedback-driven learning.** An authorised training call is stored as a versioned, scoped example; the equivalent query then gives the required output; unauthorised or cross-tenant reuse is blocked; rollback works. | `integration/test_feedback.py::test_an_approved_correction_is_replayed_versioned_and_used_by_the_repeat_question`, `::test_an_example_never_crosses_tenant_or_role_boundaries`, `::test_only_reviewers_may_submit_feedback`, `::test_a_managers_question_gets_a_department_scoped_example`, `::test_deactivation_rolls_the_example_back`, `::test_bad_feedback_is_recorded_as_rejected_and_never_applied`; `unit/test_feedback_rules.py` (validation, scope matching, replay checks) | q17 (the planned correction), then deactivate |
| 14 | **Export consistency.** JSON, XLSX and PDF contain the same permitted records and keep source references. | `unit/test_exports_render.py::test_every_format_carries_the_same_records_and_source_references`, `::test_spreadsheet_cells_are_never_formulas`, `::test_pdf_text_is_escaped_so_uploaded_content_cannot_inject_markup`; `integration/test_exports.py::test_every_format_holds_the_same_permitted_records_and_source_references`, `::test_a_question_exports_its_answer_and_the_evidence_the_caller_may_see` | export from the Records page and from an answer |

## Other controls from section 5

| Control | Tests |
|---|---|
| Authentication and request validation | `unit/test_auth.py` (17 tests: signature, expiry, audience, missing claims, stale role, other tenant, headers, the demo access code), `unit/test_http.py`, `unit/test_config.py` |
| Hosted demo (Vercel): inline uploads and platform settings | `unit/test_inline_queue.py` (retries, then giving up), `unit/test_serverless.py` (platform defaults never override the host, the bundled Tesseract), `unit/test_health.py::test_inline_mode_reports_the_queue_as_inline`. Every integration ingestion test runs its uploads through the inline queue. |
| Upload validation | `unit/test_file_checks.py` (content must match the extension; oversized, legacy, encrypted and macro-enabled files and zip bombs are rejected) |
| Auditability | `test_answer_service.py::test_every_question_is_audited`; `integration/test_audit_log.py` (append-only, same-tenant admins read, no-tenant events kept); `test_exports.py::test_each_export_is_audited`; `test_feedback.py` (feedback decisions audited) |
| Confidence and low-confidence fallback | `unit/test_governance.py::test_confidence_*`, `::test_unresolved_issues_force_low_confidence` |
| Schema and provisioning | `integration/test_schema.py` (models match the migrations; provisioning is idempotent and reports honestly what it could restrict) |

## Results

Latest run on 8 October 2026: Windows 11, Python 3.12, a local PostgreSQL 16 superuser for the throwaway integration database, Tesseract 5.4, and the scripted model.

| Suite | Passed | Skipped | Failed | Notes |
|---|---|---|---|---|
| Unit (`tests/unit`) | 181 | 0 | 0 | Includes OCR of the scanned register, compared with the ground truth. |
| Integration (`tests/integration`) | 57 | 0 | 0 | Every native sample file matches the ground truth record by record. The `set_config` restriction test ran, because a local superuser can revoke it. |
| **Total** (`uv run pytest`) | **238** | **0** | **0** | 44 s |
| Static checks | — | — | — | ruff (lint and format), mypy strict (86 source files) and the UI's `tsc` all clean. |
| Live evaluation (20 demo questions) | — | — | — | Uses the real model (about 1–3 cents), so it is run with the demo: `scripts/run_live_eval.py`. |

**Note on the final run.** One test failed at first: `test_reupload_is_idempotent_and_a_changed_version_updates_only_what_changed`. Its setup loaded the sample CSV into a tenant whose own Engineering file, ingested earlier in the same run, already holds the same employee-days. The pipeline rightly rejected every colliding row, because two files may not claim the same employee's day, which left nothing to update. The test now uses the same two files with their dates moved to 2031, which no other test uses. It then confirms 79 records created, a duplicate on re-upload, and v2 updating exactly 2 records and leaving 77 unchanged.

**Since the 29 September run (223 tests),** 15 tests were added with the hosted demo: inline uploads and their retries, interrupted jobs, retrying a failed upload, the demo access code, and the serverless platform settings. One OCR fix came with them: Tesseract now runs one thread per process (`OMP_THREAD_LIMIT=1`), because pages are already read in parallel. In the Docker image the scanned register now takes 16 s instead of 169 s; on a single CPU it had timed out.

**Also verified on managed cloud services** (Aiven PostgreSQL 17, Redis Cloud, Qdrant Cloud): provisioning, migrations, ingestion, and questions through the UI. On Aiven, `set_config` cannot be revoked without a superuser, so that one test skips there by design (see design.md, section 11). The hosted demo on Vercel was then built from this code and used through the UI against the same services: the build's offline model check and Tesseract download, sign-in with the access code, questions with citations, and reviewer corrections.
