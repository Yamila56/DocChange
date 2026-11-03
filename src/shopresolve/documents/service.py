import asyncio
import copy
import difflib
import posixpath
import re
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy import select

from shopresolve.errors import DomainError
from shopresolve.runstore import RunStore, append_event, checkpoint, db_now, require_lease
from shopresolve.service import CommerceService, digest, retry_local_transaction

from .models import ChangeSet, Revision, Workspace
from .retriever import DocumentRetriever, tokens

EDITABLE_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}


def valid_path(path):
    p = PurePosixPath(path)
    return bool(path) and not (
        p.is_absolute()
        or ".." in p.parts
        or ".git" in p.parts
        or "\\" in path
        or ":" in path
        or "\x00" in path
        or str(p) != path
        or len(path) > 512
    )


def apply_edits(documents, edits, read_paths, *, allowed=None, creatable=None):
    """Shared deterministic patch guard for workbench and repository adapters."""
    candidate = copy.deepcopy(documents)
    for edit in edits:
        path = edit["path"]
        operation = edit.get("operation", "replace")
        if not valid_path(path) or PurePosixPath(path).suffix.lower() not in EDITABLE_SUFFIXES:
            raise DomainError("INVALID_PATH", "Use a relative documentation path")
        if allowed is not None and path not in allowed:
            raise DomainError("SCOPE_VIOLATION", "Edit outside the authorized task scope")
        if operation == "create":
            if path in candidate or (creatable is not None and path not in creatable):
                raise DomainError("CREATE_CONFLICT", "New document path is not available")
            if not edit.get("new"):
                raise DomainError("INVALID_PATCH", "New document must have content")
            candidate[path] = {"role": "document", "content": edit["new"]}
            continue
        if path not in candidate or candidate[path].get("role", "document") != "document":
            raise DomainError("READ_ONLY", "Only target documents may be changed")
        if path not in read_paths:
            raise DomainError("READ_FIRST", "Read the target before editing")
        if operation == "delete":
            del candidate[path]
        elif operation == "replace":
            old, new = edit.get("old", ""), edit.get("new", "")
            count = candidate[path]["content"].count(old) if old else 0
            if not count or count != edit.get("count", 1) or old == new:
                raise DomainError("PATCH_MISMATCH", "Exact occurrence count differs; reread target")
            candidate[path]["content"] = candidate[path]["content"].replace(old, new)
        else:
            raise DomainError("INVALID_PATCH", "Unknown edit operation")
    return candidate


def check_documents(documents):
    if not 1 <= len(documents) <= 50:
        raise DomainError("DOCUMENT_LIMIT", "Import between 1 and 50 Markdown files", 422)
    total = 0
    for path, doc in documents.items():
        if not isinstance(doc, dict) or not isinstance(doc.get("content"), str):
            raise DomainError("INVALID_DOCUMENT", "Document content must be text", 422)
        p = PurePosixPath(path)
        if not valid_path(path) or (
            doc.get("role", "document") == "document" and p.suffix.lower() not in EDITABLE_SUFFIXES
        ):
            raise DomainError("INVALID_PATH", "Use relative documentation paths", 422)
        total += len(doc["content"].encode())
        if len(doc["content"].encode()) > 60000:
            raise DomainError("DOCUMENT_LIMIT", "Each file must be under 60KB", 422)
    if total > 500000:
        raise DomainError("DOCUMENT_LIMIT", "Workspace must be under 500KB", 422)


def build_index(documents):
    chunks = []
    for path, doc in sorted(documents.items()):
        lines = doc["content"].splitlines()
        for start in range(0, len(lines), 12):
            quote = "\n".join(lines[start : start + 12])
            chunks.append(
                {
                    "id": digest([path, start, quote])[:24],
                    "path": path,
                    "start_line": start + 1,
                    "end_line": min(start + 12, len(lines)),
                    "quote": quote,
                    "content_hash": digest(quote),
                }
            )
    return {
        "status": "READY",
        "method": "bm25-v1",
        "chunks": chunks,
        "source_hash": digest(documents),
    }


