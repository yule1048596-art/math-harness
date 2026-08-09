from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import uuid
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from math_harness.errors import InvalidPortableData
from math_harness.models import (
    Workspace,
    WorkspaceRestoreResult,
    utc_now,
)
from math_harness.storage import WorkspaceManager
from math_harness.version import VERSION

ARCHIVE_MEDIA_TYPE = "application/vnd.math-harness.workspace+zip"
ARCHIVE_FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_DATABASE_BYTES = 120 * 1024 * 1024
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "workspace.sqlite3"

_REQUIRED_TABLES = {
    "examples",
    "example_versions",
    "methods",
    "method_versions",
    "method_merge_proposals",
    "method_examples",
    "learning_events",
    "evaluation_runs",
    "solution_attempts",
    "attempt_methods",
    "solve_evaluation_runs",
}
_WORKSPACE_TABLES = _REQUIRED_TABLES | {
    "conversations",
    "conversation_messages",
    "memory_items",
    "memory_extraction_jobs",
    "conversation_memory_cursors",
    "memory_settings",
}
_MEMORY_TABLES = {
    "memory_items",
    "memory_extraction_jobs",
    "conversation_memory_cursors",
    "memory_settings",
}
_MEMORY_FTS_TABLES = {
    "memory_items_fts",
    "memory_items_fts_data",
    "memory_items_fts_idx",
    "memory_items_fts_docsize",
    "memory_items_fts_config",
}
_MEMORY_TRIGGERS = {"memory_items_ai", "memory_items_ad", "memory_items_au"}


