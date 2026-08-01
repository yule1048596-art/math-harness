from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from math_harness.dedup import MergeCandidate, card_to_draft
from math_harness.errors import RecordNotFound, WorkspaceNotFound
from math_harness.merging import merge_method_content
from math_harness.models import (
    EvaluationRun,
    KnowledgeStatus,
    LearningEvent,
    MergeProposalStatus,
    MethodCard,
    MethodDraft,
    MethodMergeProposal,
    MethodVersion,
    ProblemExample,
    SolutionAttempt,
    SolutionAttemptStatus,
    SolveEvaluationRun,
    Workspace,
    WorkspaceCreate,
    utc_now,
)
from math_harness.structure import MethodSignature, StructuralFeatures


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class WorkspaceManager:
    """Registry plus one physical SQLite database per workspace."""

    def __init__(self, data_root: Path | str) -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        self.workspaces_root = self.data_root / "workspaces"
        self.registry_path = self.data_root / "registry.sqlite3"
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        self._initialize_registry()

    @contextmanager
    def _registry_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.registry_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize_registry(self) -> None:
        with self._registry_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def create(self, request: WorkspaceCreate) -> Workspace:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name=request.name,
            description=request.description,
            created_at=utc_now(),
        )
        with self._registry_connection() as connection:
            connection.execute(
                "INSERT INTO workspaces (id, name, description, created_at) VALUES (?, ?, ?, ?)",
                (
                    workspace.id,
                    workspace.name,
                    workspace.description,
                    workspace.created_at.isoformat(),
                ),
            )
        WorkspaceStore(self.database_path(workspace.id), workspace.id)
        return workspace

    def get(self, workspace_id: str) -> Workspace:
        with self._registry_connection() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
        if row is None:
            raise WorkspaceNotFound(f"workspace not found: {workspace_id}")
        return Workspace(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list(self) -> list[Workspace]:
        with self._registry_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM workspaces ORDER BY created_at, id"
            ).fetchall()
        return [
            Workspace(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def database_path(self, workspace_id: str) -> Path:
        try:
            uuid.UUID(workspace_id)
        except ValueError as exc:
            raise WorkspaceNotFound(f"invalid workspace id: {workspace_id}") from exc
        return self.workspaces_root / workspace_id / "workspace.sqlite3"

    def store(self, workspace_id: str) -> WorkspaceStore:
        self.get(workspace_id)
        return WorkspaceStore(self.database_path(workspace_id), workspace_id)


class WorkspaceStore:
    def __init__(self, database_path: Path, workspace_id: str) -> None:
        self.database_path = database_path
        self.workspace_id = workspace_id
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS examples (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    problem TEXT NOT NULL,
                    solution TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    method_hint TEXT,
                    reviewed INTEGER NOT NULL DEFAULT 0,
                    problem_kind TEXT NOT NULL,
                    math_payload_json TEXT,
                    verification_json TEXT NOT NULL,
                    extraction_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK (workspace_id <> '')
                );

                CREATE INDEX IF NOT EXISTS idx_examples_workspace
                    ON examples(workspace_id, created_at);

                CREATE TABLE IF NOT EXISTS methods (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    method_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    applicable_json TEXT NOT NULL,
                    procedure_json TEXT NOT NULL,
                    failure_modes_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    success_count INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL,
                    signature_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, method_key),
                    CHECK (workspace_id <> '')
                );

                CREATE TABLE IF NOT EXISTS method_versions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    method_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content_json TEXT NOT NULL,
                    source_example_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, method_id, version),
                    FOREIGN KEY (method_id) REFERENCES methods(id) ON DELETE CASCADE,
                    CHECK (workspace_id <> '')
                );

                CREATE INDEX IF NOT EXISTS idx_method_versions_method
                    ON method_versions(workspace_id, method_id, version);

                CREATE TABLE IF NOT EXISTS method_merge_proposals (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    primary_method_id TEXT NOT NULL,
                    duplicate_method_id TEXT NOT NULL,
                    score REAL NOT NULL,
                    detail_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    UNIQUE(workspace_id, primary_method_id, duplicate_method_id),
                    FOREIGN KEY (primary_method_id)
                        REFERENCES methods(id) ON DELETE CASCADE,
                    FOREIGN KEY (duplicate_method_id)
                        REFERENCES methods(id) ON DELETE CASCADE,
                    CHECK (workspace_id <> '')
                );

                CREATE TABLE IF NOT EXISTS method_examples (
                    workspace_id TEXT NOT NULL,
                    method_id TEXT NOT NULL,
                    example_id TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, method_id, example_id),
                    FOREIGN KEY (method_id) REFERENCES methods(id) ON DELETE CASCADE,
                    FOREIGN KEY (example_id) REFERENCES examples(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS learning_events (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_evaluation_runs_workspace
                    ON evaluation_runs(workspace_id, created_at);

                CREATE TABLE IF NOT EXISTS solution_attempts (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    correction_of TEXT,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (correction_of)
                        REFERENCES solution_attempts(id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_solution_attempts_workspace
                    ON solution_attempts(workspace_id, created_at);

                CREATE TABLE IF NOT EXISTS attempt_methods (
                    workspace_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    method_id TEXT NOT NULL,
                    method_key TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    score REAL NOT NULL,
                    used INTEGER NOT NULL,
                    PRIMARY KEY (workspace_id, attempt_id, method_id),
                    FOREIGN KEY (attempt_id)
                        REFERENCES solution_attempts(id) ON DELETE CASCADE,
                    FOREIGN KEY (method_id) REFERENCES methods(id) ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS solve_evaluation_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_solve_evaluation_runs_workspace
                    ON solve_evaluation_runs(workspace_id, created_at);
                """
            )
            self._ensure_column(connection, "examples", "extraction_json", "TEXT")
            self._ensure_column(
                connection,
                "examples",
                "reviewed",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                connection,
                "methods",
                "signature_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def add_example(self, example: ProblemExample) -> ProblemExample:
        self._assert_workspace(example.workspace_id)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO examples (
                    id, workspace_id, problem, solution, tags_json, method_hint,
                    reviewed, problem_kind, math_payload_json, verification_json,
                    extraction_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    example.id,
                    example.workspace_id,
                    example.problem,
                    example.solution,
                    _dump(example.tags),
                    example.method_hint,
                    1 if example.reviewed else 0,
                    example.problem_kind.value,
                    (
                        _dump(example.math_payload.model_dump(mode="json"))
                        if example.math_payload
                        else None
                    ),
                    _dump(example.verification.model_dump(mode="json")),
                    (
                        _dump(example.extraction.model_dump(mode="json"))
                        if example.extraction
                        else None
                    ),
                    example.status.value,
                    example.created_at.isoformat(),
                ),
            )
            self._record_event(
                connection,
                "example_captured",
                example.id,
                {
                    "status": example.status.value,
                    "reviewed": example.reviewed,
                },
            )
        return example

    def get_example(self, example_id: str) -> ProblemExample:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM examples WHERE id = ? AND workspace_id = ?",
                (example_id, self.workspace_id),
            ).fetchone()
        if row is None:
            raise RecordNotFound(f"example not found in workspace: {example_id}")
        return self._row_to_example(row)

    def list_examples(self) -> list[ProblemExample]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM examples WHERE workspace_id = ? ORDER BY created_at, id",
                (self.workspace_id,),
            ).fetchall()
        return [self._row_to_example(row) for row in rows]

    def upsert_method(
        self,
        draft: MethodDraft,
        example_id: str,
        status: KnowledgeStatus,
        verified: bool,
        features: StructuralFeatures | None = None,
    ) -> MethodCard:
        now = utc_now()
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM methods WHERE workspace_id = ? AND method_key = ?",
                (self.workspace_id, draft.key),
            ).fetchone()
            previous_signature = (
                MethodSignature.model_validate_json(existing["signature_json"] or "{}")
                if existing is not None
                else MethodSignature()
            )
            signature = (
                previous_signature.accumulate(features)
                if features is not None
                else previous_signature
            )
            if existing is None:
                method_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO methods (
                        id, workspace_id, method_key, name, goal, applicable_json,
                        procedure_json, failure_modes_json, tags_json, status, version,
                        success_count, failure_count, signature_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        method_id,
                        self.workspace_id,
                        draft.key,
                        draft.name,
                        draft.goal,
                        _dump(draft.applicable_when),
                        _dump(draft.procedure),
                        _dump(draft.failure_modes),
                        _dump(draft.tags),
                        status.value,
                        1,
                        1 if verified else 0,
                        0,
                        signature.model_dump_json(),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                event_type = "method_created"
                event_extra: dict[str, object] = {}
            else:
                method_id = existing["id"]
                next_status = (
                    KnowledgeStatus.PROMOTED.value
                    if verified or existing["status"] == KnowledgeStatus.PROMOTED.value
                    else existing["status"]
                )
                current = self._row_to_method(existing, [])
                merged = merge_method_content(current, draft)
                if merged.changed:
                    # 先把改写前的内容按当前版本号存档，再更新。原始理解不被覆盖，
                    # 与「人工纠正以新记录保存」的审计风格一致。
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO method_versions (
                            id, workspace_id, method_id, version, content_json,
                            source_example_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            self.workspace_id,
                            method_id,
                            existing["version"],
                            _dump(
                                {
                                    "name": current.name,
                                    "goal": current.goal,
                                    "applicable_when": current.applicable_when,
                                    "procedure": current.procedure,
                                    "failure_modes": current.failure_modes,
                                    "tags": current.tags,
                                }
                            ),
                            example_id,
                            now.isoformat(),
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE methods
                        SET status = ?, version = version + 1,
                            success_count = success_count + ?,
                            name = ?, goal = ?, applicable_json = ?,
                            procedure_json = ?, failure_modes_json = ?, tags_json = ?,
                            signature_json = ?, updated_at = ?
                        WHERE id = ? AND workspace_id = ?
                        """,
                        (
                            next_status,
                            1 if verified else 0,
                            merged.name,
                            merged.goal,
                            _dump(merged.applicable_when),
                            _dump(merged.procedure),
                            _dump(merged.failure_modes),
                            _dump(merged.tags),
                            signature.model_dump_json(),
                            now.isoformat(),
                            method_id,
                            self.workspace_id,
                        ),
                    )
                    event_type = "method_content_evolved"
                    event_extra = {"added": merged.added}
                else:
                    connection.execute(
                        """
                        UPDATE methods
                        SET status = ?, version = version + 1,
                            success_count = success_count + ?,
                            signature_json = ?, updated_at = ?
                        WHERE id = ? AND workspace_id = ?
                        """,
                        (
                            next_status,
                            1 if verified else 0,
                            signature.model_dump_json(),
                            now.isoformat(),
                            method_id,
                            self.workspace_id,
                        ),
                    )
                    event_type = "method_updated"
                    event_extra = {}

            connection.execute(
                """
                INSERT OR IGNORE INTO method_examples
                    (workspace_id, method_id, example_id)
                VALUES (?, ?, ?)
                """,
                (self.workspace_id, method_id, example_id),
            )
            self._record_event(
                connection,
                event_type,
                method_id,
                {"example_id": example_id, "status": status.value, **event_extra},
            )

        return self.get_method(method_id)

    def update_method_status(
        self, method_id: str, status: KnowledgeStatus
    ) -> MethodCard:
        now = utc_now()
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT status FROM methods WHERE id = ? AND workspace_id = ?",
                (method_id, self.workspace_id),
            ).fetchone()
            if existing is None:
                raise RecordNotFound(f"method not found in workspace: {method_id}")
            connection.execute(
                """
                UPDATE methods
                SET status = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (status.value, now.isoformat(), method_id, self.workspace_id),
            )
            self._record_event(
                connection,
                "method_status_changed",
                method_id,
                {"from": existing["status"], "to": status.value},
            )
        return self.get_method(method_id)

    def get_method(self, method_id: str) -> MethodCard:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM methods WHERE id = ? AND workspace_id = ?",
                (method_id, self.workspace_id),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"method not found in workspace: {method_id}")
            example_rows = connection.execute(
                """
                SELECT example_id FROM method_examples
                WHERE workspace_id = ? AND method_id = ?
                ORDER BY example_id
                """,
                (self.workspace_id, method_id),
            ).fetchall()
        return self._row_to_method(row, [item["example_id"] for item in example_rows])

    def list_method_versions(self, method_id: str) -> list[MethodVersion]:
        """按版本升序返回该方法卡被改写前的历史快照。"""

        with self.connection() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM methods WHERE id = ? AND workspace_id = ?",
                    (method_id, self.workspace_id),
                ).fetchone()
                is None
            ):
                raise RecordNotFound(f"method not found in workspace: {method_id}")
            rows = connection.execute(
                """
                SELECT * FROM method_versions
                WHERE workspace_id = ? AND method_id = ?
                ORDER BY version
                """,
                (self.workspace_id, method_id),
            ).fetchall()
        return [
            MethodVersion(
                method_id=row["method_id"],
                workspace_id=row["workspace_id"],
                version=row["version"],
                source_example_id=row["source_example_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                **json.loads(row["content_json"]),
            )
            for row in rows
        ]

    def record_merge_proposals(
        self, candidates: list[MergeCandidate]
    ) -> list[MethodMergeProposal]:
        """写入疑似重复对。已存在的同一对保持原状，不覆盖人工已处理的结论。"""

        now = utc_now()
        with self.connection() as connection:
            for candidate in candidates:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO method_merge_proposals (
                        id, workspace_id, primary_method_id, duplicate_method_id,
                        score, detail_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        self.workspace_id,
                        candidate.primary_id,
                        candidate.duplicate_id,
                        candidate.score,
                        _dump(
                            {
                                "primary_key": candidate.primary_key,
                                "duplicate_key": candidate.duplicate_key,
                                "signature_similarity": candidate.signature_similarity,
                                "text_similarity": candidate.text_similarity,
                                "reasons": candidate.reasons,
                            }
                        ),
                        MergeProposalStatus.PENDING.value,
                        now.isoformat(),
                    ),
                )
        return self.list_merge_proposals()

    def list_merge_proposals(
        self, status: MergeProposalStatus | None = None
    ) -> list[MethodMergeProposal]:
        query = "SELECT * FROM method_merge_proposals WHERE workspace_id = ?"
        parameters: list[object] = [self.workspace_id]
        if status is not None:
            query += " AND status = ?"
            parameters.append(status.value)
        query += " ORDER BY score DESC, created_at, id"
        with self.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_proposal(row) for row in rows]

    def get_merge_proposal(self, proposal_id: str) -> MethodMergeProposal:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM method_merge_proposals
                WHERE id = ? AND workspace_id = ?
                """,
                (proposal_id, self.workspace_id),
            ).fetchone()
        if row is None:
            raise RecordNotFound(f"merge proposal not found: {proposal_id}")
        return self._row_to_proposal(row)

    def resolve_merge_proposal(
        self, proposal_id: str, status: MergeProposalStatus
    ) -> MethodMergeProposal:
        proposal = self.get_merge_proposal(proposal_id)
        if proposal.status is not MergeProposalStatus.PENDING:
            raise ValueError(
                f"merge proposal already resolved: {proposal.status.value}"
            )
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE method_merge_proposals
                SET status = ?, resolved_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (status.value, utc_now().isoformat(), proposal_id, self.workspace_id),
            )
        return self.get_merge_proposal(proposal_id)

    def apply_merge_proposal(self, proposal_id: str) -> MethodCard:
        """把副卡并入主卡。副卡置为 deprecated 而非删除。

        不删除既是审计要求，也是技术必需：`attempt_methods` 的外键是
        `ON DELETE RESTRICT`，被尝试记录引用过的方法卡本来就删不掉。
        """

        proposal = self.get_merge_proposal(proposal_id)
        if proposal.status is not MergeProposalStatus.PENDING:
            raise ValueError(
                f"merge proposal already resolved: {proposal.status.value}"
            )

        primary = self.get_method(proposal.primary_method_id)
        duplicate = self.get_method(proposal.duplicate_method_id)
        merged = merge_method_content(primary, card_to_draft(duplicate))
        signature = MethodSignature.model_validate(
            primary.signature or {}
        ).combined_with(MethodSignature.model_validate(duplicate.signature or {}))
        now = utc_now()

        with self.connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO method_versions (
                    id, workspace_id, method_id, version, content_json,
                    source_example_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    self.workspace_id,
                    primary.id,
                    primary.version,
                    _dump(
                        {
                            "name": primary.name,
                            "goal": primary.goal,
                            "applicable_when": primary.applicable_when,
                            "procedure": primary.procedure,
                            "failure_modes": primary.failure_modes,
                            "tags": primary.tags,
                        }
                    ),
                    None,
                    now.isoformat(),
                ),
            )
            connection.execute(
                """
                UPDATE methods
                SET name = ?, goal = ?, applicable_json = ?, procedure_json = ?,
                    failure_modes_json = ?, tags_json = ?, signature_json = ?,
                    success_count = success_count + ?, failure_count = failure_count + ?,
                    version = version + 1, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (
                    merged.name,
                    merged.goal,
                    _dump(merged.applicable_when),
                    _dump(merged.procedure),
                    _dump(merged.failure_modes),
                    _dump(merged.tags),
                    signature.model_dump_json(),
                    duplicate.success_count,
                    duplicate.failure_count,
                    now.isoformat(),
                    primary.id,
                    self.workspace_id,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO method_examples
                    (workspace_id, method_id, example_id)
                SELECT workspace_id, ?, example_id FROM method_examples
                WHERE workspace_id = ? AND method_id = ?
                """,
                (primary.id, self.workspace_id, duplicate.id),
            )
            connection.execute(
                "DELETE FROM method_examples WHERE workspace_id = ? AND method_id = ?",
                (self.workspace_id, duplicate.id),
            )
            connection.execute(
                """
                UPDATE methods
                SET status = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (
                    KnowledgeStatus.DEPRECATED.value,
                    now.isoformat(),
                    duplicate.id,
                    self.workspace_id,
                ),
            )
            connection.execute(
                """
                UPDATE method_merge_proposals
                SET status = ?, resolved_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (
                    MergeProposalStatus.APPLIED.value,
                    now.isoformat(),
                    proposal_id,
                    self.workspace_id,
                ),
            )
            self._record_event(
                connection,
                "methods_merged",
                primary.id,
                {
                    "primary_key": primary.key,
                    "duplicate_key": duplicate.key,
                    "duplicate_method_id": duplicate.id,
                    "score": proposal.score,
                },
            )
        return self.get_method(primary.id)

    @staticmethod
    def _row_to_proposal(row: sqlite3.Row) -> MethodMergeProposal:
        detail = json.loads(row["detail_json"])
        return MethodMergeProposal(
            id=row["id"],
            workspace_id=row["workspace_id"],
            primary_method_id=row["primary_method_id"],
            primary_key=detail["primary_key"],
            duplicate_method_id=row["duplicate_method_id"],
            duplicate_key=detail["duplicate_key"],
            score=row["score"],
            signature_similarity=detail["signature_similarity"],
            text_similarity=detail["text_similarity"],
            reasons=detail["reasons"],
            status=MergeProposalStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"])
                if row["resolved_at"]
                else None
            ),
        )

    def list_methods(
        self, include_pending: bool = True, include_deprecated: bool = False
    ) -> list[MethodCard]:
        statuses = [KnowledgeStatus.PROMOTED.value]
        if include_pending:
            statuses.append(KnowledgeStatus.PENDING_REVIEW.value)
        if include_deprecated:
            statuses.append(KnowledgeStatus.DEPRECATED.value)
        placeholders = ",".join("?" for _ in statuses)
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM methods
                WHERE workspace_id = ? AND status IN ({placeholders})
                ORDER BY status DESC, success_count DESC, updated_at DESC
                """,
                (self.workspace_id, *statuses),
            ).fetchall()
            result = []
            for row in rows:
                example_rows = connection.execute(
                    """
                    SELECT example_id FROM method_examples
                    WHERE workspace_id = ? AND method_id = ?
                    ORDER BY example_id
                    """,
                    (self.workspace_id, row["id"]),
                ).fetchall()
                result.append(
                    self._row_to_method(
                        row, [item["example_id"] for item in example_rows]
                    )
                )
        return result

    def list_learning_events(self) -> list[LearningEvent]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM learning_events
                WHERE workspace_id = ?
                ORDER BY created_at, id
                """,
                (self.workspace_id,),
            ).fetchall()
        return [
            LearningEvent(
                id=row["id"],
                workspace_id=row["workspace_id"],
                event_type=row["event_type"],
                target_id=row["target_id"],
                payload=json.loads(row["payload_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def add_evaluation(self, run: EvaluationRun) -> EvaluationRun:
        self._assert_workspace(run.workspace_id)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_runs (
                    id, workspace_id, report_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.workspace_id,
                    _dump(run.model_dump(mode="json")),
                    run.created_at.isoformat(),
                ),
            )
            self._record_event(
                connection,
                "evaluation_completed",
                run.id,
                {
                    "name": run.name,
                    "top_k": run.top_k,
                    "metrics": run.metrics.model_dump(mode="json"),
                },
            )
        return run

    def list_evaluations(self) -> list[EvaluationRun]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT report_json FROM evaluation_runs
                WHERE workspace_id = ?
                ORDER BY created_at, id
                """,
                (self.workspace_id,),
            ).fetchall()
        return [
            EvaluationRun.model_validate(json.loads(row["report_json"])) for row in rows
        ]

    def add_solution_attempt(self, attempt: SolutionAttempt) -> SolutionAttempt:
        """Persist an immutable attempt and atomically apply method feedback."""

        self._assert_workspace(attempt.workspace_id)
        used_keys = (
            set(attempt.candidate.used_method_keys) if attempt.candidate else set()
        )
        feedback_keys = set(attempt.feedback_method_keys)
        feedback_delta = 0
        if attempt.status is SolutionAttemptStatus.VERIFIED:
            feedback_delta = 1
        elif attempt.status is SolutionAttemptStatus.REJECTED:
            feedback_delta = -1

        with self.connection() as connection:
            if attempt.correction_of is not None:
                parent = connection.execute(
                    """
                    SELECT id FROM solution_attempts
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (attempt.correction_of, self.workspace_id),
                ).fetchone()
                if parent is None:
                    raise RecordNotFound(
                        "correction parent not found in workspace: "
                        f"{attempt.correction_of}"
                    )

            connection.execute(
                """
                INSERT INTO solution_attempts (
                    id, workspace_id, status, correction_of, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.id,
                    attempt.workspace_id,
                    attempt.status.value,
                    attempt.correction_of,
                    _dump(attempt.model_dump(mode="json")),
                    attempt.created_at.isoformat(),
                ),
            )

            credited_method_ids: set[str] = set()
            for rank, match in enumerate(attempt.recommended_methods, start=1):
                if match.method.workspace_id != self.workspace_id:
                    raise ValueError("cross-workspace method feedback rejected")
                exists = connection.execute(
                    """
                    SELECT id FROM methods
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (match.method.id, self.workspace_id),
                ).fetchone()
                if exists is None:
                    raise RecordNotFound(
                        f"method not found in workspace: {match.method.id}"
                    )
                used = match.method.key in used_keys
                connection.execute(
                    """
                    INSERT INTO attempt_methods (
                        workspace_id, attempt_id, method_id, method_key,
                        rank, score, used
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.workspace_id,
                        attempt.id,
                        match.method.id,
                        match.method.key,
                        rank,
                        match.score,
                        1 if used else 0,
                    ),
                )
                if (
                    match.method.key in feedback_keys
                    and feedback_delta
                    and match.method.id not in credited_method_ids
                ):
                    credited_method_ids.add(match.method.id)
                    success_delta = 1 if feedback_delta > 0 else 0
                    failure_delta = 1 if feedback_delta < 0 else 0
                    connection.execute(
                        """
                        UPDATE methods
                        SET success_count = success_count + ?,
                            failure_count = failure_count + ?,
                            updated_at = ?
                        WHERE id = ? AND workspace_id = ?
                        """,
                        (
                            success_delta,
                            failure_delta,
                            attempt.created_at.isoformat(),
                            match.method.id,
                            self.workspace_id,
                        ),
                    )
                    self._record_event(
                        connection,
                        "method_outcome_recorded",
                        match.method.id,
                        {
                            "attempt_id": attempt.id,
                            "method_key": match.method.key,
                            "outcome": attempt.status.value,
                        },
                    )

            self._record_event(
                connection,
                "solution_attempt_recorded",
                attempt.id,
                {
                    "status": attempt.status.value,
                    "verification_status": attempt.verification.status.value,
                    "generation_provider": attempt.generation.provider,
                    "used_method_keys": sorted(used_keys),
                    "feedback_method_keys": sorted(feedback_keys),
                    "credited_method_count": len(credited_method_ids),
                    "correction_of": attempt.correction_of,
                },
            )
        return attempt

    def get_solution_attempt(self, attempt_id: str) -> SolutionAttempt:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT report_json FROM solution_attempts
                WHERE id = ? AND workspace_id = ?
                """,
                (attempt_id, self.workspace_id),
            ).fetchone()
        if row is None:
            raise RecordNotFound(
                f"solution attempt not found in workspace: {attempt_id}"
            )
        return SolutionAttempt.model_validate(json.loads(row["report_json"]))

    def list_solution_attempts(self) -> list[SolutionAttempt]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT report_json FROM solution_attempts
                WHERE workspace_id = ?
                ORDER BY created_at, id
                """,
                (self.workspace_id,),
            ).fetchall()
        return [
            SolutionAttempt.model_validate(json.loads(row["report_json"]))
            for row in rows
        ]

    def add_solve_evaluation(self, run: SolveEvaluationRun) -> SolveEvaluationRun:
        self._assert_workspace(run.workspace_id)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO solve_evaluation_runs (
                    id, workspace_id, report_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.workspace_id,
                    _dump(run.model_dump(mode="json")),
                    run.created_at.isoformat(),
                ),
            )
            self._record_event(
                connection,
                "solve_evaluation_completed",
                run.id,
                {
                    "name": run.name,
                    "top_k": run.top_k,
                    "metrics": run.metrics.model_dump(mode="json"),
                },
            )
        return run

    def list_solve_evaluations(self) -> list[SolveEvaluationRun]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT report_json FROM solve_evaluation_runs
                WHERE workspace_id = ?
                ORDER BY created_at, id
                """,
                (self.workspace_id,),
            ).fetchall()
        return [
            SolveEvaluationRun.model_validate(json.loads(row["report_json"]))
            for row in rows
        ]

    def _record_event(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        target_id: str,
        payload: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO learning_events (
                id, workspace_id, event_type, target_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                self.workspace_id,
                event_type,
                target_id,
                _dump(payload),
                utc_now().isoformat(),
            ),
        )

    def _assert_workspace(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ValueError("cross-workspace write rejected")

    @staticmethod
    def _row_to_example(row: sqlite3.Row) -> ProblemExample:
        payload = (
            json.loads(row["math_payload_json"]) if row["math_payload_json"] else None
        )
        return ProblemExample.model_validate(
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "problem": row["problem"],
                "solution": row["solution"],
                "tags": json.loads(row["tags_json"]),
                "method_hint": row["method_hint"],
                "reviewed": bool(row["reviewed"]),
                "problem_kind": row["problem_kind"],
                "math_payload": payload,
                "verification": json.loads(row["verification_json"]),
                "extraction": (
                    json.loads(row["extraction_json"])
                    if row["extraction_json"]
                    else None
                ),
                "status": row["status"],
                "created_at": row["created_at"],
            }
        )

    @staticmethod
    def _row_to_method(row: sqlite3.Row, example_ids: list[str]) -> MethodCard:
        return MethodCard(
            id=row["id"],
            workspace_id=row["workspace_id"],
            key=row["method_key"],
            name=row["name"],
            goal=row["goal"],
            applicable_when=json.loads(row["applicable_json"]),
            procedure=json.loads(row["procedure_json"]),
            failure_modes=json.loads(row["failure_modes_json"]),
            tags=json.loads(row["tags_json"]),
            status=KnowledgeStatus(row["status"]),
            version=row["version"],
            signature=json.loads(row["signature_json"] or "{}"),
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            example_ids=example_ids,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