def broken_links(documents):
    broken = set()
    for path, doc in documents.items():
        for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", doc["content"]):
            if ":" in target or target.startswith("//"):
                continue  # External links are not fetched.
            file, _, anchor = target.partition("#")
            dest = (
                posixpath.normpath(posixpath.join(posixpath.dirname(path), file)) if file else path
            )
            if dest not in documents:
                broken.add(f"{path}: {target}")
            elif anchor:
                headings = re.findall(r"^#{1,6}\s+(.+)$", documents[dest]["content"], re.M)
                slugs = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}
                if anchor not in slugs:
                    broken.add(f"{path}: {target}")
    return sorted(broken)


def search_chunks(chunks, query, exact):
    """Compute against a detached revision snapshot, without holding a SQL session."""
    if not chunks or not query.strip():
        return []
    if exact:
        return [c for c in chunks if query in c["quote"]]
    index = DocumentRetriever([c["id"] for c in chunks], [c["quote"] for c in chunks])
    by_id = {c["id"]: c for c in chunks}
    terms = set(tokens(query))
    return [
        by_id[key]
        for key in index.rank(query, len(chunks))
        if terms & set(tokens(by_id[key]["quote"]))
    ][:6]


class DocumentService:
    @staticmethod
    def validate_contract(documents, contract):
        allowed = contract.get("editable_paths")
        creatable = contract.get("creatable_paths") or []
        if any(
            not valid_path(p)
            or p in documents
            or PurePosixPath(p).suffix.lower() not in EDITABLE_SUFFIXES
            for p in creatable
        ):
            raise DomainError("INVALID_SCOPE", "New document paths must be safe and unused", 422)
        if allowed is not None and (
            not allowed
            or any(
                (p not in documents and p not in creatable)
                or documents.get(p, {}).get("role", "document") != "document"
                for p in allowed
            )
        ):
            raise DomainError("INVALID_SCOPE", "Select existing editable documents", 422)
        for field in ("required_absent", "required_present"):
            conditions = contract.get(field) or {}
            if len(conditions) > 50:
                raise DomainError("INVALID_CONTRACT", "Too many postconditions", 422)
            for path, phrases in conditions.items():
                if (path not in documents and path not in creatable) or (
                    allowed is not None and path not in allowed
                ):
                    raise DomainError("INVALID_CONTRACT", "Postcondition outside task scope", 422)
                if not phrases or len(phrases) > 30 or any(not s or len(s) > 1000 for s in phrases):
                    raise DomainError(
                        "INVALID_CONTRACT", "Use bounded nonempty postconditions", 422
                    )

    def __init__(self, sessions):
        self.sessions = sessions
        self.idempotency = CommerceService(sessions)
        self.runs = RunStore(sessions)

    async def _owned(self, session, owner, wid, lock=False):
        query = select(Workspace).where(Workspace.id == wid, Workspace.owner_id == owner)
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise DomainError("NOT_FOUND", "Workspace not found", 404)
        return row

    @retry_local_transaction
    async def create(self, owner, name, documents, key):
        check_documents(documents)
        index = build_index(documents)
        async with self.sessions.begin() as session:
            idem = await self.idempotency._idem(
                session, owner, "doc.create", key, {"name": name, "documents": documents}
            )
            if idem.response:
                return idem.response
            row = Workspace(id=str(uuid4()), owner_id=owner, name=name, revision=1)
            session.add(row)
            await session.flush()
            session.add(
                Revision(
                    workspace_id=row.id,
                    number=1,
                    documents=documents,
                    index=index,
                    content_hash=digest(documents),
                )
            )
            idem.response = {"id": row.id, "name": name, "revision": 1}
            return idem.response

    async def list(self, owner):
        async with self.sessions() as session:
            rows = (
                await session.scalars(select(Workspace).where(Workspace.owner_id == owner))
            ).all()
            return [{"id": w.id, "name": w.name, "revision": w.revision} for w in rows]

    async def get(self, owner, wid, number=None):
        async with self.sessions() as session:
            w = await self._owned(session, owner, wid)
            r = await session.get(Revision, (wid, number or w.revision))
            if r is None:
                raise DomainError("NOT_FOUND", "Revision not found", 404)
            if r.index["source_hash"] != r.content_hash or digest(r.documents) != r.content_hash:
                raise DomainError("INDEX_INVALID", "Snapshot integrity check failed")
            return {
                "id": wid,
                "name": w.name,
                "revision": r.number,
                "active_revision": w.revision,
                "documents": r.documents,
                "content_hash": r.content_hash,
                "index": r.index,
            }

    async def search(self, owner, wid, number, query, *, exact=False):
        snapshot = await self.get(owner, wid, number)
        # get() closes its session before CPU work. Keep the event loop available
        # for task submissions, model I/O and worker heartbeats during retrieval.
        hits = await asyncio.to_thread(search_chunks, snapshot["index"]["chunks"], query, exact)
        return [{**c, "version": f"{wid}@{number}"} for c in hits]

    async def prepare(self, lease, state, edits, evidence, summary):
        cfg = state["config"]["document"]
        base = await self.get(lease.owner_id, cfg["workspace_id"], cfg["revision"])
        self.validate_contract(base["documents"], cfg)
        known = {c["id"]: c for c in state.get("evidence", [])}
        if not evidence or not set(evidence) <= set(known):
            raise DomainError("EVIDENCE_REQUIRED", "Read and cite source evidence before proposing")
        if not 1 <= len(edits) <= 30:
            raise DomainError("INVALID_PATCH", "Provide 1 to 30 exact replacement edits")
        candidate = apply_edits(
            base["documents"],
            edits,
            state.get("read_paths", []),
            allowed=cfg.get("editable_paths"),
            creatable=cfg.get("creatable_paths", []),
        )
        check_documents(candidate)
        for field, must_exist in (("required_absent", False), ("required_present", True)):
            for path, phrases in (cfg.get(field) or {}).items():
                if any(
                    (phrase in candidate.get(path, {}).get("content", "")) != must_exist
                    for phrase in phrases
                ):
                    raise DomainError("POSTCONDITION_FAILED", f"Unmet {field} condition in {path}")
        broken = sorted(set(broken_links(candidate)) - set(broken_links(base["documents"])))
        if broken:
            raise DomainError("BROKEN_LINK", "New broken internal links: " + "; ".join(broken))
        diffs = {}
        for path in sorted(set(candidate) | set(base["documents"])):
            previous = base["documents"].get(path, {}).get("content", "")
            content = candidate.get(path, {}).get("content", "")
            if content != previous:
                diffs[path] = "".join(
                    difflib.unified_diff(
                        previous.splitlines(True),
                        content.splitlines(True),
                        fromfile=path,
                        tofile=path,
                    )
                )
        payload = {
            "impact": {
                "base_revision": cfg["revision"],
                "changed_paths": sorted(diffs),
                "created_paths": sorted(set(candidate) - set(base["documents"])),
                "deleted_paths": sorted(set(base["documents"]) - set(candidate)),
                "preserved_paths": sorted(set(candidate) - set(diffs)),
                "replacement_occurrences": sum(
                    e.get("count", 1) for e in edits if e.get("operation", "replace") == "replace"
                ),
                "contract": {
                    k: cfg.get(k)
                    for k in (
                        "editable_paths",
                        "creatable_paths",
                        "required_absent",
                        "required_present",
                    )
                },
            },
            "documents": candidate,
            "index": build_index(candidate),
            "diffs": diffs,
            "edits": edits,
            "evidence": [known[e] for e in evidence],
            "validation": {
                "new_broken_links": [],
                "external_links_checked": False,
                "method": "exact-patch+internal-links-v1",
            },
        }
        async with self.sessions.begin() as session:
            await require_lease(session, lease)
            existing = await session.scalar(
                select(ChangeSet).where(ChangeSet.run_id == lease.run_id)
            )
            if existing:
                if existing.payload_hash != digest(payload):
                    raise DomainError("CHANGE_CONFLICT", "Run already has a different candidate")
                return {"id": existing.id, "status": existing.status}
            row = ChangeSet(
                id=str(uuid4()),
                run_id=lease.run_id,
                workspace_id=base["id"],
                base_revision=base["revision"],
                status="PENDING",
                payload=payload,
                payload_hash=digest(payload),
                summary=summary,
            )
            session.add(row)
            return {"id": row.id, "status": row.status}

    async def change(self, owner, cid):
        async with self.sessions() as session:
            row = await session.get(ChangeSet, cid)
            if row is None:
                raise DomainError("NOT_FOUND", "ChangeSet not found", 404)
            await self._owned(session, owner, row.workspace_id)
            return {
                "id": row.id,
                "run_id": row.run_id,
                "workspace_id": row.workspace_id,
                "base_revision": row.base_revision,
                "status": row.status,
                "payload_hash": row.payload_hash,
                "summary": row.summary,
                "published_revision": row.published_revision,
                **row.payload,
            }

    @retry_local_transaction
    async def decide(self, owner, cid, approve, expected_hash, key):
        from shopresolve.models import Run

        # Determine lock identity without holding a transaction across preparation.
        info = await self.change(owner, cid)
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(Run).where(Run.id == info["run_id"]).with_for_update()
            )
            w = await self._owned(session, owner, info["workspace_id"], lock=True)
            row = await session.scalar(
                select(ChangeSet).where(ChangeSet.id == cid).with_for_update()
            )
            idem = await self.idempotency._idem(
                session,
                owner,
                "doc.decision/" + cid,
                key,
                {"approve": approve, "hash": expected_hash},
            )
            if idem.response:
                return idem.response
            if row.payload_hash != expected_hash or digest(row.payload) != expected_hash:
                raise DomainError("CHANGE_CONFLICT", "Reviewed content changed")
            if row.status != "PENDING" or run.status != "WAITING_APPROVAL":
                raise DomainError("CHANGE_CONFLICT", "Change is no longer awaiting approval")
            state = copy.deepcopy(run.state)
            if approve and w.revision != row.base_revision:
                row.status = "STALE"
                state["answer"] = "工作区已更新，旧提案已失效。请基于当前版本创建新任务。"
                checkpoint(session, run, state)
                await self.runs._finish(
                    session, run, "FAILED", "UNRESOLVED", "STALE_REVISION", await db_now(session)
                )
            else:
                row.status = "PUBLISHED" if approve else "REJECTED"
                if approve:
                    number = w.revision + 1
                    session.add(
                        Revision(
                            workspace_id=w.id,
                            number=number,
                            documents=row.payload["documents"],
                            index=row.payload["index"],
                            content_hash=digest(row.payload["documents"]),
                        )
                    )
                    w.revision = number
                    row.published_revision = number
                state["results"] = [
                    {"changeset_id": cid, "status": row.status, "revision": row.published_revision}
                ]
                state["answer"] = (
                    f"已发布工作区版本 {w.revision}" if approve else "已拒绝修改，文档未变更。"
                )
                checkpoint(session, run, state)
                append_event(
                    session, run, "document.decision", state["results"][0], await db_now(session)
                )
                await self.runs._finish(
                    session,
                    run,
                    "SUCCEEDED",
                    "FULFILLED" if approve else "DECLINED",
                    None,
                    await db_now(session),
                )
            idem.response = {
                "id": cid,
                "status": row.status,
                "published_revision": row.published_revision,
            }
            return idem.response
