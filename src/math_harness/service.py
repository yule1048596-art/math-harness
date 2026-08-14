from __future__ import annotations

import json
import uuid
from _thread import LockType
from collections import OrderedDict
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from time import perf_counter

from math_harness.bulk_import import (
    ParsedImportItem,
    example_fingerprint,
    parse_example_corpus,
    problem_preview,
    stored_example_fingerprint,
)
from math_harness.checks import (
    CheckContext,
    ConfidenceAssessment,
    IndependentRecomputeCheck,
    InstantiationCheck,
    PeerReviewCheck,
    StepInstantiationCheck,
    SymbolicEqualityCheck,
    assess,
    run_checks,
)
from math_harness.checks.peer_review import ReviewerProtocol
from math_harness.checks.recompute import ToolClientProtocol
from math_harness.claim_drafting import (
    ClaimDrafterProtocol,
    build_claim_drafter_from_env,
)
from math_harness.classifier import classify_problem
from math_harness.config import load_local_environment
from math_harness.conversation import (
    ChatGeneration,
    ConversationContext,
    ConversationResponderProtocol,
    ExtractiveConversationSummarizer,
    build_conversation_responder_from_env,
    build_conversation_responder_from_resolved,
    stream_chat_generation,
)
from math_harness.dedup import find_merge_candidates
from math_harness.errors import InvalidKnowledgeState
from math_harness.extraction import (
    MethodExtractorProtocol,
    build_method_extractor_from_env,
)
from math_harness.memory import (
    MemoryExtractionResult,
    MemoryExtractorProtocol,
    build_memory_extractor_from_env,
    candidate_is_grounded,
    contains_memory_control_instruction,
    contains_sensitive_memory,
    looks_like_untrusted_math_claim,
    looks_like_untrusted_math_text,
    memory_source_revision,
)
from math_harness.methods import MethodExtractor
from math_harness.models import (
    AnswerKind,
    BulkExampleImportRequest,
    BulkExampleImportResult,
    BulkImportItemResult,
    BulkImportItemStatus,
    CandidateSolution,
    CandidateStep,
    ConclusionConfidence,
    Conversation,
    ConversationCaptureResult,
    ConversationCreate,
    ConversationMessage,
    ConversationMessageKind,
    ConversationRole,
    ConversationStatus,
    ConversationTurnRequest,
    ConversationTurnResult,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRequest,
    EvaluationRun,
    ExampleCreate,
    ExampleDraftUpdate,
    ExampleDraftUpdateResult,
    ExampleOrigin,
    ExampleReviewDecision,
    ExampleReviewRequest,
    ExampleReviewResult,
    ExampleVersion,
    ExtractionStatus,
    GenerationStageKind,
    GenerationStatus,
    ImportExtractorPolicy,
    IngestionResult,
    KnowledgeStatus,
    LearningEvent,
    MathPayload,
    MathTargetDraftRequest,
    MathTargetDraftResult,
    MemoryBackfillResult,
    MemoryCreate,
    MemoryExtractionJob,
    MemoryHealth,
    MemoryItem,
    MemoryJobStatus,
    MemoryKind,
    MemorySettings,
    MemorySettingsUpdate,
    MemorySource,
    MemoryStatus,
    MemoryUpdate,
    MergeProposalStatus,
    MethodCard,
    MethodExtractionResult,
    MethodExtractionTrace,
    MethodMatch,
    MethodMergeProposal,
    MethodStatusUpdate,
    MethodVersion,
    ProblemExample,
    ProcessConfidence,
    ProviderOverride,
    SolutionAttempt,
    SolutionAttemptStatus,
    SolutionCorrection,
    SolutionGenerationResult,
    SolutionGenerationStage,
    SolutionGenerationTrace,
    SolveEvaluationCaseResult,
    SolveEvaluationMetrics,
    SolveEvaluationRequest,
    SolveEvaluationRun,
    SolveMathTarget,
    SolvePlan,
    SolveRequest,
    VerificationMode,
    VerificationReport,
    VerificationStatus,
    Workspace,
    WorkspaceCreate,
    WorkspaceRestoreResult,
    utc_now,
)
from math_harness.normalization import CandidateSolutionNormalizer
from math_harness.portability import (
    create_workspace_archive,
    restore_workspace_archive,
)
from math_harness.provider_config import (
    ROLE_CONVERSATION,
    ROLE_SOLVER,
    ROLE_TARGET_DRAFTER,
    ResolvedRole,
    resolve_override,
)
from math_harness.providers.openai_reviewer import build_reviewer_from_env
from math_harness.retrieval import MethodRetriever
from math_harness.solving import (
    SolutionGeneratorProtocol,
    build_solution_generator_from_env,
    build_solution_generator_from_resolved,
)
from math_harness.storage import WorkspaceManager, WorkspaceStore
from math_harness.structure import (
    StructuralFeatures,
    extract_features,
    features_from_text,
)
from math_harness.target_drafting import (
    TargetDrafterProtocol,
    build_target_drafter_from_env,
    build_target_drafter_from_resolved,
)
from math_harness.tools import build_mcp_client_from_env
from math_harness.verifier import SolutionVerifier


@dataclass(frozen=True)
class _TurnSetup:
    """一个回合准备好、还没生成回复时的状态。

    同步与流式两条路走同样的准备和同样的收尾，差别只在正文是一次拿到还是逐段拿到。
    拆出来是为了让这一点在代码里成立，而不是靠两份相似的实现各自保持一致。
    """

    store: WorkspaceStore
    turn_id: str
    user_message: ConversationMessage
    context: ConversationContext
    matches: list[MethodMatch]
    kind: ConversationMessageKind
    override: ProviderOverride | None


@dataclass(frozen=True)
class _PreparedIngestion:
    request: ExampleCreate
    example: ProblemExample
    extraction_result: MethodExtractionResult
    promotion_approved: bool


_CONVERSATION_CONTEXT_MESSAGES = 12
_CONVERSATION_SUMMARY_TRIGGER = 16
_CONVERSATION_SUMMARY_RETAIN = 8


class _StaleMemoryRevision(Exception):
    """Signal a source-prefix change without consuming the retry budget."""