def _normalize_schema_sql(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


_MEMORY_FTS_SQL = _normalize_schema_sql(
    """
    CREATE VIRTUAL TABLE memory_items_fts USING fts5(
        content,
        search_text,
        content='memory_items',
        content_rowid='rowid',
        tokenize='unicode61 remove_diacritics 2'
    )
    """
)
_MEMORY_TRIGGER_SQL = {
    "memory_items_ai": _normalize_schema_sql(
        """
        CREATE TRIGGER memory_items_ai AFTER INSERT ON memory_items BEGIN
            INSERT INTO memory_items_fts(rowid, content, search_text)
            VALUES (new.rowid, new.content, new.search_text);
        END
        """
    ),
    "memory_items_ad": _normalize_schema_sql(
        """
        CREATE TRIGGER memory_items_ad AFTER DELETE ON memory_items BEGIN
            INSERT INTO memory_items_fts(memory_items_fts, rowid, content, search_text)
            VALUES ('delete', old.rowid, old.content, old.search_text);
        END
        """
    ),
    "memory_items_au": _normalize_schema_sql(
        """
        CREATE TRIGGER memory_items_au
        AFTER UPDATE OF content, search_text ON memory_items BEGIN
            INSERT INTO memory_items_fts(memory_items_fts, rowid, content, search_text)
            VALUES ('delete', old.rowid, old.content, old.search_text);
            INSERT INTO memory_items_fts(rowid, content, search_text)
            VALUES (new.rowid, new.content, new.search_text);
        END
        """
    ),
}
_REQUIRED_INDEXES = {
    "idx_evaluation_runs_workspace",
    "idx_example_versions_example",
    "idx_examples_source_attempt",
    "idx_examples_workspace",
    "idx_method_versions_method",
    "idx_solution_attempts_workspace",
    "idx_solve_evaluation_runs_workspace",
}
_WORKSPACE_INDEXES = _REQUIRED_INDEXES | {
    "idx_conversations_workspace",
    "idx_conversation_messages_conversation",
    "idx_memory_items_workspace",
    "idx_memory_items_fingerprint",
    "idx_memory_jobs_workspace",
    "idx_memory_jobs_conversation",
    "idx_memory_cursors_workspace",
}
_MEMORY_INDEXES = {
    "idx_memory_items_workspace",
    "idx_memory_items_fingerprint",
    "idx_memory_jobs_workspace",
    "idx_memory_jobs_conversation",
    "idx_memory_cursors_workspace",
}
_TABLE_COLUMNS = {
    "examples": {
        "id",
        "workspace_id",
        "problem",
        "solution",
        "tags_json",
        "method_hint",
        "reviewed",
        "problem_kind",
        "math_payload_json",
        "verification_json",
        "extraction_json",
        "method_drafts_json",
        "status",
        "origin",
        "source_attempt_id",
        "reviewed_at",
        "reviewer_note",
        "revision",
        "created_at",
        "updated_at",
    },
    "example_versions": {
        "id",
        "workspace_id",
        "example_id",
        "revision",
        "content_json",
        "created_at",
    },
    "methods": {
        "id",
        "workspace_id",
        "method_key",
        "name",
        "goal",
        "applicable_json",
        "procedure_json",
        "failure_modes_json",
        "tags_json",
        "status",
        "version",
        "success_count",
        "failure_count",
        "signature_json",
        "conclusion_confidence",
        "created_at",
        "updated_at",
    },
    "method_versions": {
        "id",
        "workspace_id",
        "method_id",
        "version",
        "content_json",
        "source_example_id",
        "created_at",
    },
    "method_merge_proposals": {
        "id",
        "workspace_id",
        "primary_method_id",
        "duplicate_method_id",
        "score",
        "detail_json",
        "status",
        "created_at",
        "resolved_at",
    },
    "method_examples": {"workspace_id", "method_id", "example_id"},
    "learning_events": {
        "id",
        "workspace_id",
        "event_type",
        "target_id",
        "payload_json",
        "created_at",
    },
    "evaluation_runs": {"id", "workspace_id", "report_json", "created_at"},
    "solution_attempts": {
        "id",
        "workspace_id",
        "status",
        "correction_of",
        "report_json",
        "created_at",
    },
    "attempt_methods": {
        "workspace_id",
        "attempt_id",
        "method_id",
        "method_key",
        "rank",
        "score",
        "used",
    },
    "solve_evaluation_runs": {"id", "workspace_id", "report_json", "created_at"},
    "conversations": {
        "id",
        "workspace_id",
        "title",
        "summary",
        "summary_through_ordinal",
        "message_count",
        "status",
        # 对话记住的模型选择。只是档案 ID 和模型名，**不含密钥**——密钥只在
        # Keychain 里，备份文件不加密，任何进这里的列都要按公开数据对待。
        "provider_profile_id",
        "model",
        "created_at",
        "updated_at",
    },
    "conversation_messages": {
        "id",
        "workspace_id",
        "conversation_id",
        "turn_id",
        "ordinal",
        "role",
        "kind",
        "content",
        "provider",
        "model",
        "generation_error",
        "attempt_id",
        "knowledge_draft_id",
        "verification_status",
        "conclusion_confidence",
        "process_confidence",
        "counterexample_json",
        "checked_claims_json",
        "method_keys_json",
        "created_at",
    },
    "memory_items": {
        "id",
        "workspace_id",
        "kind",
        "content",
        "normalized_fingerprint",
        "search_text",
        "tags_json",
        "status",
        "pinned",
        "source",
        "conversation_id",
        "source_message_id",
        "evidence",
        "supersedes_id",
        "created_at",
        "updated_at",
    },
    "memory_extraction_jobs": {
        "id",
        "workspace_id",
        "conversation_id",
        "from_ordinal",
        "through_ordinal",
        "source_revision",
        "status",
        "attempts",
        "provider",
        "model",
        "extracted_count",
        "input_tokens",
        "output_tokens",
        "duration_ms",
        "error",
        "created_at",
        "started_at",
        "completed_at",
    },
    "conversation_memory_cursors": {
        "conversation_id",
        "workspace_id",
        "through_ordinal",
        "updated_at",
    },
    "memory_settings": {
        "workspace_id",
        "automatic_extraction_enabled",
        "updated_at",
    },
}

# 备份先按白名单验证再交给 WorkspaceStore 做数据库迁移。新增可空列时，上一版备份
# 合理地还没有它；只允许缺少这里逐项列明的迁移列，未知的多余列仍然拒绝。
_MIGRATABLE_MISSING_COLUMNS: dict[str, set[str]] = {
    "conversation_messages": {"generation_error"},
}


class _DatabaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Literal["workspace.sqlite3"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=1, le=MAX_DATABASE_BYTES)
    record_counts: dict[str, int]


class _WorkspaceArchiveManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["math-harness-workspace"]
    format_version: Literal[1]
    app_version: str = Field(min_length=1, max_length=40)
    exported_at: str
    workspace: Workspace
    database: _DatabaseManifest


def create_workspace_archive(
    manager: WorkspaceManager,
    workspace_id: str,
) -> bytes:
    workspace = manager.get(workspace_id)
    store = manager.store(workspace_id)
    with TemporaryDirectory(prefix="math-harness-backup-") as temporary:
        snapshot_path = Path(temporary) / DATABASE_NAME
        store.backup_to(snapshot_path)
        size = snapshot_path.stat().st_size
        if size > MAX_DATABASE_BYTES:
            raise InvalidPortableData(
                f"workspace database exceeds the {MAX_DATABASE_BYTES} byte backup limit"
            )
        digest = _sha256_file(snapshot_path)
        record_counts = _validate_database(
            snapshot_path,
            expected_workspace_id=workspace.id,
        )
        manifest = _WorkspaceArchiveManifest(
            kind="math-harness-workspace",
            format_version=ARCHIVE_FORMAT_VERSION,
            app_version=VERSION,
            exported_at=utc_now().isoformat(),
            workspace=workspace,
            database=_DatabaseManifest(
                path=DATABASE_NAME,
                sha256=digest,
                size=size,
                record_counts=record_counts,
            ),
        )
        output = io.BytesIO()
        with zipfile.ZipFile(
            output,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.writestr(
                MANIFEST_NAME,
                manifest.model_dump_json(indent=2).encode("utf-8"),
            )
            archive.write(snapshot_path, DATABASE_NAME)
        payload = output.getvalue()
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise InvalidPortableData(
            f"workspace archive exceeds the {MAX_ARCHIVE_BYTES} byte limit"
        )
    return payload


def restore_workspace_archive(
    manager: WorkspaceManager,
    payload: bytes,
) -> WorkspaceRestoreResult:
    if not payload:
        raise InvalidPortableData("workspace archive is empty")
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise InvalidPortableData(
            f"workspace archive exceeds the {MAX_ARCHIVE_BYTES} byte limit"
        )

    with TemporaryDirectory(prefix="math-harness-restore-") as temporary:
        snapshot_path = Path(temporary) / DATABASE_NAME
        manifest, database_bytes = _read_archive(payload)
        if len(database_bytes) != manifest.database.size:
            raise InvalidPortableData("workspace database size does not match manifest")
        digest = hashlib.sha256(database_bytes).hexdigest()
        if digest != manifest.database.sha256:
            raise InvalidPortableData(
                "workspace database checksum does not match manifest"
            )
        snapshot_path.write_bytes(database_bytes)
        source_counts = _validate_database(
            snapshot_path,
            expected_workspace_id=manifest.workspace.id,
        )
        if source_counts != manifest.database.record_counts:
            raise InvalidPortableData("workspace record counts do not match manifest")

        restored_workspace = Workspace(
            id=str(uuid.uuid4()),
            name=_restored_name(manifest.workspace.name),
            description=manifest.workspace.description,
            created_at=utc_now(),
        )
        _rebind_workspace_database(
            snapshot_path,
            old_workspace_id=manifest.workspace.id,
            new_workspace_id=restored_workspace.id,
        )
        restored_counts = _validate_database(
            snapshot_path,
            expected_workspace_id=restored_workspace.id,
        )
        manager.register_snapshot(restored_workspace, snapshot_path)

    return WorkspaceRestoreResult(
        workspace=restored_workspace,
        source_workspace_id=manifest.workspace.id,
        source_app_version=manifest.app_version,
        archive_format_version=manifest.format_version,
        restored_record_counts=restored_counts,
    )


def _read_archive(payload: bytes) -> tuple[_WorkspaceArchiveManifest, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            if len(names) != 2 or set(names) != {MANIFEST_NAME, DATABASE_NAME}:
                raise InvalidPortableData(
                    "workspace archive must contain only manifest.json and workspace.sqlite3"
                )
            entries = {entry.filename: entry for entry in archive.infolist()}
            if any(entry.flag_bits & 0x1 for entry in entries.values()):
                raise InvalidPortableData(
                    "encrypted workspace archives are not supported"
                )
            manifest_info = entries[MANIFEST_NAME]
            database_info = entries[DATABASE_NAME]
            if manifest_info.file_size > 64 * 1024:
                raise InvalidPortableData("workspace manifest is too large")
            if database_info.file_size > MAX_DATABASE_BYTES:
                raise InvalidPortableData("workspace database is too large")
            manifest_bytes = archive.read(MANIFEST_NAME)
            database_bytes = archive.read(DATABASE_NAME)
    except InvalidPortableData:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise InvalidPortableData(f"invalid workspace archive: {exc}") from exc

    try:
        manifest = _WorkspaceArchiveManifest.model_validate_json(manifest_bytes)
    except ValidationError as exc:
        raise InvalidPortableData(f"invalid workspace manifest: {exc}") from exc
    return manifest, database_bytes


def _validate_database(
    database_path: Path,
    *,
    expected_workspace_id: str,
) -> dict[str, int]:
    try:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA trusted_schema = OFF")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise InvalidPortableData(
                f"workspace database integrity check failed: {integrity}"
            )
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise InvalidPortableData("workspace database has broken foreign keys")

        schema_rows = connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if any(row["type"] == "view" for row in schema_rows):
            raise InvalidPortableData("workspace database cannot contain views")
        unsupported_objects = {
            row["name"]
            for row in schema_rows
            if row["type"] not in {"table", "index", "trigger"}
        }
        if unsupported_objects:
            raise InvalidPortableData(
                "workspace database contains unsupported schema objects: "
                + ", ".join(sorted(unsupported_objects))
            )
        all_tables = {row["name"] for row in schema_rows if row["type"] == "table"}
        tables = all_tables - _MEMORY_FTS_TABLES
        unknown_tables = tables - _WORKSPACE_TABLES
        missing_tables = _REQUIRED_TABLES - tables
        if unknown_tables:
            raise InvalidPortableData(
                "workspace database contains unsupported tables: "
                + ", ".join(sorted(unknown_tables))
            )
        if missing_tables:
            raise InvalidPortableData(
                "workspace database is missing required tables: "
                + ", ".join(sorted(missing_tables))
            )
        memory_present = bool(tables & _MEMORY_TABLES)
        if memory_present and not _MEMORY_TABLES <= tables:
            raise InvalidPortableData(
                "workspace database has an incomplete memory schema"
            )
        fts_present = all_tables & _MEMORY_FTS_TABLES
        if memory_present and fts_present != _MEMORY_FTS_TABLES:
            raise InvalidPortableData(
                "workspace database has an incomplete memory FTS index"
            )
        if not memory_present and fts_present:
            raise InvalidPortableData(
                "workspace database has an orphaned memory FTS index"
            )
        truly_unknown_tables = all_tables - _WORKSPACE_TABLES - _MEMORY_FTS_TABLES
        if truly_unknown_tables:
            raise InvalidPortableData(
                "workspace database contains unsupported tables: "
                + ", ".join(sorted(truly_unknown_tables))
            )
        indexes = {row["name"] for row in schema_rows if row["type"] == "index"}
        unknown_indexes = indexes - _WORKSPACE_INDEXES
        required_indexes = _REQUIRED_INDEXES | (
            _MEMORY_INDEXES if memory_present else set()
        )
        missing_indexes = required_indexes - indexes
        if unknown_indexes or missing_indexes:
            details = []
            if unknown_indexes:
                details.append("unknown: " + ", ".join(sorted(unknown_indexes)))
            if missing_indexes:
                details.append("missing: " + ", ".join(sorted(missing_indexes)))
            raise InvalidPortableData(
                "workspace database index layout is unsupported ("
                + "; ".join(details)
                + ")"
            )
        triggers = {row["name"] for row in schema_rows if row["type"] == "trigger"}
        if triggers - _MEMORY_TRIGGERS:
            raise InvalidPortableData(
                "workspace database contains unsupported triggers or views: "
                + ", ".join(sorted(triggers - _MEMORY_TRIGGERS))
            )
        if memory_present and triggers != _MEMORY_TRIGGERS:
            raise InvalidPortableData(
                "workspace database has incomplete memory triggers"
            )
        if not memory_present and triggers:
            raise InvalidPortableData("workspace database has orphaned memory triggers")
        for row in schema_rows:
            if row["type"] != "trigger" or row["name"] not in _MEMORY_TRIGGER_SQL:
                continue
            if _normalize_schema_sql(row["sql"]) != _MEMORY_TRIGGER_SQL[row["name"]]:
                raise InvalidPortableData(
                    f"workspace database has unsupported trigger definition: {row['name']}"
                )
        for row in schema_rows:
            sql = (row["sql"] or "").lstrip().upper()
            if (
                sql.startswith("CREATE VIRTUAL TABLE")
                and row["name"] != "memory_items_fts"
            ):
                raise InvalidPortableData(
                    "unsupported virtual table in workspace backup"
                )
            if (
                row["name"] == "memory_items_fts"
                and _normalize_schema_sql(row["sql"]) != _MEMORY_FTS_SQL
            ):
                raise InvalidPortableData(
                    "workspace database has unsupported memory FTS definition"
                )

        counts: dict[str, int] = {}
        for table in sorted(tables):
            columns = _table_columns(connection, table)
            expected_columns = _TABLE_COLUMNS[table]
            unknown_columns = columns - expected_columns
            unsupported_missing = (
                expected_columns
                - columns
                - _MIGRATABLE_MISSING_COLUMNS.get(table, set())
            )
            if unknown_columns or unsupported_missing:
                raise InvalidPortableData(
                    f"workspace database has unsupported columns in table {table}"
                )
            if "workspace_id" in columns:
                values = {
                    row[0]
                    for row in connection.execute(
                        f'SELECT DISTINCT workspace_id FROM "{table}"'
                    ).fetchall()
                }
                if values - {expected_workspace_id}:
                    raise InvalidPortableData(
                        f"workspace database mixes scopes in table {table}"
                    )
            counts[table] = connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
        return counts
    except InvalidPortableData:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise InvalidPortableData(f"invalid workspace database: {exc}") from exc
    finally:
        if "connection" in locals():
            connection.close()


def _rebind_workspace_database(
    database_path: Path,
    *,
    old_workspace_id: str,
    new_workspace_id: str,
) -> None:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for table in tables:
            columns = _table_columns(connection, table)
            for column in columns:
                if not column.endswith("_json"):
                    continue
                rows = connection.execute(
                    f'SELECT rowid, "{column}" FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL'
                ).fetchall()
                for row in rows:
                    try:
                        original = json.loads(row[column])
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise InvalidPortableData(
                            f"invalid JSON in {table}.{column}"
                        ) from exc
                    rebound = _replace_workspace_id(
                        original,
                        old_workspace_id,
                        new_workspace_id,
                    )
                    if rebound != original:
                        connection.execute(
                            f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',
                            (
                                json.dumps(
                                    rebound,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                                row["rowid"],
                            ),
                        )
            if "workspace_id" in columns:
                connection.execute(
                    f'UPDATE "{table}" SET workspace_id = ? WHERE workspace_id = ?',
                    (new_workspace_id, old_workspace_id),
                )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise InvalidPortableData("restored workspace has broken foreign keys")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _replace_workspace_id(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return new if value == old else value
    if isinstance(value, list):
        return [_replace_workspace_id(item, old, new) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_workspace_id(item, old, new) for key, item in value.items()
        }
    return value


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _restored_name(name: str) -> str:
    suffix = "（恢复）"
    return f"{name[: 120 - len(suffix)]}{suffix}"
