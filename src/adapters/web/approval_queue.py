"""Manual approval queue for GitHub actions (CREATE_PR, MERGE_PR).

Provides a simple, file-backed queue so high-impact actions are never
executed without explicit human approval.

States: pending -> approved -> executed | failed
         \\-> rejected
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import CONFIG

MEMORY_DIR = Path("memory")
MEMORY_DIR.mkdir(exist_ok=True)
QUEUE_FILE = MEMORY_DIR / "action_approvals.jsonl"

_file_lock = asyncio.Lock()


@dataclass
class ActionApproval:
    id: str
    platform: str  # "github"
    action: str  # "create_pr" | "merge_pr"
    text: str
    meta: Dict[str, Any]
    status: str  # "pending" | "approved" | "rejected" | "executed" | "failed"
    created_at: str
    updated_at: str
    result_url: Optional[str] = None
    error: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_file():
    if not QUEUE_FILE.exists():
        QUEUE_FILE.touch()


def _append_record(rec: ActionApproval) -> None:
    _ensure_file()
    with QUEUE_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")


def _read_all() -> List[ActionApproval]:
    _ensure_file()
    out: List[ActionApproval] = []
    with QUEUE_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            out.append(ActionApproval(**data))
    return out


def _write_all(recs: List[ActionApproval]) -> None:
    tmp = QUEUE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    tmp.replace(QUEUE_FILE)


async def enqueue_action(platform: str, action: str, text: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rec = ActionApproval(
        id=str(uuid.uuid4()),
        platform=platform,
        action=action,
        text=text,
        meta=meta or {},
        status="pending",
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    async with _file_lock:
        _append_record(rec)
    return {"success": True, "queued": True, "approval_id": rec.id, "text": text}


def list_pending() -> List[Dict[str, Any]]:
    return [asdict(r) for r in _read_all() if r.status == "pending"]


def _update_status(rec_id: str, status: str, **kw) -> Optional[ActionApproval]:
    recs = _read_all()
    found = None
    for r in recs:
        if r.id == rec_id:
            r.status = status
            r.updated_at = _now_iso()
            for k, v in kw.items():
                setattr(r, k, v)
            found = r
            break
    if found:
        _write_all(recs)
    return found


async def approve_and_execute(rec_id: str, github_client) -> Dict[str, Any]:
    async with _file_lock:
        recs = _read_all()
        target = next((r for r in recs if r.id == rec_id), None)
        if not target:
            return {"success": False, "error": "not_found"}
        if target.status != "pending":
            return {"success": False, "error": f"invalid_status:{target.status}"}
        _update_status(rec_id, "approved")

        try:
            from src.domain.action_parser import parse_kv_body
            fields = parse_kv_body(target.text)

            if target.action == "create_pr":
                result = await github_client.create_pr(
                    title=fields.get("title", ""),
                    body=fields.get("body", ""),
                    head=fields.get("head", ""),
                    base=fields.get("base", "main"),
                )
            elif target.action == "merge_pr":
                pr_number = int(fields.get("pr_number", "0"))
                method = fields.get("method", "squash")
                result = await github_client.merge_pr(pr_number, method)
            else:
                raise ValueError(f"unsupported action: {target.action}")

            if result.success:
                _update_status(rec_id, "executed", result_url=result.url)
                return {"success": True, "url": result.url}
            else:
                _update_status(rec_id, "failed", error=result.error)
                return {"success": False, "error": result.error}
        except Exception as e:
            _update_status(rec_id, "failed", error=str(e))
            return {"success": False, "error": str(e)}


async def reject(rec_id: str) -> Dict[str, Any]:
    async with _file_lock:
        updated = _update_status(rec_id, "rejected")
    if not updated:
        return {"success": False, "error": "not_found"}
    return {"success": True}
