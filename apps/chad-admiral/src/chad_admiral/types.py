"""Admiral-tier data contracts.

These implement the two schemas HUB_ARCHITECTURE.md § 5 marked TO-BUILD:
CaptainDossier (admiral -> captain, FROZEN) and EscalationPacket
(captain -> admiral -> user, reply routed by correlation_id).

Field mapping to the existing AgentOps ContractKernel CaptaincyLease
(~/automation_architecture/src/schemas.ts:274 CaptaincyLeaseSchema):
    dossier.captain_id          -> lease.captainId
    dossier.repo_path           -> lease.scope (LeaseScope) / allowedMutationRoots
    dossier.allowed_tools       -> (lease has no tools field yet; net-new here,
                                    will fold into the tool/MCP registry, build-surface #2)
The dossier = the lease + the discovered context. In Slice 1 the lease side is
deferred (ContractKernel wired in Slice 4); the dossier carries the context.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TaskItem(BaseModel):
    """One line of Chad's intake list. Parsed deterministically from chat text."""
    task_id: str
    title: str                       # the work, <=80 chars
    repo_hint: str                   # repo name/path as written by Chad
    raw: str                         # original bullet, verbatim


class DiscoveryResult(BaseModel):
    """What the admiral learned touching the repo for one task (Slice 1: lightweight)."""
    task_id: str
    repo_path: Optional[str] = None  # resolved absolute path, or None if not found
    git_head: Optional[str] = None   # short sha of HEAD, or None
    gaps: list[str] = Field(default_factory=list)  # things the admiral can't resolve itself


class CaptainDossier(BaseModel):
    """admiral -> captain contract. FROZEN once spawned (HUB_ARCHITECTURE D3)."""
    task_id: str
    omni_mem_thread_id: str          # drill-down handle to all raw discovery
    task_brief: str                  # what + (later) acceptance criteria
    repo_path: str                   # current code location
    rlm_ref: Optional[str] = None    # repo-language-map ref (Slice 1: git HEAD sha)
    resolved_clarifications: dict[str, str] = Field(default_factory=dict)  # Gate-A answers, baked in
    coding_principles_ref: str = "~/.claude/CLAUDE.md"
    allowed_tools: list[str] = Field(default_factory=list)
    track_id: Optional[str] = None   # the auto_runtime track this captain IS (D7)


class EscalationPacket(BaseModel):
    """captain -> admiral -> user. Reply travels back down by correlation_id (D6).

    Defined now so Slice 3 (escalation routing through the hub) has a stable contract.
    Sibling to the Evidence/Closure packets in fleet-orchestration-doctrine.md.
    """
    correlation_id: str              # routes the reply to the exact captain/slice
    omni_mem_id: str                 # drill-down to full context
    summary: str                     # self-contained; admiral triages without re-reading
    problem: str                     # the specific blocker / question
    context: str                     # what's needed to decide
    parked_slice_set: list[str] = Field(default_factory=list)
