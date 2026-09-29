"""The feedback/training loop (docs/design.md, section 9).

A tenant admin corrects an answer. The original question, answer, asker and versions come from the
query log, which row-level security limits to the admin's own tenant. The correction is validated as
untrusted input and then replayed: the original question is answered again, as the original asker,
with the example applied. The example becomes active only if the replayed answer is a normal answer that
carries the ideal output's numbers and follows it closely; otherwise it is recorded as rejected.

Active examples guide later equivalent questions in exactly the same scope. Deactivating one rolls it
back. Both changes bump the scope's knowledge version, which also invalidates cached answers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from attendance_ai.core.access import CLEARANCE_LEVELS, AccessContext, Scope
from attendance_ai.core.directory import Directory
from attendance_ai.core.errors import AppError, NotFoundError
from attendance_ai.feedback.examples import Example, ExampleStore
from attendance_ai.feedback.models import FeedbackExampleOut, FeedbackScopeOut, ReplayOut
from attendance_ai.feedback.validation import policy_problems, replay_problems
from attendance_ai.governance.audit import AuditEvent, AuditSink
from attendance_ai.orchestration.answer import AnswerService
from attendance_ai.orchestration.models import QueryRequest
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import FeedbackExample, QueryLogEntry
from attendance_ai.stores.versions import bump_knowledge_version, current_versions


class FeedbackNotAllowedError(AppError):
    status_code = 422
    code = "feedback_not_allowed"
    title = "Feedback not allowed"


class FeedbackStateError(AppError):
    status_code = 409
    code = "feedback_state"
    title = "Feedback state conflict"


class ReplayUnavailableError(AppError):
    status_code = 503
    code = "replay_unavailable"
    title = "Replay unavailable"


class FeedbackService:
    def __init__(
        self,
        *,
        database: Database,
        directory: Directory,
        answers: AnswerService,
        examples: ExampleStore | None,
        audit: AuditSink,
    ) -> None:
        self._db = database
        self._directory = directory
        self._answers = answers
        self._examples = examples
        self._audit = audit

    def submit(
        self,
        ctx: AccessContext,
        *,
        request_id: str,
        feedback: str,
        ideal_output: str,
        approval_note: str | None = None,
    ) -> FeedbackExampleOut:
        ctx.require("feedback:submit")
        original = self._original(ctx, request_id)
        if original["outcome"] == "denied" or original["reason_code"] == "other_tenant":
            raise FeedbackNotAllowedError("A refused question cannot be used as a training example.")
        origin = self._origin_context(ctx, original["user_id"])
        example_id = uuid.uuid4()
        ideal_output = ideal_output.strip()

        problems = policy_problems(self._directory, origin, feedback=feedback, ideal_output=ideal_output)
        replay: ReplayOut | None = None
        if not problems:
            replay, replay_issues = self._replay(ctx, origin, original, example_id, ideal_output)
            problems.extend(replay_issues)
        status = "rejected" if problems else "active"

        embedding = self._examples.embed(original["question"]) if self._examples else None
        with self._db.session_for(ctx) as session:
            if status == "active":
                version = bump_knowledge_version(session, ctx)
            else:
                version = current_versions(session, ctx).knowledge
            row = FeedbackExample(
                id=example_id,
                tenant_id=ctx.tenant_id,
                product_id=ctx.product_id,
                module=ctx.module,
                scope=origin.scope.value,
                entity_scope=list(origin.entity_scope),
                employee_id=origin.employee_id if origin.scope is Scope.SELF else None,
                clearance=origin.clearance,
                request_id=request_id,
                question=original["question"],
                question_embedding=embedding,
                original_answer=original["answer"],
                feedback=feedback.strip(),
                ideal_output=ideal_output,
                status=status,
                version=version,
                reviewer_id=ctx.user_id,
                approval_note=approval_note,
                validation={
                    "problems": problems,
                    "replay": replay.model_dump(mode="json") if replay else None,
                },
                versions={
                    "original": original["versions"],
                    "replay": replay_versions(replay, self._answers),
                },
            )
            session.add(row)
            session.flush()
            result = example_view(row)
        result.knowledge_version = version
        result.message = (
            f"Active from knowledge version {version}: equivalent questions in this scope now follow it."
            if status == "active"
            else "Rejected: " + " ".join(problems)
        )
        self._audit.record(
            AuditEvent(
                "feedback",
                "feedback_submitted",
                status,
                details={
                    "example_id": str(example_id),
                    "request_id": request_id,
                    "scope": origin.scope.value,
                    "entity_scope": list(origin.entity_scope),
                    "knowledge_version": version,
                    "problems": problems,
                    "replay_request_id": replay.request_id if replay else None,
                    "similarity": replay.similarity if replay else None,
                },
            ),
            ctx,
        )
        return result

    def deactivate(self, ctx: AccessContext, example_id: str) -> FeedbackExampleOut:
        """Roll an example back: it stops applying from the next knowledge version."""
        ctx.require("feedback:submit")
        key = _uuid(example_id)
        with self._db.session_for(ctx) as session:
            row = session.get(FeedbackExample, key)
            if row is None:
                raise NotFoundError("No such feedback example.")
            if row.status != "active":
                raise FeedbackStateError(f"The example is already {row.status}.")
            row.status = "inactive"
            row.deactivated_at = datetime.now(UTC)
            row.deactivated_by = ctx.user_id
            version = bump_knowledge_version(session, ctx)
            session.flush()
            result = example_view(row)
        result.knowledge_version = version
        result.message = f"Deactivated from knowledge version {version}; it no longer applies."
        self._audit.record(
            AuditEvent(
                "feedback",
                "feedback_deactivated",
                "success",
                details={"example_id": example_id, "knowledge_version": version},
            ),
            ctx,
        )
        return result

    def list_examples(
        self, ctx: AccessContext, *, status: str | None, limit: int
    ) -> list[FeedbackExampleOut]:
        ctx.require("feedback:submit")
        query = select(FeedbackExample).order_by(FeedbackExample.created_at.desc()).limit(limit)
        if status:
            query = query.where(FeedbackExample.status == status)
        with self._db.session_for(ctx) as session:
            return [example_view(row) for row in session.scalars(query).all()]

    def get(self, ctx: AccessContext, example_id: str) -> FeedbackExampleOut:
        ctx.require("feedback:submit")
        with self._db.session_for(ctx) as session:
            row = session.get(FeedbackExample, _uuid(example_id))
            if row is None:
                raise NotFoundError("No such feedback example.")
            return example_view(row)

    def _original(self, ctx: AccessContext, request_id: str) -> dict[str, Any]:
        with self._db.session_for(ctx) as session:
            entry = session.get(QueryLogEntry, request_id)  # only this tenant's questions are visible
            if entry is None:
                raise NotFoundError("No such question in your tenant.")
            response = dict(entry.answer or {})
            return {
                "question": entry.question_masked,
                "user_id": entry.user_id,
                "asked_at": entry.created_at,
                "outcome": entry.outcome,
                "reason_code": response.get("reason_code"),
                "answer": response,
                "versions": dict(entry.versions or {}),
            }

    def _origin_context(self, ctx: AccessContext, user_id: str) -> AccessContext:
        """The access of the user who asked: the example's scope, and the context of its replay."""
        user = self._directory.find_user(user_id)
        if user is None or user.tenant_id != ctx.tenant_id:
            raise FeedbackNotAllowedError("The user who asked this question no longer exists.")
        return self._directory.build_context(
            tenant_id=ctx.tenant_id,
            user_id=user.user_id,
            product_id=ctx.product_id,
            module=ctx.module,
            request_id=f"replay_{uuid.uuid4().hex[:16]}",
        )

    def _replay(
        self,
        ctx: AccessContext,
        origin: AccessContext,
        original: dict[str, Any],
        example_id: uuid.UUID,
        ideal_output: str,
    ) -> tuple[ReplayOut, list[str]]:
        tenant = self._directory.get_tenant(ctx.tenant_id)
        asked_at: datetime = original["asked_at"]
        as_of = asked_at.astimezone(ZoneInfo(tenant.config.timezone)).date() if tenant else asked_at.date()
        candidate = Example(str(example_id), original["question"], ideal_output, version=0, similarity=1.0)
        response = self._answers.answer(
            origin,
            QueryRequest(question=original["question"], as_of=as_of),
            candidate=candidate,
            use_cache=False,
            audit_details={"replay_for_example": str(example_id), "reviewer": ctx.user_id},
        )
        if response.reason_code == "provider_unavailable":
            raise ReplayUnavailableError(
                "The language model is unavailable, so the example could not be verified. Try again shortly."
            )
        problems, similarity, missing = replay_problems(response, ideal_output, original["question"])
        replay = ReplayOut(
            request_id=response.request_id,
            outcome=response.outcome,
            reason_code=response.reason_code,
            answer=response.answer,
            similarity=similarity,
            numbers_missing=missing,
            feedback_applied=response.feedback_applied,
            confidence=response.confidence,
        )
        return replay, problems


