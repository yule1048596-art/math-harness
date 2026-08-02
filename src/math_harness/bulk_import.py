from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from pydantic import ValidationError

from math_harness.errors import InvalidPortableData
from math_harness.models import (
    ExampleCreate,
    ImportFileFormat,
    ImportReviewPolicy,
    ProblemExample,
)

MAX_IMPORT_ITEMS = 500
MAX_IMPORT_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class ParsedImportItem:
    index: int
    request: ExampleCreate | None
    errors: tuple[str, ...] = ()


def parse_example_corpus(
    content: str,
    requested_format: ImportFileFormat,
    review_policy: ImportReviewPolicy,
    *,
    source_name: str,
) -> tuple[ImportFileFormat, list[ParsedImportItem]]:
    if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise InvalidPortableData(
            f"single import is limited to {MAX_IMPORT_BYTES} UTF-8 bytes"
        )
    detected = _detect_format(content, requested_format, source_name)
    raw_items = (
        _parse_json_document(content)
        if detected is ImportFileFormat.JSON
        else _parse_json_lines(content)
    )
    if len(raw_items) > MAX_IMPORT_ITEMS:
        raise InvalidPortableData(
            f"single import is limited to {MAX_IMPORT_ITEMS} examples"
        )

    parsed: list[ParsedImportItem] = []
    for index, raw, parse_error in raw_items:
        if parse_error:
            parsed.append(
                ParsedImportItem(index=index, request=None, errors=(parse_error,))
            )
            continue
        if not isinstance(raw, dict):
            parsed.append(
                ParsedImportItem(
                    index=index,
                    request=None,
                    errors=("each example must be a JSON object",),
                )
            )
            continue
        try:
            request = ExampleCreate.model_validate(raw)
            if review_policy is ImportReviewPolicy.PENDING and request.reviewed:
                request = request.model_copy(update={"reviewed": False})
            parsed.append(ParsedImportItem(index=index, request=request))
        except ValidationError as exc:
            messages = tuple(
                _validation_message(error) for error in exc.errors(include_url=False)
            )
            parsed.append(ParsedImportItem(index=index, request=None, errors=messages))

    if not parsed:
        raise InvalidPortableData("the import file does not contain any examples")
    return detected, parsed


def example_fingerprint(request: ExampleCreate) -> str:
    payload = request.model_dump(mode="json", exclude={"reviewed"})
    payload["tags"] = sorted(payload.get("tags", []))
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def stored_example_fingerprint(example: ProblemExample) -> str:
    return example_fingerprint(
        ExampleCreate(
            problem=example.problem,
            solution=example.solution,
            tags=example.tags,
            method_hint=example.method_hint,
            math_payload=example.math_payload,
            reviewed=example.reviewed,
        )
    )


def problem_preview(request: ExampleCreate | None) -> str:
    if request is None:
        return ""
    compact = " ".join(request.problem.split())
    return compact[:240]


def _detect_format(
    content: str,
    requested: ImportFileFormat,
    source_name: str,
) -> ImportFileFormat:
    if requested is not ImportFileFormat.AUTO:
        return requested
    suffix = PurePath(source_name).suffix.lower()
    if suffix in {".jsonl", ".ndjson"}:
        return ImportFileFormat.JSONL
    try:
        json.loads(content)
    except json.JSONDecodeError:
        return ImportFileFormat.JSONL
    return ImportFileFormat.JSON


def _parse_json_document(content: str) -> list[tuple[int, Any, str | None]]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        return [(1, None, f"invalid JSON at line {exc.lineno}: {exc.msg}")]
    if isinstance(value, dict) and "examples" in value:
        value = value["examples"]
    if isinstance(value, list):
        return [(index, item, None) for index, item in enumerate(value, start=1)]
    if isinstance(value, dict):
        return [(1, value, None)]
    return [(1, value, "JSON corpus must be an object, an array, or contain examples")]


def _parse_json_lines(content: str) -> list[tuple[int, Any, str | None]]:
    items: list[tuple[int, Any, str | None]] = []
    logical_index = 0
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        logical_index += 1
        try:
            items.append((logical_index, json.loads(line), None))
        except json.JSONDecodeError as exc:
            items.append(
                (
                    logical_index,
                    None,
                    f"invalid JSONL at source line {line_number}: {exc.msg}",
                )
            )
    return items


def _validation_message(error: dict[str, Any]) -> str:
    location = ".".join(str(part) for part in error.get("loc", ())) or "item"
    return f"{location}: {error.get('msg', 'invalid value')}"[:500]