class MathHarnessService:
    def __init__(
        self,
        data_root: Path | str,
        verifier: SolutionVerifier | None = None,
        extractor: MethodExtractorProtocol | None = None,
        retriever: MethodRetriever | None = None,
        generator: SolutionGeneratorProtocol | None = None,
        normalizer: CandidateSolutionNormalizer | None = None,
        target_drafter: TargetDrafterProtocol | None = None,
        claim_drafter: ClaimDrafterProtocol | None = None,
        reviewer: ReviewerProtocol | None = None,
        tool_client: ToolClientProtocol | None = None,
        conversation_responder: ConversationResponderProtocol | None = None,
        conversation_summarizer: ExtractiveConversationSummarizer | None = None,
        memory_extractor: MemoryExtractorProtocol | None = None,
    ) -> None:
        load_local_environment()
        self.workspaces = WorkspaceManager(data_root)
        self.verifier = verifier or SolutionVerifier()
        self.extractor = extractor or build_method_extractor_from_env()
        self.retriever = retriever or MethodRetriever()
        self.generator = generator or build_solution_generator_from_env()
        self.normalizer = normalizer or CandidateSolutionNormalizer()
        self.target_drafter = target_drafter or build_target_drafter_from_env()
        # 没配 claim_drafter 角色时就是纯规则版，一次模型调用都不会发生。配了的话
        # 也是**规则优先**：能直接从回答里解析出等式的场景不花钱，模型只补规则版
        # 够不着的那一类。
        self.claim_drafter = claim_drafter or build_claim_drafter_from_env()
        # 这两层都默认关闭，各有各的理由：
        #   复核要额外花一次模型调用，而且只有绑到**另一个** provider 才有价值；
        #   独立重算要发网络请求，离线路径「不发请求」的承诺不能因为加了它而变。
        self.reviewer = reviewer or build_reviewer_from_env()
        self.tool_client = tool_client or build_mcp_client_from_env()
        self.conversation_responder = (
            conversation_responder or build_conversation_responder_from_env()
        )
        self.conversation_summarizer = (
            conversation_summarizer or ExtractiveConversationSummarizer()
        )
        self.memory_extractor = memory_extractor or build_memory_extractor_from_env()
        # 单次请求覆盖出来的客户端缓存，键是 (角色, 档案, 模型)。
        #
        # 没有覆盖时一律返回上面那几个默认实例本身——不是等价对象，是同一个对象。
        # 这样「不带覆盖时行为不变」是构造上成立的，不依赖两条路径碰巧一致。
        self._override_clients: dict[tuple[str, str, str], object] = {}
        self._override_lock = RLock()
        self._knowledge_lock = RLock()
        # 同一对话的两个回合不能交错写入；不同对话却不该因为一个 provider 很慢而
        # 互相冻结。这里用普通 Lock 而非 RLock：StreamingResponse 可能在不同的
        # thread-pool worker 上推进/关闭生成器，普通 Lock 允许由关闭方可靠释放。
        self._conversation_turn_locks: dict[tuple[str, str], LockType] = {}
        self._conversation_turn_locks_guard = Lock()
        # 已被叫停的回合。停止请求走另一个 HTTP 请求（SSE 是单向的），所以这张表要
        # 跨线程可见；键就是 turn_id，客户端每次发送都生成一个新的。
        self._stopped_turns: OrderedDict[str, None] = OrderedDict()
        self._stopped_turns_guard = Lock()
        self._memory_wakeup = Event()
        self._memory_stop = Event()
        self._memory_thread: Thread | None = None

    def _resolve_client(
        self,
        role: str,
        default: object,
        factory: Callable[[ResolvedRole | None], object],
        override: ProviderOverride | None,
    ) -> object:
        """按角色取客户端；没有覆盖时返回默认实例本身。

        档案已被删除时 `resolve_override` 返回 None，这里同样退回默认——引用了旧档案
        的历史对话应当继续可用，而不是整条路径报错。
        """

        if override is None or not override.profile_id:
            return default
        resolved = resolve_override(role, override.profile_id, override.model)
        if resolved is None:
            return default
        key = (role, resolved.profile_id, resolved.model)
        with self._override_lock:
            client = self._override_clients.get(key)
            if client is None:
                client = factory(resolved)
                self._override_clients[key] = client
            return client

    def resolve_conversation_responder(
        self, override: ProviderOverride | None = None
    ) -> ConversationResponderProtocol:
        return self._resolve_client(
            ROLE_CONVERSATION,
            self.conversation_responder,
            build_conversation_responder_from_resolved,
            override,
        )

    def resolve_solution_generator(
        self, override: ProviderOverride | None = None
    ) -> SolutionGeneratorProtocol:
        return self._resolve_client(
            ROLE_SOLVER,
            self.generator,
            build_solution_generator_from_resolved,
            override,
        )

    def resolve_target_drafter(
        self, override: ProviderOverride | None = None
    ) -> TargetDrafterProtocol:
        return self._resolve_client(
            ROLE_TARGET_DRAFTER,
            self.target_drafter,
            build_target_drafter_from_resolved,
            override,
        )

    def rename_conversation(
        self, workspace_id: str, conversation_id: str, title: str
    ) -> Conversation:
        return self.workspaces.store(workspace_id).rename_conversation(
            conversation_id, title
        )

    def set_conversation_status(
        self,
        workspace_id: str,
        conversation_id: str,
        status: ConversationStatus,
    ) -> Conversation:
        return self.workspaces.store(workspace_id).set_conversation_status(
            conversation_id, status
        )

    def search_conversation_messages(
        self, workspace_id: str, query: str, limit: int = 50
    ) -> list[ConversationMessage]:
        return self.workspaces.store(workspace_id).search_conversation_messages(
            query, limit
        )

    def set_conversation_provider(
        self,
        workspace_id: str,
        conversation_id: str,
        provider: ProviderOverride | None,
    ) -> Conversation:
        return self.workspaces.store(workspace_id).set_conversation_provider(
            conversation_id, provider
        )

    def create_workspace(self, request: WorkspaceCreate) -> Workspace:
        return self.workspaces.create(request)

    def get_workspace(self, workspace_id: str) -> Workspace:
        return self.workspaces.get(workspace_id)

    def list_workspaces(self) -> list[Workspace]:
        return self.workspaces.list()

    def start_background_workers(self) -> None:
        if self._memory_thread is not None and self._memory_thread.is_alive():
            return
        for workspace in self.workspaces.list():
            self.workspaces.store(workspace.id).recover_running_memory_jobs()
        self._memory_stop.clear()
        self._memory_thread = Thread(
            target=self._memory_worker_loop,
            daemon=True,
            name="math-harness-memory",
        )
        self._memory_thread.start()
        self._memory_wakeup.set()

    def stop_background_workers(self) -> None:
        self._memory_stop.set()
        self._memory_wakeup.set()
        thread = self._memory_thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._memory_thread = None

    def _memory_worker_loop(self) -> None:
        while not self._memory_stop.is_set():
            worked = self.process_memory_jobs_once()
            if worked:
                continue
            self._memory_wakeup.wait(timeout=0.5)
            self._memory_wakeup.clear()

    def process_memory_jobs_once(self) -> bool:
        if not self.memory_extractor.available:
            return False
        for workspace in self.workspaces.list():
            store = self.workspaces.store(workspace.id)
            job = store.claim_next_memory_job()
            if job is None:
                continue
            self._process_memory_job(store, job)
            return True
        return False

    def list_memories(
        self,
        workspace_id: str,
        *,
        query: str | None = None,
        kind: MemoryKind | None = None,
        status: MemoryStatus | None = MemoryStatus.ACTIVE,
        limit: int = 100,
    ) -> list[MemoryItem]:
        return self.workspaces.store(workspace_id).list_memories(
            query=query,
            kind=kind,
            status=status,
            limit=limit,
        )

    def create_memory(self, workspace_id: str, request: MemoryCreate) -> MemoryItem:
        store = self.workspaces.store(workspace_id)
        with store.atomic():
            memory, created = store.create_memory_item(
                kind=request.kind,
                content=request.content,
                tags=request.tags,
                pinned=request.pinned,
                source=MemorySource.MANUAL,
            )
            store.record_learning_event(
                "memory_created" if created else "memory_duplicate_ignored",
                memory.id,
                {"kind": memory.kind.value, "source": memory.source.value},
            )
        return memory

    def update_memory(
        self,
        workspace_id: str,
        memory_id: str,
        request: MemoryUpdate,
    ) -> MemoryItem:
        store = self.workspaces.store(workspace_id)
        with store.atomic():
            before = store.get_memory(memory_id)
            updated = store.update_memory_item(
                memory_id,
                content=request.content,
                kind=request.kind,
                tags=request.tags,
                pinned=request.pinned,
                status=request.status,
            )
            event_type = "memory_updated"
            if (
                before.status is MemoryStatus.ARCHIVED
                and updated.status is MemoryStatus.ACTIVE
            ):
                event_type = "memory_restored"
            elif (
                before.status is MemoryStatus.ACTIVE
                and updated.status is MemoryStatus.ARCHIVED
            ):
                event_type = "memory_archived"
            store.record_learning_event(
                event_type,
                memory_id,
                {
                    "before": before.model_dump(mode="json"),
                    "after": updated.model_dump(mode="json"),
                },
            )
        return updated

    def archive_memory(self, workspace_id: str, memory_id: str) -> MemoryItem:
        store = self.workspaces.store(workspace_id)
        with store.atomic():
            before = store.get_memory(memory_id)
            archived = store.archive_memory(memory_id)
            store.record_learning_event(
                "memory_archived",
                memory_id,
                {"previous_status": before.status.value},
            )
        return archived

    def get_memory_settings(self, workspace_id: str) -> MemorySettings:
        return self.workspaces.store(workspace_id).get_memory_settings()

    def update_memory_settings(
        self,
        workspace_id: str,
        request: MemorySettingsUpdate,
    ) -> MemorySettings:
        store = self.workspaces.store(workspace_id)
        with store.atomic():
            settings = store.update_memory_settings(
                request.automatic_extraction_enabled
            )
            store.record_learning_event(
                "memory_settings_updated",
                workspace_id,
                {
                    "automatic_extraction_enabled": (
                        settings.automatic_extraction_enabled
                    )
                },
            )
        if settings.automatic_extraction_enabled:
            self._memory_wakeup.set()
        return settings

    def get_memory_health(self, workspace_id: str) -> MemoryHealth:
        return self.workspaces.store(workspace_id).memory_health(
            extractor_available=self.memory_extractor.available
        )

    def get_memory_job(
        self,
        workspace_id: str,
        job_id: str,
    ) -> MemoryExtractionJob:
        return self.workspaces.store(workspace_id).get_memory_job(job_id)

    def enqueue_memory_extraction(
        self,
        workspace_id: str,
        conversation_id: str,
        *,
        from_ordinal: int | None = None,
    ) -> MemoryExtractionJob:
        if not self.memory_extractor.available:
            raise InvalidKnowledgeState(
                "automatic memory requires a configured MiMo or OpenAI model"
            )
        store = self.workspaces.store(workspace_id)
        conversation = store.get_conversation(conversation_id)
        messages = store.list_conversation_messages(conversation_id)
        revision = memory_source_revision(messages, conversation.message_count)
        job = store.enqueue_memory_job(
            conversation_id,
            from_ordinal=from_ordinal,
            through_ordinal=conversation.message_count,
            source_revision=revision,
        )
        if job is None:
            raise InvalidKnowledgeState("conversation has no unprocessed messages")
        self._memory_wakeup.set()
        return job

    def backfill_memories(self, workspace_id: str) -> MemoryBackfillResult:
        if not self.memory_extractor.available:
            raise InvalidKnowledgeState(
                "memory backfill requires a configured MiMo or OpenAI model"
            )
        store = self.workspaces.store(workspace_id)
        jobs: list[MemoryExtractionJob] = []
        skipped = 0
        for conversation in store.list_conversations():
            if conversation.message_count == 0:
                skipped += 1
                continue
            messages = store.list_conversation_messages(conversation.id)
            revision = memory_source_revision(messages, conversation.message_count)
            job = store.enqueue_memory_job(
                conversation.id,
                from_ordinal=0,
                through_ordinal=conversation.message_count,
                source_revision=revision,
            )
            if job is None:
                skipped += 1
            else:
                jobs.append(job)
        if jobs:
            self._memory_wakeup.set()
        return MemoryBackfillResult(
            workspace_id=workspace_id,
            queued_jobs=jobs,
            skipped_conversation_count=skipped,
        )

    def _process_memory_job(
        self,
        store: WorkspaceStore,
        job: MemoryExtractionJob,
    ) -> None:
        started = perf_counter()
        try:
            all_messages = store.list_conversation_messages(job.conversation_id)
            current_revision = memory_source_revision(
                all_messages,
                job.through_ordinal,
            )
            if current_revision != job.source_revision:
                raise _StaleMemoryRevision

            source_messages = [
                message
                for message in all_messages
                if job.from_ordinal < message.ordinal <= job.through_ordinal
                and message.role is ConversationRole.USER
            ]
            existing = store.list_memories(
                status=MemoryStatus.ACTIVE,
                limit=30,
            )
            if source_messages:
                result = self.memory_extractor.extract(source_messages, existing)
            else:
                result = MemoryExtractionResult(
                    candidates=[],
                    provider=self.memory_extractor.name,
                    model=getattr(self.memory_extractor, "model", None),
                    duration_ms=0,
                )

            messages_by_id = {message.id: message for message in source_messages}
            existing_by_id = {memory.id: memory for memory in existing}
            accepted = []
            for candidate in result.candidates:
                if not candidate_is_grounded(candidate, messages_by_id):
                    continue
                if contains_sensitive_memory(
                    candidate.content
                ) or contains_sensitive_memory(candidate.evidence):
                    continue
                if contains_memory_control_instruction(
                    candidate.content
                ) or contains_memory_control_instruction(candidate.evidence):
                    continue
                if looks_like_untrusted_math_claim(candidate):
                    continue
                if looks_like_untrusted_math_text(candidate.evidence):
                    continue
                if any(
                    contains_sensitive_memory(tag)
                    or contains_memory_control_instruction(tag)
                    or looks_like_untrusted_math_text(tag)
                    for tag in candidate.tags
                ):
                    continue
                if candidate.replaces_memory_id:
                    replaced = existing_by_id.get(candidate.replaces_memory_id)
                    if replaced is None or replaced.kind is not candidate.kind:
                        continue
                accepted.append(candidate)

            created_count = 0
            with store.atomic():
                latest_messages = store.list_conversation_messages(job.conversation_id)
                latest_revision = memory_source_revision(
                    latest_messages,
                    job.through_ordinal,
                )
                if latest_revision != job.source_revision:
                    raise _StaleMemoryRevision
                for candidate in accepted:
                    memory, created = store.create_memory_item(
                        kind=candidate.kind,
                        content=candidate.content,
                        tags=candidate.tags,
                        pinned=False,
                        source=MemorySource.AUTOMATIC,
                        conversation_id=job.conversation_id,
                        source_message_id=candidate.source_message_id,
                        evidence=candidate.evidence,
                        supersedes_id=candidate.replaces_memory_id,
                    )
                    if not created:
                        continue
                    created_count += 1
                    store.record_learning_event(
                        "memory_extracted",
                        memory.id,
                        {
                            "job_id": job.id,
                            "kind": memory.kind.value,
                            "source_message_id": memory.source_message_id,
                            "supersedes_id": memory.supersedes_id,
                        },
                    )
                store.update_memory_cursor(job.conversation_id, job.through_ordinal)
                store.complete_memory_job(
                    job.id,
                    extracted_count=created_count,
                    provider=result.provider,
                    model=result.model,
                    duration_ms=result.duration_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            store.record_learning_event(
                "memory_extraction_completed",
                job.id,
                {
                    "conversation_id": job.conversation_id,
                    "through_ordinal": job.through_ordinal,
                    "extracted_count": created_count,
                },
            )
        except _StaleMemoryRevision:
            store.mark_memory_job_stale(job.id)
            conversation = store.get_conversation(job.conversation_id)
            latest_messages = store.list_conversation_messages(job.conversation_id)
            replacement_revision = memory_source_revision(
                latest_messages,
                conversation.message_count,
            )
            replacement = store.enqueue_memory_job(
                job.conversation_id,
                from_ordinal=job.from_ordinal,
                through_ordinal=conversation.message_count,
                source_revision=replacement_revision,
            )
            if replacement is not None:
                self._memory_wakeup.set()
        except Exception as exc:  # noqa: BLE001
            failed = store.fail_memory_job(
                job.id,
                f"{exc.__class__.__name__}: {exc}",
                provider=getattr(self.memory_extractor, "name", None),
                model=getattr(self.memory_extractor, "model", None),
                duration_ms=max(0, round((perf_counter() - started) * 1_000)),
            )
            if failed.status is MemoryJobStatus.FAILED:
                store.record_learning_event(
                    "memory_extraction_failed",
                    job.id,
                    {"error": failed.error or "unknown error"},
                )
            else:
                self._memory_wakeup.set()

    def _enqueue_turn_memory(
        self,
        store: WorkspaceStore,
        conversation_id: str,
        through_ordinal: int,
    ) -> MemoryExtractionJob | None:
        if (
            not self.memory_extractor.available
            or not store.get_memory_settings().automatic_extraction_enabled
        ):
            # Disabled periods are intentionally skipped. Enabling a model later
            # must not silently upload older messages; explicit backfill remains
            # available behind the App's confirmation dialog.
            store.update_memory_cursor(conversation_id, through_ordinal)
            return None
        messages = store.list_conversation_messages(conversation_id)
        revision = memory_source_revision(messages, through_ordinal)
        job = store.enqueue_memory_job(
            conversation_id,
            through_ordinal=through_ordinal,
            source_revision=revision,
        )
        if job is not None:
            self._memory_wakeup.set()
        return job

    def create_conversation(
        self,
        workspace_id: str,
        request: ConversationCreate,
    ) -> Conversation:
        self.workspaces.get(workspace_id)
        now = utc_now()
        conversation = Conversation(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            title=request.title or "新对话",
            created_at=now,
            updated_at=now,
        )
        store = self.workspaces.store(workspace_id)
        store.add_conversation(conversation)
        store.record_learning_event(
            "conversation_created",
            conversation.id,
            {"title": conversation.title},
        )
        return conversation

    def get_conversation(
        self,
        workspace_id: str,
        conversation_id: str,
    ) -> Conversation:
        return self.workspaces.store(workspace_id).get_conversation(conversation_id)

    def list_conversations(
        self, workspace_id: str, include_archived: bool = False
    ) -> list[Conversation]:
        return self.workspaces.store(workspace_id).list_conversations(include_archived)

    def list_conversation_messages(
        self,
        workspace_id: str,
        conversation_id: str,
    ) -> list[ConversationMessage]:
        return self.workspaces.store(workspace_id).list_conversation_messages(
            conversation_id
        )

    def send_conversation_turn(
        self,
        workspace_id: str,
        conversation_id: str,
        request: ConversationTurnRequest,
    ) -> ConversationTurnResult:
        with self._conversation_turn_lock(workspace_id, conversation_id):
            with self._knowledge_lock:
                setup = self._begin_turn(workspace_id, conversation_id, request)
            if isinstance(setup, ConversationTurnResult):
                return setup

            generation: ChatGeneration | None = None
            if request.math_target is None:
                # 网络等待不占全服务的知识锁。上下文已经是一个不可变快照，生成结束后
                # 再短暂取锁做检查与原子化持久化即可。
                generation = self._generate_chat_response(
                    setup.context,
                    request.message,
                    request.max_output_tokens,
                    override=setup.override,
                )
            with self._knowledge_lock:
                return self._finish_turn(
                    workspace_id, conversation_id, request, setup, generation
                )

    def _conversation_turn_lock(
        self, workspace_id: str, conversation_id: str
    ) -> LockType:
        key = (workspace_id, conversation_id)
        with self._conversation_turn_locks_guard:
            lock = self._conversation_turn_locks.get(key)
            if lock is None:
                lock = Lock()
                self._conversation_turn_locks[key] = lock
            return lock

    def _begin_turn(
        self,
        workspace_id: str,
        conversation_id: str,
        request: ConversationTurnRequest,
    ) -> _TurnSetup | ConversationTurnResult:
        """准备一个回合：幂等重放、检索、上下文、落用户消息。

        同步与流式两条路共用这一段。返回 `ConversationTurnResult` 表示这个 turn_id
        已经完整答过了，直接把原结果给回去——重发不该再生成一次。
        """

        store = self.workspaces.store(workspace_id)
        conversation = store.get_conversation(conversation_id)
        # 解析顺序：本次请求指定 > 这个对话记住的 > 全局设置。
        effective_override = request.provider or conversation.provider
        turn_id = request.turn_id or str(uuid.uuid4())
        existing = store.get_conversation_turn_messages(conversation_id, turn_id)
        user_message = next(
            (item for item in existing if item.role is ConversationRole.USER),
            None,
        )
        assistant_message = next(
            (item for item in existing if item.role is ConversationRole.ASSISTANT),
            None,
        )
        if user_message is not None and user_message.content != request.message:
            raise InvalidKnowledgeState(
                "turn_id already belongs to a different conversation message"
            )
        if user_message is not None and assistant_message is not None:
            attempt = (
                store.get_solution_attempt(assistant_message.attempt_id)
                if assistant_message.attempt_id
                else None
            )
            knowledge_draft = (
                store.get_example(assistant_message.knowledge_draft_id)
                if assistant_message.knowledge_draft_id
                else None
            )
            return ConversationTurnResult(
                conversation=store.get_conversation(conversation_id),
                user_message=user_message,
                assistant_message=assistant_message,
                attempt=attempt,
                knowledge_draft=knowledge_draft,
                summary_updated=False,
            )

        matches = self.search_methods(
            workspace_id,
            request.message,
            tags=request.tags,
            top_k=request.top_k,
            math_target=request.math_target,
        )
        context = self._build_conversation_context(
            workspace_id,
            conversation_id,
            matches,
            memory_query=request.message,
            excluding_turn_id=turn_id,
        )
        kind = (
            ConversationMessageKind.SOLVE
            if request.math_target is not None
            else ConversationMessageKind.CHAT
        )
        if user_message is None:
            user_message = store.append_conversation_message(
                conversation_id,
                turn_id,
                ConversationRole.USER,
                kind,
                request.message,
            )
        return _TurnSetup(
            store=store,
            turn_id=turn_id,
            user_message=user_message,
            context=context,
            matches=matches,
            kind=kind,
            override=effective_override,
        )

    def _finish_turn(
        self,
        workspace_id: str,
        conversation_id: str,
        request: ConversationTurnRequest,
        setup: _TurnSetup,
        generation: ChatGeneration | None = None,
    ) -> ConversationTurnResult:
        """回合的后半段：求解或聊天、检查、入库、落库、记忆任务。

        `generation` 由流式路径传进来——正文已经吐给用户了，这里不能再生成一次。
        """

        store = setup.store
        turn_id = setup.turn_id
        user_message = setup.user_message
        context = setup.context
        matches = setup.matches
        kind = setup.kind
        effective_override = setup.override

        attempt: SolutionAttempt | None = None
        knowledge_draft: ProblemExample | None = None
        generation_error: str | None = None
        if request.math_target is not None:
            solve_request = SolveRequest(
                problem=request.message,
                tags=request.tags,
                top_k=request.top_k,
                math_target=request.math_target,
                max_output_tokens=request.max_output_tokens,
            )
            attempt = self._build_solution_attempt(
                workspace_id,
                solve_request,
                conversation_context=context.model_payload(),
            )
            attempt = self._persist_attempt_and_capture(workspace_id, attempt)
            knowledge_draft = store.get_example_by_source_attempt(attempt.id)
            candidate = attempt.candidate
            assistant_content = (
                self._candidate_solution_text(candidate)
                if candidate is not None
                else attempt.generation.error or attempt.verification.summary
            )
            method_keys = candidate.used_method_keys if candidate else []
            provider = attempt.generation.provider
            model = attempt.generation.model
            verification_status = attempt.verification.status
        else:
            if generation is None:
                generation = self._generate_chat_response(
                    context,
                    request.message,
                    request.max_output_tokens,
                    override=effective_override,
                )
            assistant_content = generation.content
            method_keys = [match.method.key for match in matches]
            provider = generation.provider
            model = generation.model
            verification_status = None
            generation_error = generation.error

        if generation_error is None:
            # 聊天路径也过检查。以前这条路一次检查都不做，于是「只有渐进题能被验证」——
            # 而模型本来就答得了各领域的题，卡住的从来不是模型，是这道闸。
            assessment, checked_claims = self._check_assistant_answer(
                request.message,
                assistant_content,
                answer_profile_id=provider,
            )
            if knowledge_draft is None:
                knowledge_draft = self._capture_chat_knowledge(
                    workspace_id,
                    store,
                    problem=request.message,
                    answer=assistant_content,
                    tags=request.tags,
                    assessment=assessment,
                )
        else:
            # 断流后的正文只是一份可恢复的现场，不是答案。即使里面碰巧有一条能被
            # SymPy 验过的等式，也不能因此获得可信度或进入成长闭环。
            assessment = None
            checked_claims = []

        assistant_message = store.append_conversation_message(
            conversation_id,
            turn_id,
            ConversationRole.ASSISTANT,
            kind,
            assistant_content,
            provider=provider,
            model=model,
            generation_error=generation_error,
            attempt_id=attempt.id if attempt else None,
            knowledge_draft_id=knowledge_draft.id if knowledge_draft else None,
            verification_status=verification_status,
            conclusion_confidence=assessment.conclusion if assessment else None,
            process_confidence=assessment.process if assessment else None,
            counterexample=assessment.counterexample if assessment else {},
            checked_claims=checked_claims,
            method_keys=method_keys,
        )
        summary_updated = self._compact_conversation(store, conversation_id)
        conversation = store.get_conversation(conversation_id)
        memory_job = self._enqueue_turn_memory(
            store,
            conversation_id,
            assistant_message.ordinal,
        )
        store.record_learning_event(
            (
                "conversation_turn_interrupted"
                if generation_error
                else "conversation_turn_completed"
            ),
            turn_id,
            {
                "conversation_id": conversation_id,
                "kind": kind.value,
                "attempt_id": attempt.id if attempt else None,
                "knowledge_draft_id": knowledge_draft.id if knowledge_draft else None,
                "verification_status": (
                    verification_status.value if verification_status else None
                ),
                "summary_updated": summary_updated,
                "memory_job_id": memory_job.id if memory_job else None,
                "generation_error": generation_error,
            },
        )
        return ConversationTurnResult(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            attempt=attempt,
            knowledge_draft=knowledge_draft,
            summary_updated=summary_updated,
            memory_job=memory_job,
        )

    def stream_conversation_turn(
        self,
        workspace_id: str,
        conversation_id: str,
        request: ConversationTurnRequest,
    ) -> Generator[str, None, ConversationTurnResult]:
        """逐段产出正文，结束时返回和同步路径完全一样的回合结果。

        **检查、入库、落库全部在整条回复吐完之后才跑。** 可信度徽章不能一边流一边变
        ——用户看到「先说对、又说错」比等一下要糟得多。而且检查本来就要看完整的推导：
        逐步检查在只有半条推导时给出的判断没有意义。

        指定了验算目标的回合不流式：那条路的正文是由求解器和验证器一起产出的，中间
        没有可以逐字给出的东西。
        """

        try:
            with self._conversation_turn_lock(workspace_id, conversation_id):
                with self._knowledge_lock:
                    setup = self._begin_turn(workspace_id, conversation_id, request)
                if isinstance(setup, ConversationTurnResult):
                    # 这个 turn_id 已经完整答过了。重放不重新生成，把原文一次给回去。
                    if setup.assistant_message is not None:
                        yield setup.assistant_message.content
                    return setup

                generation: ChatGeneration | None = None
                if request.math_target is None:
                    responder = self.resolve_conversation_responder(setup.override)
                    turn_id = setup.turn_id
                    generation = yield from stream_chat_generation(
                        responder,
                        setup.context,
                        request.message,
                        request.max_output_tokens,
                        should_stop=lambda: self._turn_is_stopped(turn_id),
                    )
                with self._knowledge_lock:
                    return self._finish_turn(
                        workspace_id, conversation_id, request, setup, generation
                    )
        finally:
            if request.turn_id:
                self._clear_turn_stop(request.turn_id)

    def request_turn_stop(self, turn_id: str) -> None:
        """标记这一回合应当停止。

        停止**不是取消**：已经吐出去的正文照常落库，只是带上中断标记——不检查、不给
        可信度、不进知识库。用户按停止和网络断掉在这一点上没有区别，走同一条路。

        故意不校验 turn_id 是否正在生成：停止请求可能比生成请求先到（客户端一发出去
        就能按），那时拒绝掉，用户看到的就是「按了没反应」。落一个标记等它自己来取，
        代价只是一条僵尸记录，而下面那个上限管着它。
        """

        with self._stopped_turns_guard:
            self._stopped_turns[turn_id] = None
            self._stopped_turns.move_to_end(turn_id)
            while len(self._stopped_turns) > self._MAX_TRACKED_STOPS:
                self._stopped_turns.popitem(last=False)

    #: 停止标记的保留上限。回合结束时会自己清掉，这条只防没人来取的僵尸记录堆积。
    _MAX_TRACKED_STOPS = 512

    def _turn_is_stopped(self, turn_id: str) -> bool:
        with self._stopped_turns_guard:
            return turn_id in self._stopped_turns

    def _clear_turn_stop(self, turn_id: str) -> None:
        with self._stopped_turns_guard:
            self._stopped_turns.pop(turn_id, None)

    #: 允许晋级的结论档位。
    #
    # 判据是「**有程序**查过」，不是「有人说对」。这四档都来自一段代码给出的结论：
    # 符号化简、随机实例化、独立引擎重算。`peer_reviewed` 不在里面——另一个模型同意
    # 仍然只是意见，而晋级意味着以后会被检索出来当依据用。
    _PROMOTABLE_CONCLUSIONS = frozenset(
        {
            ConclusionConfidence.PROOF_VERIFIED,
            ConclusionConfidence.VERIFIED,
            ConclusionConfidence.NUMERICALLY_CHECKED,
            ConclusionConfidence.CROSS_CHECKED,
        }
    )

    @staticmethod
    def _signature_features(
        problem: str, payload: MathPayload | None
    ) -> StructuralFeatures:
        """方法卡签名用的结构特征。

        签名描述「这个方法适用于什么形状的题」，所以取的是**题面**结构——检索时手里
        只有提问，两边必须是同一种东西。

        没有渐进目标的例题以前拿到的是空签名，于是它们的方法卡在结构检索里完全不可见：
        库里有卡，但结构那一路永远打不中。
        """

        if payload is not None:
            return extract_features(payload)
        return features_from_text(problem)

    def _verify_for_ingestion(self, request: ExampleCreate) -> VerificationReport:
        """入库时取一次验证结论。

        有渐进目标的走原来的验证器，逐字不变。

        没有目标的走检查流水线。以前这条路只会得到「未提供结构化数学表达式」——手工
        录入和批量导入的跨领域知识因此永远停在待复核，和聊天路径 v0.15 之前的处境
        一模一样。检查流水线本来就不认领域，没有理由只给聊天路径用。
        """

        if request.math_payload is not None:
            return self.verifier.verify(request.math_payload)

        assessment, _ = self._check_assistant_answer(request.problem, request.solution)
        if assessment is None:
            return self.verifier.verify(None)
        return self._report_from(assessment)

    def _verification_for_promotion(
        self, example: ProblemExample
    ) -> VerificationReport:
        """晋级前重新取一次验证结论。

        有渐进目标的走原来的验证器，逐字不变——那条路被大量测试覆盖着。

        没有目标的走已经存过的双轴结论。以前这里无条件调 `verify(example.math_payload)`，
        聊天路径的 `math_payload` 恒为 `None`，于是**一条已经被 SymPy 符号验证过的解答
        照样晋级失败**：门禁把 v0.15 已经得出的结论整个丢掉，重新用只认渐进形状的验证器
        再验一遍。结果是跨领域的知识永远停在待复核，永远进不了检索。
        """

        if example.math_payload is not None:
            return self.verifier.verify(example.math_payload)

        stored = example.verification
        if (
            stored.conclusion in self._PROMOTABLE_CONCLUSIONS
            and stored.process_confidence is not ProcessConfidence.STEP_FAILED
        ):
            return stored.model_copy(update={"status": VerificationStatus.VERIFIED})
        return stored.model_copy(update={"status": VerificationStatus.NEEDS_REVIEW})

    def _capture_chat_knowledge(
        self,
        workspace_id: str,
        store: WorkspaceStore,
        *,
        problem: str,
        answer: str,
        tags: list[str],
        assessment: ConfidenceAssessment | None,
    ) -> ProblemExample | None:
        """把一次聊天问答存成待复核的知识草稿。

        入库门禁刻意收得很紧：**只有真的抽出了可检验内容、而且没被反例推翻的回合才
        进库**。每个回合都建草稿会把知识库淹掉，而纯讲解的回合本来也没有可复用的东西
        ——这正是「不会记住无关紧要的信息防止污染」的落点。

        草稿一律是 `pending_review`：这条路上的答案再怎么查也是概率性检查，
        晋级仍然要人工确认。
        """

        if assessment is None or not assessment.may_enter_knowledge_base:
            return None
        try:
            return self._ingest_example(
                workspace_id,
                ExampleCreate(
                    problem=problem,
                    solution=answer,
                    tags=tags,
                    reviewed=False,
                ),
                origin=ExampleOrigin.CONVERSATION,
                verification_override=self._report_from(assessment),
                store=store,
            ).example
        except Exception:  # noqa: BLE001
            # 入库是附加价值，不是前置条件。存不进去也不能把这一轮对话弄丢。
            return None

    @staticmethod
    def _report_from(assessment: ConfidenceAssessment) -> VerificationReport:
        """把双轴折算成旧的三值状态，同时把两轴原样保留。

        **只有确定性的符号判定才映射到 `VERIFIED`**。三值状态是晋级门禁看的东西，
        把概率性检查折算进去，等于让「随机取值都对」拿到和符号证明一样的待遇。
        """

        deterministic = assessment.conclusion in {
            ConclusionConfidence.VERIFIED,
            ConclusionConfidence.PROOF_VERIFIED,
        }
        return VerificationReport(
            status=(
                VerificationStatus.VERIFIED
                if deterministic
                else VerificationStatus.NEEDS_REVIEW
            ),
            summary=f"聊天路径检查：{assessment.conclusion.value} / {assessment.process.value}",
            conclusion_confidence=assessment.conclusion,
            process_confidence=assessment.process,
            counterexample=assessment.counterexample,
        )

    def _check_assistant_answer(
        self,
        problem: str,
        answer: str,
        *,
        answer_profile_id: str | None = None,
    ) -> tuple[ConfidenceAssessment | None, list[str]]:
        """从回答里抽出断言，跑一遍检查流水线。

        抽不出可检验内容时返回 `(None, [])`——那不是失败，只是这条回答没有可机检的
        部分，照样正常展示。检查本身出问题也不能把用户的回答弄丢，所以整段兜住异常。

        返回的断言原文要展示给用户：抽错题的风险始终存在（会验证一个你没问的命题），
        处理方式是让它**可见**，而不是事前拦着不让走。
        """

        try:
            draft = self.claim_drafter.draft(problem, answer)
            if not draft.is_checkable:
                return None, []
            checks = [
                SymbolicEqualityCheck(),
                InstantiationCheck(),
                StepInstantiationCheck(),
            ]
            # 后两层各自默认关闭：没配 Wolfram 就没有独立重算，没配第二个 provider
            # 就没有复核。两者都只在真的能带来独立信息时才跑。
            if self.tool_client is not None:
                checks.append(IndependentRecomputeCheck(client=self.tool_client))
            if self.reviewer is not None:
                checks.append(
                    PeerReviewCheck(
                        reviewer=self.reviewer,
                        answer_profile_id=answer_profile_id,
                    )
                )
            report = run_checks(
                checks,
                CheckContext(
                    problem=problem,
                    claim=draft.claim,
                    steps=draft.steps,
                    answer_text=answer,
                ),
            )
            claims = [f"{item.lhs} = {item.rhs}" for item in draft.steps if item.rhs]
            return assess(report), claims
        except Exception:  # noqa: BLE001
            return None, []

    def _build_conversation_context(
        self,
        workspace_id: str,
        conversation_id: str,
        matches: list[MethodMatch],
        *,
        memory_query: str,
        excluding_turn_id: str | None = None,
    ) -> ConversationContext:
        workspace = self.workspaces.get(workspace_id)
        store = self.workspaces.store(workspace_id)
        conversation = store.get_conversation(conversation_id)
        messages = store.list_conversation_messages(
            conversation_id,
            after_ordinal=conversation.summary_through_ordinal,
        )
        if excluding_turn_id is not None:
            messages = [
                message for message in messages if message.turn_id != excluding_turn_id
            ]
        return ConversationContext(
            workspace=workspace,
            summary=conversation.summary,
            recent_messages=messages[-_CONVERSATION_CONTEXT_MESSAGES:],
            trusted_methods=matches,
            soft_memories=store.select_memory_context(memory_query),
        )

    def _generate_chat_response(
        self,
        context: ConversationContext,
        message: str,
        max_output_tokens: int,
        override: ProviderOverride | None = None,
    ) -> ChatGeneration:
        responder = self.resolve_conversation_responder(override)
        try:
            return responder.respond(context, message, max_output_tokens)
        except Exception as exc:  # noqa: BLE001
            provider = getattr(responder, "name", responder.__class__.__name__)
            error = f"{exc.__class__.__name__}: {exc}"[:2_000]
            return ChatGeneration(
                content=(
                    "这次对话回复生成失败，但你的消息已经保存在当前工作区。"
                    "请检查模型设置或网络后重新发送。"
                ),
                provider=provider,
                model=getattr(responder, "model", None),
                prompt_version=getattr(responder, "prompt_version", "unknown"),
                error=error,
            )

    def _compact_conversation(
        self,
        store: WorkspaceStore,
        conversation_id: str,
    ) -> bool:
        conversation = store.get_conversation(conversation_id)
        unsummarized = store.list_conversation_messages(
            conversation_id,
            after_ordinal=conversation.summary_through_ordinal,
        )
        if len(unsummarized) <= _CONVERSATION_SUMMARY_TRIGGER:
            return False
        compacted = unsummarized[:-_CONVERSATION_SUMMARY_RETAIN]
        if not compacted:
            return False
        summary = self.conversation_summarizer.summarize(
            conversation.summary,
            compacted,
        )
        through_ordinal = compacted[-1].ordinal
        store.update_conversation_summary(
            conversation_id,
            summary,
            through_ordinal,
        )
        store.record_learning_event(
            "conversation_summary_updated",
            conversation_id,
            {
                "through_ordinal": through_ordinal,
                "summary_characters": len(summary),
            },
        )
        return True

    def export_workspace_backup(self, workspace_id: str) -> bytes:
        return create_workspace_archive(self.workspaces, workspace_id)

    def restore_workspace_backup(self, payload: bytes) -> WorkspaceRestoreResult:
        with self._knowledge_lock:
            result = restore_workspace_archive(self.workspaces, payload)
            store = self.workspaces.store(result.workspace.id)
            if store.recover_running_memory_jobs() > 0:
                self._memory_wakeup.set()
            return result

    def draft_math_target(
        self,
        workspace_id: str,
        request: MathTargetDraftRequest,
    ) -> MathTargetDraftResult:
        # Resolve the workspace first so drafting cannot be used as a cross-scope
        # side channel from an invalid workspace identifier.
        self.workspaces.get(workspace_id)
        return self.target_drafter.draft(request.problem)

    def ingest_example(
        self, workspace_id: str, request: ExampleCreate
    ) -> IngestionResult:
        with self._knowledge_lock:
            return self._ingest_example(
                workspace_id,
                request,
                origin=ExampleOrigin.MANUAL,
            )

    def bulk_import_examples(
        self,
        workspace_id: str,
        request: BulkExampleImportRequest,
    ) -> BulkExampleImportResult:
        with self._knowledge_lock:
            return self._bulk_import_examples(workspace_id, request)

    def _bulk_import_examples(
        self,
        workspace_id: str,
        request: BulkExampleImportRequest,
    ) -> BulkExampleImportResult:
        store = self.workspaces.store(workspace_id)
        detected_format, parsed = parse_example_corpus(
            request.content,
            request.file_format,
            request.review_policy,
            source_name=request.source_name,
        )
        known_fingerprints = {
            stored_example_fingerprint(example) for example in store.list_examples()
        }
        ready: list[tuple[ParsedImportItem, VerificationReport]] = []
        item_results: list[BulkImportItemResult] = []
        for item in parsed:
            if item.request is None:
                item_results.append(
                    BulkImportItemResult(
                        index=item.index,
                        status=BulkImportItemStatus.INVALID,
                        errors=list(item.errors),
                    )
                )
                continue

            fingerprint = example_fingerprint(item.request)
            if fingerprint in known_fingerprints:
                item_results.append(
                    BulkImportItemResult(
                        index=item.index,
                        status=BulkImportItemStatus.DUPLICATE,
                        problem_preview=problem_preview(item.request),
                        fingerprint=fingerprint,
                    )
                )
                continue

            known_fingerprints.add(fingerprint)
            verification = self.verifier.verify(item.request.math_payload)
            ready.append((item, verification))
            item_results.append(
                BulkImportItemResult(
                    index=item.index,
                    status=BulkImportItemStatus.READY,
                    problem_preview=problem_preview(item.request),
                    fingerprint=fingerprint,
                    verification=verification,
                )
            )

        invalid_count = sum(
            item.status is BulkImportItemStatus.INVALID for item in item_results
        )
        duplicate_count = sum(
            item.status is BulkImportItemStatus.DUPLICATE for item in item_results
        )
        can_commit = invalid_count == 0
        if not request.commit or not can_commit:
            return BulkExampleImportResult(
                source_name=request.source_name,
                detected_format=detected_format,
                commit_requested=request.commit,
                committed=False,
                can_commit=can_commit,
                total_count=len(item_results),
                ready_count=len(ready),
                duplicate_count=duplicate_count,
                invalid_count=invalid_count,
                imported_count=0,
                items=item_results,
            )

        extractor = (
            MethodExtractor()
            if request.extractor_policy is ImportExtractorPolicy.RULES
            else self.extractor
        )
        prepared = [
            self._prepare_ingestion(
                workspace_id,
                item.request,
                origin=ExampleOrigin.MANUAL,
                verification_override=verification,
                extractor=extractor,
            )
            for item, verification in ready
            if item.request is not None
        ]
        persisted: dict[int, IngestionResult] = {}
        with store.atomic():
            for (item, _), prepared_item in zip(ready, prepared, strict=True):
                persisted[item.index] = self._persist_prepared_ingestion(
                    store,
                    prepared_item,
                )

        committed_items: list[BulkImportItemResult] = []
        for result in item_results:
            ingestion = persisted.get(result.index)
            if ingestion is None:
                committed_items.append(result)
                continue
            committed_items.append(
                result.model_copy(
                    update={
                        "status": BulkImportItemStatus.IMPORTED,
                        "example_id": ingestion.example.id,
                        "method_keys": [
                            method.key for method in ingestion.learned_methods
                        ],
                    }
                )
            )
        return BulkExampleImportResult(
            source_name=request.source_name,
            detected_format=detected_format,
            commit_requested=True,
            committed=True,
            can_commit=True,
            total_count=len(committed_items),
            ready_count=len(ready),
            duplicate_count=duplicate_count,
            invalid_count=0,
            imported_count=len(persisted),
            items=committed_items,
        )

    def _ingest_example(
        self,
        workspace_id: str,
        request: ExampleCreate,
        *,
        origin: ExampleOrigin,
        source_attempt_id: str | None = None,
        verification_override: VerificationReport | None = None,
        extractor: MethodExtractorProtocol | None = None,
        store: WorkspaceStore | None = None,
    ) -> IngestionResult:
        workspace_store = store or self.workspaces.store(workspace_id)
        prepared = self._prepare_ingestion(
            workspace_id,
            request,
            origin=origin,
            source_attempt_id=source_attempt_id,
            verification_override=verification_override,
            extractor=extractor,
        )
        return self._persist_prepared_ingestion(workspace_store, prepared)

    def _prepare_ingestion(
        self,
        workspace_id: str,
        request: ExampleCreate,
        *,
        origin: ExampleOrigin,
        source_attempt_id: str | None = None,
        verification_override: VerificationReport | None = None,
        extractor: MethodExtractorProtocol | None = None,
    ) -> _PreparedIngestion:
        verification = verification_override or self._verify_for_ingestion(request)
        promotion_approved = (
            verification.status is VerificationStatus.VERIFIED and request.reviewed
        )
        if promotion_approved:
            status = KnowledgeStatus.PROMOTED
        elif verification.status is VerificationStatus.REJECTED:
            status = KnowledgeStatus.REJECTED
        else:
            status = KnowledgeStatus.PENDING_REVIEW

        extraction_result = self._extract_methods(
            request,
            verification.status,
            extractor=extractor,
            verification=verification,
        )
        if origin is ExampleOrigin.CONVERSATION:
            extraction_result = self._filter_conversation_extraction(extraction_result)
        now = utc_now()
        example = ProblemExample(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            problem=request.problem,
            solution=request.solution,
            tags=request.tags,
            method_hint=request.method_hint,
            reviewed=request.reviewed,
            problem_kind=classify_problem(request.problem),
            math_payload=request.math_payload,
            verification=verification,
            extraction=extraction_result.trace,
            method_drafts=[
                draft.model_dump(mode="json") for draft in extraction_result.methods
            ],
            status=status,
            origin=origin,
            source_attempt_id=source_attempt_id,
            reviewed_at=now if request.reviewed else None,
            created_at=now,
            updated_at=now,
        )
        return _PreparedIngestion(
            request=request,
            example=example,
            extraction_result=extraction_result,
            promotion_approved=promotion_approved,
        )

    def _persist_prepared_ingestion(
        self,
        store: WorkspaceStore,
        prepared: _PreparedIngestion,
    ) -> IngestionResult:
        request = prepared.request
        example = prepared.example
        extraction_result = prepared.extraction_result
        promotion_approved = prepared.promotion_approved
        store.add_example(example, extraction_result.methods)

        learned_methods: list[MethodCard] = []
        if example.verification.status is not VerificationStatus.REJECTED:
            method_status = (
                KnowledgeStatus.PROMOTED
                if promotion_approved
                else KnowledgeStatus.PENDING_REVIEW
            )
            # 签名会直接影响后续检索，因此它和方法晋级共用同一条信任边界：
            # 数学上可验证但尚未经人工复核的例子，也不能改写已晋级知识。
            features = (
                self._signature_features(request.problem, request.math_payload)
                if promotion_approved
                else None
            )
            for draft in extraction_result.methods:
                learned_methods.append(
                    store.upsert_method(
                        draft=draft,
                        example_id=example.id,
                        status=method_status,
                        verified=promotion_approved,
                        features=features,
                        confidence=example.verification.conclusion,
                    )
                )

        return IngestionResult(example=example, learned_methods=learned_methods)

    def _extract_methods(
        self,
        request: ExampleCreate,
        verification_status: VerificationStatus,
        *,
        extractor: MethodExtractorProtocol | None = None,
        verification: VerificationReport | None = None,
    ) -> MethodExtractionResult:
        selected_extractor = extractor or self.extractor
        provider = getattr(
            selected_extractor,
            "name",
            selected_extractor.__class__.__name__,
        )
        prompt_version = getattr(selected_extractor, "prompt_version", "unknown")
        if verification_status is VerificationStatus.REJECTED:
            return MethodExtractionResult(
                trace=MethodExtractionTrace(
                    provider=provider,
                    prompt_version=prompt_version,
                    status=ExtractionStatus.SKIPPED,
                    extracted_method_keys=[],
                )
            )
        # 过程轴门禁：推导被反例推翻时不提取方法卡。
        #
        # 阶段 H 实测，结论正确、某一中间步写错的解会通过全部只查结论的层。方法卡是从
        # 推导提取的，学下来就是一个错方法，还会被后续检索复用。
        #
        # 只在 `step_failed` 时拦截，不在 `step_unchecked` 时拦：旧的渐进路径压根没有
        # 步骤断言，一律按未检查处理会把方法提取整个停掉。等阶段 G 把所有输入接进新
        # 流水线，这条才谈得上全局生效。
        if (
            verification is not None
            and verification.process_confidence is ProcessConfidence.STEP_FAILED
        ):
            return MethodExtractionResult(
                trace=MethodExtractionTrace(
                    provider=provider,
                    prompt_version=prompt_version,
                    status=ExtractionStatus.SKIPPED,
                    error="推导中有步骤被反例推翻，不从中提取方法。",
                    extracted_method_keys=[],
                )
            )
        try:
            return selected_extractor.extract(
                request.problem, request.solution, request.method_hint
            )
        except Exception as exc:  # noqa: BLE001
            return MethodExtractionResult(
                trace=MethodExtractionTrace(
                    provider=provider,
                    model=getattr(selected_extractor, "model", None),
                    prompt_version=prompt_version,
                    status=ExtractionStatus.ERROR,
                    error=f"{exc.__class__.__name__}: {exc}"[:2_000],
                    extracted_method_keys=[],
                )
            )

    @staticmethod
    def _filter_conversation_extraction(
        extraction_result: MethodExtractionResult,
    ) -> MethodExtractionResult:
        # A generic fallback is useful for explicit corpus ingestion, but on every
        # chat turn it would create low-signal method cards.
        kept_methods = [
            draft
            for draft in extraction_result.methods
            if draft.key != "generic_example"
        ]
        if len(kept_methods) == len(extraction_result.methods):
            return extraction_result
        kept_keys = {draft.key for draft in kept_methods}
        return extraction_result.model_copy(
            update={
                "methods": kept_methods,
                "trace": extraction_result.trace.model_copy(
                    update={
                        "extracted_method_keys": [
                            key
                            for key in extraction_result.trace.extracted_method_keys
                            if key in kept_keys
                        ],
                        "evidence_by_method": {
                            key: evidence
                            for key, evidence in extraction_result.trace.evidence_by_method.items()
                            if key in kept_keys
                        },
                        "confidence_by_method": {
                            key: confidence
                            for key, confidence in extraction_result.trace.confidence_by_method.items()
                            if key in kept_keys
                        },
                    }
                ),
            }
        )

    def get_example(self, workspace_id: str, example_id: str) -> ProblemExample:
        return self.workspaces.store(workspace_id).get_example(example_id)

    def list_examples(self, workspace_id: str) -> list[ProblemExample]:
        return self.workspaces.store(workspace_id).list_examples()

    def list_example_versions(
        self,
        workspace_id: str,
        example_id: str,
    ) -> list[ExampleVersion]:
        return self.workspaces.store(workspace_id).list_example_versions(example_id)

    def update_example_draft(
        self,
        workspace_id: str,
        example_id: str,
        request: ExampleDraftUpdate,
    ) -> ExampleDraftUpdateResult:
        with self._knowledge_lock:
            return self._update_example_draft(workspace_id, example_id, request)

    def _update_example_draft(
        self,
        workspace_id: str,
        example_id: str,
        request: ExampleDraftUpdate,
    ) -> ExampleDraftUpdateResult:
        store = self.workspaces.store(workspace_id)
        example = store.get_example(example_id)
        if example.revision != request.expected_revision:
            raise InvalidKnowledgeState("知识草稿已被其他操作更新；请刷新后再保存。")
        if example.status is KnowledgeStatus.PROMOTED or example.reviewed:
            raise InvalidKnowledgeState("已晋级或已复核的例题不能作为草稿编辑。")
        if example.status is KnowledgeStatus.DEPRECATED or (
            example.status is KnowledgeStatus.REJECTED
            and example.reviewed_at is not None
        ):
            raise InvalidKnowledgeState("已人工驳回或废弃的例题不能继续编辑。")

        changed_fields = [
            field
            for field, old, new in (
                ("problem", example.problem, request.problem),
                ("solution", example.solution, request.solution),
                ("tags", example.tags, request.tags),
                ("method_hint", example.method_hint, request.method_hint),
                ("math_payload", example.math_payload, request.math_payload),
            )
            if old != new
        ]
        if not changed_fields:
            return ExampleDraftUpdateResult(
                example=example,
                learned_methods=store.list_methods_for_example(example.id),
            )

        verification = self.verifier.verify(request.math_payload)
        create_request = ExampleCreate(
            problem=request.problem,
            solution=request.solution,
            tags=request.tags,
            method_hint=request.method_hint,
            math_payload=request.math_payload,
            reviewed=False,
        )
        extraction_result = self._extract_methods(
            create_request,
            verification.status,
        )
        if example.origin is ExampleOrigin.CONVERSATION:
            extraction_result = self._filter_conversation_extraction(extraction_result)

        now = utc_now()
        updated = example.model_copy(
            update={
                "problem": request.problem,
                "solution": request.solution,
                "tags": request.tags,
                "method_hint": request.method_hint,
                "problem_kind": classify_problem(request.problem),
                "math_payload": request.math_payload,
                "verification": verification,
                "extraction": extraction_result.trace,
                "method_drafts": [
                    draft.model_dump(mode="json") for draft in extraction_result.methods
                ],
                "status": (
                    KnowledgeStatus.REJECTED
                    if verification.status is VerificationStatus.REJECTED
                    else KnowledgeStatus.PENDING_REVIEW
                ),
                "reviewed": False,
                "reviewed_at": None,
                "reviewer_note": "",
                "revision": example.revision + 1,
                "updated_at": now,
            }
        )
        saved = store.replace_example_draft(
            updated,
            extraction_result.methods,
            changed_fields=changed_fields,
        )
        learned_methods: list[MethodCard] = []
        if verification.status is not VerificationStatus.REJECTED:
            for draft in extraction_result.methods:
                learned_methods.append(
                    store.upsert_method(
                        draft=draft,
                        example_id=example.id,
                        status=KnowledgeStatus.PENDING_REVIEW,
                        verified=False,
                    )
                )
        return ExampleDraftUpdateResult(
            example=saved,
            learned_methods=learned_methods,
        )

    def capture_solution_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
    ) -> ConversationCaptureResult:
        with self._knowledge_lock:
            return self._capture_solution_attempt(workspace_id, attempt_id)

    def _capture_solution_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
    ) -> ConversationCaptureResult:
        """Turn one persisted conversation into one idempotent knowledge draft."""

        store = self.workspaces.store(workspace_id)
        existing = store.get_example_by_source_attempt(attempt_id)
        if existing is not None:
            return ConversationCaptureResult(
                example=existing,
                learned_methods=store.list_methods_for_example(existing.id),
                created=False,
            )

        attempt = store.get_solution_attempt(attempt_id)
        candidate = attempt.candidate
        if candidate is None:
            raise InvalidKnowledgeState(
                "生成失败的求解记录没有候选解，不能转为知识草稿。"
            )

        math_payload: MathPayload | None = None
        if (
            attempt.math_target is not None
            and candidate.answer_kind is AnswerKind.EXPRESSION
            and candidate.answer_expression
        ):
            math_payload = MathPayload(
                **attempt.math_target.model_dump(),
                expected=candidate.answer_expression,
            )
        method_hint = ", ".join(candidate.used_method_keys) or None
        if method_hint is not None:
            method_hint = method_hint[:200]
        result = self._ingest_example(
            workspace_id,
            ExampleCreate(
                problem=attempt.problem,
                solution=self._candidate_solution_text(candidate),
                tags=attempt.tags,
                method_hint=method_hint,
                math_payload=math_payload,
                reviewed=False,
            ),
            origin=ExampleOrigin.CONVERSATION,
            source_attempt_id=attempt.id,
            verification_override=attempt.verification,
        )
        return ConversationCaptureResult(
            example=result.example,
            learned_methods=result.learned_methods,
            created=True,
        )

    def review_example(
        self,
        workspace_id: str,
        example_id: str,
        request: ExampleReviewRequest,
    ) -> ExampleReviewResult:
        with self._knowledge_lock:
            return self._review_example(workspace_id, example_id, request)

    def _review_example(
        self,
        workspace_id: str,
        example_id: str,
        request: ExampleReviewRequest,
    ) -> ExampleReviewResult:
        store = self.workspaces.store(workspace_id)
        example = store.get_example(example_id)
        if example.revision != request.expected_revision:
            raise InvalidKnowledgeState("知识草稿已被其他操作更新；请刷新并重新复核。")

        if request.decision is ExampleReviewDecision.REJECT:
            if example.status is KnowledgeStatus.PROMOTED:
                raise InvalidKnowledgeState(
                    "已晋级例题不能直接驳回；请单独废弃受影响的方法卡。"
                )
            if example.status is KnowledgeStatus.REJECTED:
                return ExampleReviewResult(example=example, learned_methods=[])
            rejected = store.reject_example(example.id, request.reviewer_note)
            return ExampleReviewResult(example=rejected, learned_methods=[])

        if example.status is KnowledgeStatus.PROMOTED and example.reviewed:
            return ExampleReviewResult(
                example=example,
                learned_methods=store.list_methods_for_example(example.id),
            )
        if example.status in {
            KnowledgeStatus.REJECTED,
            KnowledgeStatus.DEPRECATED,
        }:
            raise InvalidKnowledgeState("已拒绝或废弃的例题不能晋级。")
        if example.source_attempt_id is not None and example.revision == 1:
            source_attempt = store.get_solution_attempt(example.source_attempt_id)
            if source_attempt.status is not SolutionAttemptStatus.VERIFIED:
                raise InvalidKnowledgeState(
                    "来源求解记录未完整通过独立数学验证，不能晋级这条例题。"
                )

        fresh_verification = self._verification_for_promotion(example)
        if fresh_verification.status is not VerificationStatus.VERIFIED:
            raise InvalidKnowledgeState(
                "只有独立数学验证通过的例题才能晋级；请先补充可验证数学目标，"
                "或让检查流水线给出程序级证据。"
            )

        drafts = store.get_example_method_drafts(example.id)
        replacement_extraction: MethodExtractionTrace | None = None
        if (
            not drafts
            and example.extraction is not None
            and example.extraction.extracted_method_keys
        ):
            extraction_result = self._extract_methods(
                ExampleCreate(
                    problem=example.problem,
                    solution=example.solution,
                    tags=example.tags,
                    method_hint=example.method_hint,
                    math_payload=example.math_payload,
                    reviewed=True,
                ),
                fresh_verification.status,
            )
            drafts = extraction_result.methods
            replacement_extraction = extraction_result.trace

        features = self._signature_features(example.problem, example.math_payload)
        learned_methods = [
            store.upsert_method(
                draft=draft,
                example_id=example.id,
                status=KnowledgeStatus.PROMOTED,
                verified=True,
                features=features,
                idempotent_evidence=True,
                confidence=fresh_verification.conclusion,
            )
            for draft in drafts
        ]
        reviewed = store.complete_example_review(
            example.id,
            request.reviewer_note,
            expected_revision=request.expected_revision,
            extraction=replacement_extraction,
            method_drafts=drafts if replacement_extraction is not None else None,
        )
        return ExampleReviewResult(
            example=reviewed,
            learned_methods=learned_methods,
        )

    @staticmethod
    def _candidate_solution_text(candidate: CandidateSolution) -> str:
        parts = [candidate.answer_text.strip()]
        if candidate.steps:
            rendered_steps = []
            for index, step in enumerate(candidate.steps, start=1):
                line = f"{index}. {step.explanation.strip()}"
                if step.expression:
                    line += f"\n   {step.expression.strip()}"
                rendered_steps.append(line)
            parts.append("解题步骤：\n" + "\n".join(rendered_steps))
        if (
            candidate.answer_expression
            and candidate.answer_expression not in candidate.answer_text
        ):
            parts.append(f"最终表达式：{candidate.answer_expression}")
        return "\n\n".join(part for part in parts if part).strip()[:40_000]

    def list_methods(
        self,
        workspace_id: str,
        include_pending: bool = True,
        include_deprecated: bool = False,
    ) -> list[MethodCard]:
        return self.workspaces.store(workspace_id).list_methods(
            include_pending=include_pending,
            include_deprecated=include_deprecated,
        )

    def list_method_versions(
        self, workspace_id: str, method_id: str
    ) -> list[MethodVersion]:
        return self.workspaces.store(workspace_id).list_method_versions(method_id)

    def scan_merge_proposals(
        self, workspace_id: str, threshold: float | None = None
    ) -> list[MethodMergeProposal]:
        """扫描疑似重复的方法卡。只写提案，不动数据——合并需人工确认。

        显式触发而不是挂在摄取路径上：全量两两比较既慢又吵。
        """

        store = self.workspaces.store(workspace_id)
        methods = store.list_methods(include_pending=True, include_deprecated=False)
        return store.record_merge_proposals(
            find_merge_candidates(methods, threshold=threshold)
        )

    def list_merge_proposals(
        self, workspace_id: str, status: MergeProposalStatus | None = None
    ) -> list[MethodMergeProposal]:
        return self.workspaces.store(workspace_id).list_merge_proposals(status)

    def apply_merge_proposal(self, workspace_id: str, proposal_id: str) -> MethodCard:
        return self.workspaces.store(workspace_id).apply_merge_proposal(proposal_id)

    def reject_merge_proposal(
        self, workspace_id: str, proposal_id: str
    ) -> MethodMergeProposal:
        return self.workspaces.store(workspace_id).resolve_merge_proposal(
            proposal_id, MergeProposalStatus.REJECTED
        )

    def update_method_status(
        self,
        workspace_id: str,
        method_id: str,
        request: MethodStatusUpdate,
    ) -> MethodCard:
        if request.status is KnowledgeStatus.PROMOTED:
            raise InvalidKnowledgeState("方法卡只能通过已验证例题的人工复核晋级。")
        return self.workspaces.store(workspace_id).update_method_status(
            method_id, request.status
        )

    def list_learning_events(self, workspace_id: str) -> list[LearningEvent]:
        return self.workspaces.store(workspace_id).list_learning_events()

    def search_methods(
        self,
        workspace_id: str,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 5,
        math_target: SolveMathTarget | None = None,
    ) -> list[MethodMatch]:
        methods = self.workspaces.store(workspace_id).list_methods(
            include_pending=False
        )
        # 没有渐进目标时从提问文本抽结构。以前这里直接给 `None`——聊天路径上永远
        # 没有 `math_target`，于是结构检索一次都不会启动，只剩词面和标签。
        features = (
            extract_features(math_target) if math_target else features_from_text(query)
        )
        return self.retriever.search(
            methods,
            query,
            tags=tags,
            top_k=top_k,
            features=features if not features.is_empty else None,
            inferred_structure=math_target is None,
        )

    def build_solve_plan(
        self,
        workspace_id: str,
        problem: str,
        tags: list[str] | None = None,
        top_k: int = 5,
        math_target: SolveMathTarget | None = None,
    ) -> SolvePlan:
        matches = self.search_methods(
            workspace_id,
            problem,
            tags=tags,
            top_k=top_k,
            math_target=math_target,
        )
        return SolvePlan(
            workspace_id=workspace_id,
            problem=problem,
            problem_kind=classify_problem(problem),
            recommended_methods=matches,
            note=(
                "这是不执行生成与反馈的检索预览；使用 /solve 可生成候选解、"
                "执行数学验证并保存尝试记录。"
            ),
        )

    def solve_problem(
        self,
        workspace_id: str,
        request: SolveRequest,
    ) -> SolutionAttempt:
        attempt = self._build_solution_attempt(workspace_id, request)
        return self._persist_attempt_and_capture(workspace_id, attempt)

    def _persist_attempt_and_capture(
        self,
        workspace_id: str,
        attempt: SolutionAttempt,
    ) -> SolutionAttempt:
        store = self.workspaces.store(workspace_id)
        saved = store.add_solution_attempt(attempt)
        if saved.candidate is None:
            store.record_learning_event(
                "conversation_capture_skipped",
                saved.id,
                {"reason": "generation_failed"},
            )
            return saved
        try:
            capture = self.capture_solution_attempt(workspace_id, saved.id)
            store.record_learning_event(
                "conversation_capture_completed",
                saved.id,
                {
                    "example_id": capture.example.id,
                    "created": capture.created,
                    "status": capture.example.status.value,
                },
            )
        except Exception as exc:  # noqa: BLE001
            # A knowledge-extraction failure must not make a completed solve look
            # lost. The explicit /capture endpoint can safely retry this attempt.
            store.record_learning_event(
                "conversation_capture_failed",
                saved.id,
                {"error": f"{exc.__class__.__name__}: {exc}"[:2_000]},
            )
        return saved

    def _build_solution_attempt(
        self,
        workspace_id: str,
        request: SolveRequest,
        *,
        correction_of: str | None = None,
        generation_result: SolutionGenerationResult | None = None,
        conversation_context: dict[str, object] | None = None,
    ) -> SolutionAttempt:
        allow_automatic_recovery = generation_result is None
        matches = self.search_methods(
            workspace_id,
            request.problem,
            tags=request.tags,
            top_k=request.top_k,
            math_target=request.math_target,
        )
        if generation_result is None:
            generation_result = self._generate_candidate(
                request,
                matches,
                conversation_context=conversation_context,
            )
        if allow_automatic_recovery:
            generation_result = self._prepare_generation_result(
                generation_result,
                matches,
                request.math_target,
            )
        else:
            generation_result = self._filter_unretrieved_method_keys(
                generation_result,
                matches,
            )
        verification, status = self._verify_candidate(request, generation_result)
        initial_stage = (
            GenerationStageKind.INITIAL
            if allow_automatic_recovery
            else GenerationStageKind.HUMAN
        )
        generation_result = self._record_initial_verification(
            generation_result,
            initial_stage,
            verification,
        )
        if allow_automatic_recovery:
            generation_result, verification, status = self._recover_after_verification(
                request,
                matches,
                generation_result,
                verification,
                status,
            )
        generation_result = self._canonicalize_verified_answer(
            generation_result,
            status,
        )
        feedback_method_keys = (
            generation_result.candidate.used_method_keys
            if generation_result.candidate is not None
            and generation_result.trace.method_feedback_eligible
            else []
        )
        return SolutionAttempt(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            problem=request.problem,
            tags=request.tags,
            problem_kind=classify_problem(request.problem),
            math_target=request.math_target,
            recommended_methods=matches,
            candidate=generation_result.candidate,
            generation=generation_result.trace,
            verification=verification,
            status=status,
            feedback_method_keys=feedback_method_keys,
            correction_of=correction_of,
            created_at=utc_now(),
        )

    def _generate_candidate(
        self,
        request: SolveRequest,
        matches: list[MethodMatch],
        *,
        conversation_context: dict[str, object] | None = None,
    ) -> SolutionGenerationResult:
        try:
            contextual_generate = getattr(
                self.generator,
                "generate_with_context",
                None,
            )
            if conversation_context is not None and callable(contextual_generate):
                return contextual_generate(
                    request.problem,
                    matches,
                    request.math_target,
                    request.max_output_tokens,
                    conversation_context,
                )
            return self.generator.generate(
                request.problem,
                matches,
                request.math_target,
                request.max_output_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            return SolutionGenerationResult(
                candidate=None,
                trace=SolutionGenerationTrace(
                    provider=getattr(
                        self.generator,
                        "name",
                        self.generator.__class__.__name__,
                    ),
                    model=getattr(self.generator, "model", None),
                    prompt_version=getattr(
                        self.generator,
                        "prompt_version",
                        "unknown",
                    ),
                    status=GenerationStatus.ERROR,
                    error=f"{exc.__class__.__name__}: {exc}"[:2_000],
                ),
            )

    def _prepare_generation_result(
        self,
        result: SolutionGenerationResult,
        matches: list[MethodMatch],
        math_target: SolveMathTarget | None,
    ) -> SolutionGenerationResult:
        result = self._filter_unretrieved_method_keys(result, matches)
        if result.candidate is None:
            return result

        normalized = self.normalizer.normalize(result.candidate, math_target)
        if not normalized.actions and normalized.candidate == result.candidate:
            return result
        actions = self._unique_strings(
            [*result.trace.normalization_actions, *normalized.actions]
        )
        return result.model_copy(
            update={
                "candidate": normalized.candidate,
                "trace": result.trace.model_copy(
                    update={"normalization_actions": actions}
                ),
            }
        )

    @staticmethod
    def _stage_from_result(
        stage: GenerationStageKind,
        result: SolutionGenerationResult,
        verification: VerificationReport,
    ) -> SolutionGenerationStage:
        trace = result.trace
        return SolutionGenerationStage(
            stage=stage,
            provider=trace.provider,
            model=trace.model,
            response_id=trace.response_id,
            prompt_version=trace.prompt_version,
            status=trace.status,
            candidate=result.candidate,
            verification=verification,
            raw_output=trace.raw_output,
            error=trace.error,
            duration_ms=trace.duration_ms,
        )

    def _record_initial_verification(
        self,
        result: SolutionGenerationResult,
        stage: GenerationStageKind,
        verification: VerificationReport,
    ) -> SolutionGenerationResult:
        stages = list(result.trace.stages)
        if stages:
            stages[-1] = stages[-1].model_copy(update={"verification": verification})
        else:
            stages.append(self._stage_from_result(stage, result, verification))
        return result.model_copy(
            update={
                "trace": result.trace.model_copy(
                    update={"stages": stages},
                )
            }
        )

    @staticmethod
    def _canonicalize_verified_answer(
        result: SolutionGenerationResult,
        status: SolutionAttemptStatus,
    ) -> SolutionGenerationResult:
        candidate = result.candidate
        if (
            status is not SolutionAttemptStatus.VERIFIED
            or candidate is None
            or candidate.answer_expression is None
        ):
            return result
        canonical_text = f"已验证答案：{candidate.answer_expression}"
        if candidate.answer_text == canonical_text:
            return result
        notes = MathHarnessService._unique_strings(
            [*result.trace.recovery_notes, "canonicalized_verified_answer_text"]
        )
        return result.model_copy(
            update={
                "candidate": candidate.model_copy(
                    update={"answer_text": canonical_text},
                ),
                "trace": result.trace.model_copy(
                    update={"recovery_notes": notes},
                ),
            }
        )

    def _recover_after_verification(
        self,
        request: SolveRequest,
        matches: list[MethodMatch],
        initial_result: SolutionGenerationResult,
        initial_verification: VerificationReport,
        initial_status: SolutionAttemptStatus,
    ) -> tuple[
        SolutionGenerationResult,
        VerificationReport,
        SolutionAttemptStatus,
    ]:
        if (
            request.math_target is None
            or initial_status is SolutionAttemptStatus.VERIFIED
            or initial_result.candidate is None
            or initial_result.candidate.answer_kind is not AnswerKind.EXPRESSION
            or initial_result.trace.fallback_used
        ):
            return initial_result, initial_verification, initial_status

        total_duration = initial_result.trace.duration_ms
        stages = list(initial_result.trace.stages)
        recovery_notes = [
            self._verification_note("initial_verification", initial_verification)
        ]
        normalization_actions = list(initial_result.trace.normalization_actions)
        final_result = initial_result
        final_verification = initial_verification
        final_status = initial_status
        correction_attempted = False
        correction_error: str | None = None

        repair = getattr(self.generator, "repair_after_verification", None)
        if callable(repair):
            corrected = repair(
                request.problem,
                matches,
                request.math_target,
                initial_result.candidate,
                initial_verification,
                request.max_output_tokens,
            )
            if corrected is not None:
                correction_attempted = True
                total_duration += corrected.trace.duration_ms
                corrected = self._prepare_generation_result(
                    corrected,
                    matches,
                    request.math_target,
                )
                normalization_actions.extend(corrected.trace.normalization_actions)
                recovery_notes.extend(corrected.trace.recovery_notes)
                correction_error = corrected.trace.error
                if corrected.candidate is not None:
                    corrected_verification, corrected_status = self._verify_candidate(
                        request,
                        corrected,
                    )
                    stages.append(
                        self._stage_from_result(
                            GenerationStageKind.CORRECTION,
                            corrected,
                            corrected_verification,
                        )
                    )
                    recovery_notes.append(
                        self._verification_note(
                            "correction_verification",
                            corrected_verification,
                        )
                    )
                    final_result = corrected
                    final_verification = corrected_verification
                    final_status = corrected_status
                    if corrected.candidate.answer_kind is not AnswerKind.EXPRESSION:
                        recovery_notes.append(
                            "model_correction_returned_non_expression_claim"
                        )
                        final_result = corrected.model_copy(
                            update={
                                "trace": corrected.trace.model_copy(
                                    update={
                                        "correction_attempted": True,
                                        "correction_succeeded": False,
                                        "normalization_actions": self._unique_strings(
                                            normalization_actions
                                        ),
                                        "recovery_notes": self._unique_strings(
                                            recovery_notes
                                        ),
                                        "stages": stages,
                                        "duration_ms": total_duration,
                                    }
                                )
                            }
                        )
                        return (
                            final_result,
                            final_verification,
                            final_status,
                        )
                    if corrected_status is SolutionAttemptStatus.VERIFIED:
                        final_result = corrected.model_copy(
                            update={
                                "trace": corrected.trace.model_copy(
                                    update={
                                        "correction_attempted": True,
                                        "correction_succeeded": True,
                                        "normalization_actions": self._unique_strings(
                                            normalization_actions
                                        ),
                                        "recovery_notes": self._unique_strings(
                                            recovery_notes
                                        ),
                                        "stages": stages,
                                        "duration_ms": total_duration,
                                    }
                                )
                            }
                        )
                        return (
                            final_result,
                            final_verification,
                            final_status,
                        )
                else:
                    corrected_verification, _ = self._verify_candidate(
                        request,
                        corrected,
                    )
                    stages.append(
                        self._stage_from_result(
                            GenerationStageKind.CORRECTION,
                            corrected,
                            corrected_verification,
                        )
                    )
                    recovery_notes.append("model_correction_returned_no_candidate")

        fallback = getattr(self.generator, "fallback_after_verification", None)
        if callable(fallback):
            fallback_result = fallback(
                request.problem,
                matches,
                request.math_target,
                request.max_output_tokens,
                final_verification,
                correction_attempted=correction_attempted,
                correction_error=correction_error,
            )
            if fallback_result is not None:
                total_duration += fallback_result.trace.duration_ms
                fallback_result = self._prepare_generation_result(
                    fallback_result,
                    matches,
                    request.math_target,
                )
                normalization_actions.extend(
                    fallback_result.trace.normalization_actions
                )
                recovery_notes.extend(fallback_result.trace.recovery_notes)
                if fallback_result.candidate is not None:
                    fallback_verification, fallback_status = self._verify_candidate(
                        request,
                        fallback_result,
                    )
                    stages.append(
                        self._stage_from_result(
                            GenerationStageKind.FALLBACK,
                            fallback_result,
                            fallback_verification,
                        )
                    )
                    recovery_notes.append(
                        self._verification_note(
                            "fallback_verification",
                            fallback_verification,
                        )
                    )
                    final_result = fallback_result
                    final_verification = fallback_verification
                    final_status = fallback_status
                elif fallback_result.trace.error:
                    fallback_verification, _ = self._verify_candidate(
                        request,
                        fallback_result,
                    )
                    stages.append(
                        self._stage_from_result(
                            GenerationStageKind.FALLBACK,
                            fallback_result,
                            fallback_verification,
                        )
                    )
                    recovery_notes.append(
                        "verification_fallback_failed: "
                        + fallback_result.trace.error[:500]
                    )

        final_result = final_result.model_copy(
            update={
                "trace": final_result.trace.model_copy(
                    update={
                        "correction_attempted": correction_attempted,
                        "correction_succeeded": False,
                        "normalization_actions": self._unique_strings(
                            normalization_actions
                        ),
                        "recovery_notes": self._unique_strings(recovery_notes),
                        "stages": stages,
                        "duration_ms": total_duration,
                    }
                )
            }
        )
        return final_result, final_verification, final_status

    @staticmethod
    def _verification_note(
        stage: str,
        verification: VerificationReport,
    ) -> str:
        return (f"{stage}={verification.status.value}: {verification.summary}")[:1_000]

    @staticmethod
    def _unique_strings(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _filter_unretrieved_method_keys(
        result: SolutionGenerationResult,
        matches: list[MethodMatch],
    ) -> SolutionGenerationResult:
        if result.candidate is None:
            return result
        allowed_keys = {match.method.key for match in matches}
        filtered = [
            key for key in result.candidate.used_method_keys if key in allowed_keys
        ]
        if filtered == result.candidate.used_method_keys:
            return result
        return result.model_copy(
            update={
                "candidate": result.candidate.model_copy(
                    update={"used_method_keys": filtered}
                )
            }
        )

    def _verify_candidate(
        self,
        request: SolveRequest,
        generation_result: SolutionGenerationResult,
    ) -> tuple[VerificationReport, SolutionAttemptStatus]:
        candidate = generation_result.candidate
        if candidate is None:
            return (
                VerificationReport(
                    status=VerificationStatus.NEEDS_REVIEW,
                    summary="候选解生成失败，未进入自动验收。",
                    checks=["candidate_generation_failed"],
                    error=generation_result.trace.error,
                ),
                SolutionAttemptStatus.GENERATION_FAILED,
            )
        if candidate.answer_kind is not AnswerKind.EXPRESSION:
            verification = VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary=(
                    "候选解声明的是不存在、无极限或条件性结论；"
                    "当前版本保留该结论并交由复核，不自动改写。"
                ),
                checks=[
                    "candidate_generated",
                    f"answer_kind:{candidate.answer_kind.value}",
                    "non_expression_claim_requires_review",
                ],
            )
        elif request.math_target is None:
            verification = VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary="候选解已保存；缺少 math_target，无法自动验收。",
                checks=["candidate_generated", "missing_math_target"],
            )
        elif candidate.answer_expression is None:
            verification = VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary="候选解已保存；未提供机器可检查的答案表达式。",
                checks=["candidate_generated", "missing_answer_expression"],
            )
        else:
            verification = self.verifier.verify(
                MathPayload(
                    **request.math_target.model_dump(),
                    expected=candidate.answer_expression,
                )
            )
            if verification.status is VerificationStatus.VERIFIED:
                verification = self._verify_final_step_consistency(
                    request.math_target,
                    candidate,
                    verification,
                )

        status_map = {
            VerificationStatus.VERIFIED: SolutionAttemptStatus.VERIFIED,
            VerificationStatus.NEEDS_REVIEW: SolutionAttemptStatus.NEEDS_REVIEW,
            VerificationStatus.REJECTED: SolutionAttemptStatus.REJECTED,
        }
        return verification, status_map[verification.status]

    def _verify_final_step_consistency(
        self,
        math_target: SolveMathTarget,
        candidate: CandidateSolution,
        mathematical_verification: VerificationReport,
    ) -> VerificationReport:
        final_step_expression = next(
            (
                step.expression
                for step in reversed(candidate.steps)
                if step.expression is not None
            ),
            None,
        )
        if final_step_expression is None or candidate.answer_expression is None:
            return VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary=(
                    "答案表达式通过数学验证，但最终步骤没有可检查表达式，需要复核。"
                ),
                checks=[
                    *mathematical_verification.checks,
                    "candidate_final_step_missing",
                ],
                computed=mathematical_verification.computed,
            )

        step_candidate = candidate.model_copy(
            update={
                "answer_kind": AnswerKind.EXPRESSION,
                "answer_expression": final_step_expression,
            }
        )
        normalized_step = self.normalizer.normalize(
            step_candidate,
            math_target,
        ).candidate.answer_expression
        if normalized_step is None:
            return VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary="最终步骤表达式无法规范化，需要复核。",
                checks=[
                    *mathematical_verification.checks,
                    "candidate_final_step_missing",
                ],
                computed=mathematical_verification.computed,
            )

        payload = math_target.model_dump()
        payload.update(
            {
                "expression": candidate.answer_expression,
                "expected": normalized_step,
                "mode": VerificationMode.EXACT_EQUIVALENCE,
                "remainder_power": None,
            }
        )
        consistency = self.verifier.verify(MathPayload(**payload))
        if consistency.status is VerificationStatus.VERIFIED:
            return mathematical_verification.model_copy(
                update={
                    "summary": (
                        mathematical_verification.summary + " 候选答案与最终步骤一致。"
                    ),
                    "checks": self._unique_strings(
                        [
                            *mathematical_verification.checks,
                            "candidate_final_step_consistency",
                        ]
                    ),
                }
            )

        return VerificationReport(
            status=consistency.status,
            summary=(
                "数学答案表达式本身可通过验证，但与候选解的最终步骤不一致。"
                if consistency.status is VerificationStatus.REJECTED
                else "数学答案表达式本身可通过验证，但最终步骤一致性无法判定。"
            ),
            checks=self._unique_strings(
                [
                    *mathematical_verification.checks,
                    "candidate_final_step_consistency",
                    *consistency.checks,
                ]
            ),
            computed={
                **mathematical_verification.computed,
                **{
                    f"final_step_{key}": value
                    for key, value in consistency.computed.items()
                },
            },
            error=consistency.error,
        )

    def get_solution_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
    ) -> SolutionAttempt:
        return self.workspaces.store(workspace_id).get_solution_attempt(attempt_id)

    def list_solution_attempts(
        self,
        workspace_id: str,
    ) -> list[SolutionAttempt]:
        return self.workspaces.store(workspace_id).list_solution_attempts()

    def correct_solution_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
        request: SolutionCorrection,
    ) -> SolutionAttempt:
        original = self.get_solution_attempt(workspace_id, attempt_id)
        candidate = CandidateSolution(
            answer_kind=request.answer_kind,
            answer_text=request.answer_text,
            answer_expression=request.answer_expression,
            steps=request.steps
            or [
                CandidateStep(
                    explanation="人工复核后提交纠正答案。",
                    expression=request.answer_expression,
                )
            ],
            used_method_keys=(
                request.used_method_keys if request.used_method_keys is not None else []
            ),
            assumptions=request.assumptions,
            confidence=1,
        )
        generation_result = SolutionGenerationResult(
            candidate=candidate,
            trace=SolutionGenerationTrace(
                provider="human",
                prompt_version="human-correction-v1",
                status=GenerationStatus.SUCCESS,
                method_feedback_eligible=bool(request.used_method_keys),
                raw_output=json.dumps(
                    {
                        "candidate": candidate.model_dump(mode="json"),
                        "reviewer_note": request.reviewer_note,
                    },
                    ensure_ascii=False,
                )[:8_000],
            ),
        )
        solve_request = SolveRequest(
            problem=original.problem,
            tags=original.tags,
            top_k=max(1, len(original.recommended_methods)),
            math_target=original.math_target,
        )
        corrected = self._build_solution_attempt(
            workspace_id,
            solve_request,
            correction_of=original.id,
            generation_result=generation_result,
        )
        return self._persist_attempt_and_capture(workspace_id, corrected)

    def evaluate_workspace(
        self, workspace_id: str, request: EvaluationRequest
    ) -> EvaluationRun:
        case_results: list[EvaluationCaseResult] = []
        zero_results = 0
        for case in request.cases:
            matches = self.search_methods(
                workspace_id,
                case.problem,
                tags=case.tags,
                top_k=request.top_k,
                math_target=case.math_target,
            )
            returned = [match.method.key for match in matches]
            expected = set(case.expected_method_keys)
            if not returned:
                zero_results += 1
            first_relevant_rank = next(
                (
                    index
                    for index, method_key in enumerate(returned, start=1)
                    if method_key in expected
                ),
                None,
            )
            case_results.append(
                EvaluationCaseResult(
                    case_id=case.id,
                    expected_method_keys=case.expected_method_keys,
                    returned_method_keys=returned,
                    hit_at_1=bool(returned and returned[0] in expected),
                    recall_at_k=len(expected & set(returned)) / len(expected),
                    reciprocal_rank=(
                        1 / first_relevant_rank if first_relevant_rank else 0
                    ),
                )
            )

        count = len(case_results)
        run = EvaluationRun(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            name=request.name,
            top_k=request.top_k,
            metrics=EvaluationMetrics(
                case_count=count,
                hit_at_1=round(
                    sum(result.hit_at_1 for result in case_results) / count, 6
                ),
                recall_at_k=round(
                    sum(result.recall_at_k for result in case_results) / count, 6
                ),
                mean_reciprocal_rank=round(
                    sum(result.reciprocal_rank for result in case_results) / count,
                    6,
                ),
                zero_result_rate=round(zero_results / count, 6),
            ),
            cases=case_results,
            created_at=utc_now(),
        )
        return self.workspaces.store(workspace_id).add_evaluation(run)

    def list_evaluations(self, workspace_id: str) -> list[EvaluationRun]:
        return self.workspaces.store(workspace_id).list_evaluations()

    def evaluate_solver(
        self,
        workspace_id: str,
        request: SolveEvaluationRequest,
    ) -> SolveEvaluationRun:
        case_results: list[SolveEvaluationCaseResult] = []
        for case in request.cases:
            attempt = self._build_solution_attempt(
                workspace_id,
                SolveRequest(
                    problem=case.problem,
                    tags=case.tags,
                    top_k=request.top_k,
                    math_target=case.math_target,
                    max_output_tokens=request.max_output_tokens,
                ),
            )
            case_results.append(
                SolveEvaluationCaseResult(
                    case_id=case.id,
                    status=attempt.status,
                    verification_status=attempt.verification.status,
                    retrieved_method_keys=[
                        match.method.key for match in attempt.recommended_methods
                    ],
                    used_method_keys=(
                        attempt.candidate.used_method_keys if attempt.candidate else []
                    ),
                    feedback_method_keys=attempt.feedback_method_keys,
                    generation_provider=attempt.generation.provider,
                    fallback_used=attempt.generation.fallback_used,
                    correction_attempted=attempt.generation.correction_attempted,
                    correction_succeeded=attempt.generation.correction_succeeded,
                )
            )

        count = len(case_results)
        run = SolveEvaluationRun(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            name=request.name,
            top_k=request.top_k,
            metrics=SolveEvaluationMetrics(
                case_count=count,
                verified_rate=self._status_rate(
                    case_results,
                    SolutionAttemptStatus.VERIFIED,
                ),
                needs_review_rate=self._status_rate(
                    case_results,
                    SolutionAttemptStatus.NEEDS_REVIEW,
                ),
                rejected_rate=self._status_rate(
                    case_results,
                    SolutionAttemptStatus.REJECTED,
                ),
                generation_failure_rate=self._status_rate(
                    case_results,
                    SolutionAttemptStatus.GENERATION_FAILED,
                ),
                fallback_rate=round(
                    sum(case.fallback_used for case in case_results) / count,
                    6,
                ),
                correction_attempt_rate=round(
                    sum(case.correction_attempted for case in case_results) / count,
                    6,
                ),
                correction_success_rate=round(
                    sum(case.correction_succeeded for case in case_results) / count,
                    6,
                ),
            ),
            cases=case_results,
            created_at=utc_now(),
        )
        return self.workspaces.store(workspace_id).add_solve_evaluation(run)

    @staticmethod
    def _status_rate(
        cases: list[SolveEvaluationCaseResult],
        status: SolutionAttemptStatus,
    ) -> float:
        return round(sum(case.status is status for case in cases) / len(cases), 6)

    def list_solve_evaluations(
        self,
        workspace_id: str,
    ) -> list[SolveEvaluationRun]:
        return self.workspaces.store(workspace_id).list_solve_evaluations()