def example_view(row: FeedbackExample) -> FeedbackExampleOut:
    validation = dict(row.validation or {})
    replay = validation.get("replay")
    return FeedbackExampleOut(
        example_id=str(row.id),
        status=row.status,  # type: ignore[arg-type]
        version=row.version,
        scope=FeedbackScopeOut(
            scope=row.scope,  # type: ignore[arg-type]
            entity_scope=list(row.entity_scope or []),
            employee_id=row.employee_id,
            clearance=CLEARANCE_LEVELS[row.clearance],
        ),
        request_id=row.request_id,
        question=row.question,
        feedback=row.feedback,
        ideal_output=row.ideal_output,
        reviewer_id=row.reviewer_id,
        approval_note=row.approval_note,
        problems=list(validation.get("problems") or []),
        replay=ReplayOut.model_validate(replay) if replay else None,
        created_at=row.created_at.isoformat(timespec="seconds") if row.created_at else "",
        deactivated_at=row.deactivated_at.isoformat(timespec="seconds") if row.deactivated_at else None,
        deactivated_by=row.deactivated_by,
    )


def replay_versions(replay: ReplayOut | None, answers: AnswerService) -> dict[str, Any] | None:
    if replay is None:
        return None
    return {"model": answers.primary_model, "prompts": answers.prompt_versions}


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise NotFoundError("No such feedback example.") from None
