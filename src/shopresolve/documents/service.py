import copy
import difflib
import posixpath
import re
from pathlib import PurePosixPath
from uuid import uuid4

from rank_bm25 import BM25Okapi
from sqlalchemy import select

from shopresolve.errors import DomainError
from shopresolve.runstore import RunStore, append_event, checkpoint, db_now, require_lease
from shopresolve.service import CommerceService, digest, retry_local_transaction

from .models import ChangeSet, Revision, Workspace


def tokens(text):
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())


def check_documents(documents):
    if not 1 <= len(documents) <= 50:
        raise DomainError("DOCUMENT_LIMIT", "Import between 1 and 50 Markdown files", 422)
    total = 0
    for path, doc in documents.items():
        p = PurePosixPath(path)
        if (
            p.is_absolute()
            or ".." in p.parts
            or "\\" in path
            or ":" in path
            or str(p) != path
            or not path.endswith(".md")
            or len(path) > 160
        ):
            raise DomainError("INVALID_PATH", "Use relative .md paths without traversal", 422)
        if not isinstance(doc, dict) or not isinstance(doc.get("content"), str):
            raise DomainError("INVALID_DOCUMENT", "Document content must be text", 422)
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


class DocumentService:
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
        chunks = snapshot["index"]["chunks"]
        if not chunks or not query.strip():
            return []
        if exact:
            hits = [c for c in chunks if query in c["quote"]]
        else:
            corpus = [tokens(c["quote"]) or ["_"] for c in chunks]
            # Positive IDF for a small corpus; common terms must not all score zero.
            bm25 = BM25Okapi(corpus)
            scores = bm25.get_scores(tokens(query))
            ranked = sorted(range(len(chunks)), key=lambda i: float(scores[i]), reverse=True)
            hits = [chunks[i] for i in ranked if set(tokens(query)) & set(corpus[i])][:6]
        return [{**c, "version": f"{wid}@{number}"} for c in hits]

    async def prepare(self, lease, state, edits, evidence, summary):
        cfg = state["config"]["document"]
        base = await self.get(lease.owner_id, cfg["workspace_id"], cfg["revision"])
        known = {c["id"]: c for c in state.get("evidence", [])}
        if not evidence or not set(evidence) <= set(known):
            raise DomainError("EVIDENCE_REQUIRED", "Read and cite source evidence before proposing")
        if not 1 <= len(edits) <= 30:
            raise DomainError("INVALID_PATCH", "Provide 1 to 30 exact replacement edits")
        candidate = copy.deepcopy(base["documents"])
        for edit in edits:
            path, old, new = edit["path"], edit["old"], edit["new"]
            if path not in candidate or candidate[path].get("role", "document") != "document":
                raise DomainError("READ_ONLY", "Only target documents may be changed")
            if path not in state.get("read_paths", []):
                raise DomainError("READ_FIRST", "Read the target before editing")
            count = candidate[path]["content"].count(old) if old else 0
            if not count or count != edit["count"] or old == new:
                raise DomainError("PATCH_MISMATCH", "Exact occurrence count differs; reread target")
            candidate[path]["content"] = candidate[path]["content"].replace(old, new)
        check_documents(candidate)
        broken = sorted(set(broken_links(candidate)) - set(broken_links(base["documents"])))
        if broken:
            raise DomainError("BROKEN_LINK", "New broken internal links: " + "; ".join(broken))
        diffs = {}
        for path, doc in candidate.items():
            previous = base["documents"][path]["content"]
            if doc["content"] != previous:
                diffs[path] = "".join(
                    difflib.unified_diff(
                        previous.splitlines(True),
                        doc["content"].splitlines(True),
                        fromfile=path,
                        tofile=path,
                    )
                )
        payload = {
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
