"""The answer pipeline (docs/design.md, section 7):

cache -> guard -> plan (LLM) -> SQL and/or document retrieval -> sufficiency -> compose (LLM)
-> grounding checks -> confidence -> respond, query log, audit, cache.

The LLM never decides access: retrieval is filtered before anything reaches it, and every number,
name and citation it writes is checked against what was actually retrieved.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError

from attendance_ai.core.access import AccessContext, Scope
from attendance_ai.core.directory import Directory, Tenant
from attendance_ai.feedback.examples import Example, ExampleStore
from attendance_ai.generation import prompts
from attendance_ai.generation.composer import Composer
from attendance_ai.generation.llm import LLMError, ProviderUnavailableError
from attendance_ai.generation.router import LLMRouter, RoutedReply
from attendance_ai.governance.audit import AuditEvent, AuditSink
from attendance_ai.governance.confidence import ConfidenceInputs, score_confidence
from attendance_ai.governance.grounding import check_answer
from attendance_ai.governance.pii import mask_contact_details
from attendance_ai.orchestration.guard import QuestionGuard, foreign_names
from attendance_ai.orchestration.models import (
    CitationOut,
    ComputationOut,
    ConfidenceOut,
    FeedbackUsedOut,
    Outcome,
    QueryRequest,
    QueryResponse,
    VersionsOut,
)
from attendance_ai.orchestration.planner import Plan, Planner
from attendance_ai.retrieval.context import PackedContext, pack_context
from attendance_ai.retrieval.documents import DocumentHit, DocumentSearch
from attendance_ai.retrieval.sql_guard import GuardedSql, UnsafeSqlError, guard_sql
from attendance_ai.retrieval.sql_runner import SqlExecutionError, SqlResult, SqlRunner
from attendance_ai.stores.cache import AnswerCache, cache_key
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import QueryLogEntry
from attendance_ai.stores.versions import current_versions

logger = logging.getLogger(__name__)

NO_DATA = (
    "No attendance records match this question within the data you can see. "
    "Days without a record are treated as unknown, not as absences."
)
_CACHED_EVIDENCE = "_retrieved_ids"


@dataclass(slots=True)
class Trace:
    """What happened while answering: kept for the query log and the audit trail."""

    attempts: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    used_fallback_model: bool = False
    plan: Plan | None = None
    sql: str | None = None
    sql_failed: bool = False
    provider_failed: bool = False
    retrieved_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    audit_details: dict[str, Any] = field(default_factory=dict)

    def record(self, routed: RoutedReply, step: str) -> None:
        self.attempts.extend(f"{step}: {attempt}" for attempt in routed.attempts)
        self.provider, self.model = routed.reply.provider, routed.reply.model
        self.used_fallback_model = self.used_fallback_model or routed.used_fallback


@dataclass(frozen=True, slots=True)
class Composed:
    answer: str
    citation_ids: list[str]
    insufficient: bool
    repaired: bool
    template: bool
    issues: list[str]


class AnswerService:
    def __init__(
        self,
        *,
        directory: Directory,
        database: Database,
        router: LLMRouter | None,
        sql_runner: SqlRunner,
        documents: DocumentSearch | None,
        cache: AnswerCache | None,
        audit: AuditSink,
        max_tokens: int,
        examples: ExampleStore | None = None,
    ) -> None:
        self._directory = directory
        self._db = database
        self._router = router
        self._runner = sql_runner
        self._documents = documents
        self._cache = cache
        self._audit = audit
        self._examples = examples
        self._guard = QuestionGuard(directory)
        self._planner = Planner(router, max_tokens=max_tokens) if router else None
        self._composer = Composer(router, max_tokens=max_tokens) if router else None
        self._foreign_names: dict[str, list[str]] = {}

    @property
    def primary_model(self) -> str | None:
        return self._router.primary_model if self._router else None

    @property
    def prompt_versions(self) -> str:
        return f"{prompts.PLANNER}, {prompts.COMPOSER}"

    @property
    def example_store(self) -> ExampleStore | None:
        return self._examples

    def answer(
        self,
        ctx: AccessContext,
        request: QueryRequest,
        *,
        candidate: Example | None = None,
        use_cache: bool = True,
        audit_details: dict[str, Any] | None = None,
    ) -> QueryResponse:
        """Answer one question. ``candidate`` applies a feedback example that is not active yet (the
        replay that validates it); ``use_cache=False`` always answers afresh and caches nothing."""
        started = time.perf_counter()
        ctx.require("query")
        tenant = self._directory.get_tenant(ctx.tenant_id)
        if tenant is None:
            raise LookupError(ctx.tenant_id)
        today = request.as_of or datetime.now(ZoneInfo(tenant.config.timezone)).date()
        with self._db.session_for(ctx) as session:
            versions = current_versions(session, ctx)
        version_out = VersionsOut(
            model=self.primary_model,
            prompts=self.prompt_versions,
            data=versions.data,
            knowledge=versions.knowledge,
        )
        key = cache_key(ctx, request.question, as_of=today.isoformat(), versions=version_out.model_dump())
        trace = Trace(audit_details=dict(audit_details or {}))

        cached = self._cache.get(key) if self._cache and use_cache else None
        if cached is not None:
            # The evidence IDs travel with the cached answer, so its log entry, audit event and exports
            # still point at the records it was built from.
            trace.retrieved_ids = [str(item) for item in cached.pop(_CACHED_EVIDENCE, [])]
            response = QueryResponse.model_validate(
                {**cached, "request_id": ctx.request_id, "context": ctx.describe(), "cached": True}
            )
        else:
            response = self._run(ctx, request.question.strip(), today, tenant, version_out, trace, candidate)
            if self._cache and use_cache and not trace.provider_failed:
                self._cache.put(
                    key,
                    {**response.model_dump(mode="json"), _CACHED_EVIDENCE: trace.retrieved_ids[:1000]},
                )
        response.latency_ms = round((time.perf_counter() - started) * 1000)
        self._log(ctx, request.question, response, trace)
        return response

    def _run(
        self,
        ctx: AccessContext,
        question: str,
        today: date,
        tenant: Tenant,
        versions: VersionsOut,
        trace: Trace,
        candidate: Example | None = None,
    ) -> QueryResponse:
        base: dict[str, Any] = {"request_id": ctx.request_id, "context": ctx.describe(), "versions": versions}

        def respond(outcome: Outcome, reason_code: str, message: str) -> QueryResponse:
            return QueryResponse(**base, outcome=outcome, reason_code=reason_code, message=message)

        decision = self._guard.check(ctx, question)
        if not decision.allowed:
            trace.notes.append(f"guard: {decision.reason_code}")
            return respond(
                decision.outcome, decision.reason_code or "denied", decision.message or "Access denied."
            )
        if self._planner is None or self._composer is None:
            trace.provider_failed = True
            return respond(
                "unavailable", "provider_unavailable", "No language model is configured for this service."
            )

        scope = scope_description(ctx, tenant)
        # Approved examples only reach the prompts after the access guard, and only in the exact scope
        # they were approved for; the replay that validates a new example passes it in directly.
        examples = (
            [candidate] if candidate else (self._examples.find(ctx, question) if self._examples else [])
        )
        guidance = [(example.question, example.ideal_output) for example in examples]
        try:
            plan, routed = self._planner.plan(
                question,
                today=today,
                timezone=tenant.config.timezone,
                scope_description=scope,
                examples=guidance,
            )
        except ProviderUnavailableError as exc:
            trace.attempts.extend(f"planner: {attempt}" for attempt in exc.attempts)
            trace.provider_failed = True
            return respond(
                "unavailable",
                "provider_unavailable",
                "The language service is unavailable right now. Please try again shortly.",
            )
        except LLMError as exc:
            trace.notes.append(f"planner failed: {exc}")
            return respond(
                "unavailable", "planning_failed", "The question could not be interpreted. Try rephrasing it."
            )
        trace.record(routed, "planner")
        trace.plan = plan
        if plan.mode == "out_of_scope":
            return respond(
                "out_of_scope", "not_attendance", "This service answers questions about attendance only."
            )

        sql_result, guarded = (None, None)
        if plan.sql:
            sql_result, guarded = self._run_sql(ctx, question, plan, today, tenant, scope, trace, guidance)
        documents = self._search(ctx, plan, question, trace)
        has_rows = sql_result is not None and not sql_result.is_empty
        if not has_rows and not documents:
            if trace.sql_failed and plan.mode == "structured":
                return respond(
                    "unavailable",
                    "query_failed",
                    "The data could not be queried for this question. Try rephrasing it.",
                )
            return respond("unavailable", "no_data", NO_DATA)

        rows = sql_result if has_rows else None
        context = pack_context(rows, documents)
        pending = (
            self._runner.pending_review(ctx, _date_span(rows.evidence), _departments(rows.evidence))
            if rows
            else 0
        )
        composed = self._compose(ctx, question, scope, context, rows, documents, trace, guidance)
        if rows:
            trace.retrieved_ids.extend(str(row["record_id"]) for row in rows.evidence if row.get("record_id"))
        trace.retrieved_ids.extend(hit.point_id for hit in documents)

        confidence = score_confidence(
            ConfidenceInputs(
                mode=plan.mode,
                has_rows=has_rows,
                evidence=rows.evidence if rows else [],
                evidence_available=rows.evidence_available if rows else True,
                documents=documents,
                pending_review=pending,
                used_fallback_model=trace.used_fallback_model,
                repaired=composed.repaired,
                unresolved_issues=composed.issues,
                template_answer=composed.template,
            )
        )
        outcome: Outcome
        reason: str | None
        message: str | None
        if composed.insufficient:
            outcome, reason, message = "unavailable", "insufficient_evidence", composed.answer
        elif confidence.band == "low":
            outcome, reason = "needs_review", "low_confidence"
            message = "Low confidence: please check the sources below."
        else:
            outcome, reason, message = "answered", None, None
        return QueryResponse(
            **base,
            outcome=outcome,
            reason_code=reason,
            message=message,
            answer=composed.answer if composed.insufficient else with_pending_note(composed.answer, pending),
            retrieval_mode=plan.mode,
            confidence=ConfidenceOut(
                score=confidence.score,
                band=confidence.band,
                reasons=[
                    *confidence.reasons,
                    *(f"Follows reviewer-approved example {e.example_id[:8]}." for e in examples),
                ],
            ),
            citations=_citations(context, composed.citation_ids),
            computation=ComputationOut(
                sql=guarded.sql if guarded else None,
                row_count=len(sql_result.rows) if sql_result else 0,
                excluded_pending_review=pending,
                result_preview=sql_result.rows[:20] if sql_result else [],
            ),
            feedback_applied=[
                FeedbackUsedOut(example_id=e.example_id, version=e.version, similarity=e.similarity)
                for e in examples
            ],
        )

    def _run_sql(
        self,
        ctx: AccessContext,
        question: str,
        plan: Plan,
        today: date,
        tenant: Tenant,
        scope: str,
        trace: Trace,
        guidance: list[tuple[str, str]],
    ) -> tuple[SqlResult | None, GuardedSql | None]:
        planner = self._planner
        if planner is None:
            trace.sql_failed = True
            return None, None
        sql = plan.sql or ""
        for attempt in range(2):
            try:
                guarded = guard_sql(sql)
                result = self._runner.run(ctx, guarded)
            except (UnsafeSqlError, SqlExecutionError) as exc:
                problem = exc.detail if isinstance(exc, UnsafeSqlError) else str(exc)
                trace.notes.append(f"sql rejected: {problem}")
                if attempt == 1:
                    break
                try:
                    repaired, routed = planner.plan(
                        question,
                        today=today,
                        timezone=tenant.config.timezone,
                        scope_description=scope,
                        repair=f"{problem}. The SQL was: {sql}",
                        examples=guidance,
                    )
                except LLMError:
                    break
                trace.record(routed, "planner-repair")
                if not repaired.sql:
                    break
                sql = repaired.sql
                continue
            trace.sql = guarded.sql
            return result, guarded
        trace.sql_failed = True
        return None, None

    def _search(self, ctx: AccessContext, plan: Plan, question: str, trace: Trace) -> list[DocumentHit]:
        if plan.mode not in ("document", "hybrid") or self._documents is None:
            return []
        try:
            return self._documents.search(ctx, plan.search_query or question)
        except Exception as exc:  # the vector store is optional for structured answers
            logger.exception("document_search_failed")
            trace.notes.append(f"document search unavailable: {type(exc).__name__}")
            return []

    def _compose(
        self,
        ctx: AccessContext,
        question: str,
        scope: str,
        context: PackedContext,
        rows: SqlResult | None,
        documents: list[DocumentHit],
        trace: Trace,
        guidance: list[tuple[str, str]],
    ) -> Composed:
        composer = self._composer
        if composer is None:
            return _template(context, rows, documents)
        problems: list[str] | None = None
        for attempt in range(2):
            try:
                draft, routed = composer.compose(
                    question,
                    scope_description=scope,
                    context_text=context.text,
                    problems=problems,
                    examples=guidance,
                )
            except LLMError as exc:
                trace.notes.append(f"composer failed: {exc}")
                if isinstance(exc, ProviderUnavailableError):
                    trace.attempts.extend(f"composer: {item}" for item in exc.attempts)
                break
            trace.record(routed, "composer")
            report = check_answer(
                draft.answer,
                draft.citations,
                context=context,
                question=question,
                known_names=self._known_names(ctx),
                foreign_names=self._foreign(ctx),
            )
            if report.ok:
                return Composed(report.answer, report.citations, draft.insufficient, attempt == 1, False, [])
            trace.notes.append("grounding issues: " + "; ".join(report.issues))
            problems = report.issues
        return _template(context, rows, documents)

    def _known_names(self, ctx: AccessContext) -> list[str]:
        tenant = self._directory.get_tenant(ctx.tenant_id)
        if tenant is None:
            return []
        return [employee.name for employee in tenant.employees.values()] + [
            department.name for department in tenant.departments.values()
        ]

    def _foreign(self, ctx: AccessContext) -> list[str]:
        """Names that belong only to other tenants (companies and their people)."""
        if ctx.tenant_id not in self._foreign_names:
            self._foreign_names[ctx.tenant_id] = foreign_names(self._directory, ctx.tenant_id)
        return self._foreign_names[ctx.tenant_id]

    def _log(self, ctx: AccessContext, question: str, response: QueryResponse, trace: Trace) -> None:
        plan = trace.plan
        applied = [item.model_dump() for item in response.feedback_applied]
        try:
            with self._db.session_for(ctx) as session:
                session.add(
                    QueryLogEntry(
                        request_id=ctx.request_id,
                        tenant_id=ctx.tenant_id,
                        product_id=ctx.product_id,
                        module=ctx.module,
                        user_id=ctx.user_id,
                        role=ctx.role,
                        question_masked=mask_contact_details(question),
                        mode=response.retrieval_mode,
                        plan=(
                            {
                                "mode": plan.mode,
                                "search_query": plan.search_query,
                                "rewritten_question": plan.rewritten_question,
                                "reason": plan.reason,
                                "feedback_examples": applied,
                            }
                            if plan
                            else None
                        ),
                        sql_text=trace.sql,
                        retrieved_ids=trace.retrieved_ids[:1000],
                        answer=response.model_dump(mode="json", exclude={"context"}),
                        confidence=response.confidence.score if response.confidence else None,
                        outcome=response.outcome,
                        versions=response.versions.model_dump(),
                    )
                )
        except SQLAlchemyError:
            logger.exception("query_log_write_failed")
        self._audit.record(
            AuditEvent(
                # A question about another tenant is shown as "unavailable" but audited as a denial.
                "denial"
                if response.outcome == "denied" or response.reason_code == "other_tenant"
                else "query",
                "question_answered",
                response.outcome,
                query_mode=response.retrieval_mode,
                retrieved_ids=trace.retrieved_ids[:200],
                provider=trace.provider,
                model=trace.model,
                fallback_path=trace.attempts or None,
                confidence=response.confidence.score if response.confidence else None,
                latency_ms=response.latency_ms,
                details={
                    "reason_code": response.reason_code,
                    "cached": response.cached,
                    "notes": trace.notes[:10],
                    "feedback_examples": [item["example_id"] for item in applied],
                    "knowledge_version": response.versions.knowledge,
                    **trace.audit_details,
                },
            ),
            ctx,
        )


def scope_description(ctx: AccessContext, tenant: Tenant) -> str:
    if ctx.scope is Scope.TENANT:
        return f"all of {tenant.name}"
    if ctx.scope is Scope.DEPARTMENT:
        names = ", ".join(tenant.departments[entity].name for entity in ctx.entity_scope)
        return f"only the {names} department" + ("s" if len(ctx.entity_scope) > 1 else "")
    employee = tenant.employees.get(ctx.employee_id or "")
    who = f"{employee.name}, {employee.employee_id}" if employee else ctx.employee_id
    return f"only their own attendance records ({who})"


def with_pending_note(answer: str, pending: int) -> str:
    """Records awaiting review are never counted; say so in the answer itself, not only in the metadata."""
    if pending <= 0:
        return answer
    if pending == 1:
        return f"{answer}\n\nNote: 1 record in this period is waiting for review and was not counted."
    return f"{answer}\n\nNote: {pending} records in this period are waiting for review and were not counted."


def template_answer(context: PackedContext, rows: SqlResult | None, documents: list[DocumentHit]) -> str:
    """A deterministic answer straight from the data, used when a verified narrative is not available."""
    lines: list[str] = []
    record_ids = " ".join(f"[{c.id}]" for c in context.citations if c.kind == "records")
    if rows is not None and rows.rows:
        if len(rows.rows) == 1:
            lines.append("Result: " + _describe_row(rows.rows[0]) + (f" {record_ids}" if record_ids else ""))
        else:
            lines.append(f"Results ({len(rows.rows)} rows)" + (f" {record_ids}" if record_ids else "") + ":")
            lines.extend(f"- {_describe_row(row)}" for row in rows.rows[:10])
            if len(rows.rows) > 10:
                lines.append(f"- and {len(rows.rows) - 10} more")
    document_citations = [c for c in context.citations if c.kind == "document"]
    if document_citations:
        lines.append("Relevant notes:")
        lines.extend(f"- [{c.id}] {(c.snippet or '')[:240]}" for c in document_citations)
    return "\n".join(lines)


def _template(context: PackedContext, rows: SqlResult | None, documents: list[DocumentHit]) -> Composed:
    answer = template_answer(context, rows, documents)
    return Composed(answer, [citation.id for citation in context.citations], False, False, True, [])


def _describe_row(row: dict[str, Any]) -> str:
    return ", ".join(
        f"{key.replace('_', ' ')}: {'—' if value is None else value}" for key, value in row.items()
    )


def _citations(context: PackedContext, used: list[str]) -> list[CitationOut]:
    documents_cited = any(cid.startswith("S") for cid in used)
    chosen = [
        c
        for c in context.citations
        if c.kind == "records" or c.id in used or (not documents_cited and c.kind == "document")
    ]
    return [
        CitationOut(
            id=c.id,
            kind=c.kind,
            source_file=c.source_file,
            locations=c.locations,
            record_count=c.record_count,
            snippet=c.snippet,
        )
        for c in chosen
    ]


def _departments(evidence: list[dict[str, Any]]) -> set[str]:
    return {str(row["department_id"]) for row in evidence if row.get("department_id")}


def _date_span(evidence: list[dict[str, Any]]) -> tuple[date, date] | None:
    dates = sorted(str(row["attendance_date"]) for row in evidence if row.get("attendance_date"))
    if not dates:
        return None
    return date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
