# Upstream deer-flow Reference Plan

Reference survey of bytedance/deer-flow `main` (491 commits, `fe825520`→`540940ba`)
for **general_agent** `model_server` branch. Goal: identify and port the parts worth
referencing **while keeping the model_server subagent system as the architecture**.

## Deliverables

1. **Written analysis doc** — `docs/upstream-review.md` (this plan's companion): a
   self-contained comparison of upstream `main` vs `model_server` across the six
   chosen areas, with per-item fit rationale (what to adopt / adapt / skip).
2. **Implementation phases** below, each TDD-driven with backend tests.

---

## Area 0 — Subagent system: what to reference *while keeping model_server's architecture*

The model_server subagent system (EventBus, BudgetMonitor, AgentRegistry,
SSE bridge, yield/wait_for_tasks/follow_up, IDLE revival, nested depth-2,
message persistence) is **kept as-is**. The upstream subagent deltas that are
*compatible add-ons* — they plug into existing seams without restructuring:

### 0.1 Additive `stop_reason` + per-agent `token_budget` (upstream #3875 Phase 2, #3980, #3949)

Upstream replaced the coarse `max_turns_reached` status with an **additive**
`subagent_stop_reason` field (`token_capped` / `turn_capped` / `loop_capped`)
carried on `ToolMessage.additional_kwargs`, surfaced to the lead with a
`(capped: ...)` note in the result text.

- **Seam**: user's `subagents/status_contract.py` (109 lines) already has the
  `make_subagent_additional_kwargs` stamp. Extend it with
  `SUBAGENT_STOP_REASON_KEY` / `SUBAGENT_STOP_REASON_VALUES` /
  `format_subagent_result_message` while **retaining** `idle`/`interrupted`.
- **Seam**: user's `BudgetMonitor` (`subagents/budget.py`) counts LLM requests
  (soft/hard). Add a `consume_stop_reason(run_id)` contract on the user's
  `TokenBudgetMiddleware` and `LoopDetectionMiddleware` (mirroring upstream's
  `consume_stop_reason`), and have `SubagentExecutor._aexecute` collect the
  fired reason and stamp it on the result via the extended status contract.
- **Config**: add `token_budget` per-agent override next to `max_turns`.
- **Shared fixture**: extend `contracts/subagent_status_contract.json` with
  `stop_reason` values; add contract test pinning backend↔frontend.
- **Frontend**: `subtask-card.tsx` + `subtask-result.ts` read `stop_reason` to
  render "capped" states; keep old statuses working (additive).

### 0.2 Delegation ledger (upstream #3877)

System-maintained ledger of *all* prior `task` delegations rendered as model
context ("Work already delegated") to stop redundant re-delegation; reads the
structured metadata (including `stop_reason`).

- **Seam**: user already has `subagents/agent_registry.py` (live state) and
  `subagents/state_mirror.py` (persisted snapshot). Port upstream's
  `agents/middlewares/delegation_ledger.py` as a **pure helper**
  (`extract_delegations(messages)` + `render_delegation_ledger(entries)`) and
  inject it in `SubagentContextMiddleware.awrap_model_call` (or
  `DynamicContextMiddleware`), reusing the registry snapshot rather than
  scanning messages. HTML-escape included (upstream #4157).

### 0.3 Subagent step history (upstream #3779/#3845)

Persist per-tool **steps** (AIMessage + ToolMessage) as run events so users can
review what a subagent ran after reload — beyond the message feed they already
persist.

- **Seam**: user already has `subagents/message_persistence.py` +
  `RunEventStore.list_subagent_messages` (category `subagent_message`). Add a
  `subagent.step` event category + `capture_new_step_messages` (port upstream
  `subagents/step_events.py` — pure data shaping, dedup by id, cursor reset on
  summarization compaction) and persist steps alongside messages.
- **Frontend**: fetch-on-expand in the subagent conversation view.

### 0.4 Security in subagent contract

- html-escape subagent descriptions before the `<subagent_system>` block
  (upstream #4157).
- Prohibit `task` tool in general-purpose system prompt (upstream #4161).

### 0.5 Not adopting (recorded in the analysis doc)

`route subagents by net benefit` (#4384) — user's scheduler is cooperative
(yield/wait), a different model; `total delegation cap` (#4115) — user has
`count_active_children` + depth limits; `preserve parent checkpoint namespace`
(#4215) / `load user-scoped skills` (#4356) — user already isolates checkpoints
and has per-user subagent shadowing; `isolate callbacks + activate skills lazily`
(#4497) — reviewed, low value for user's execution model.

---

## Area 1 — Memory consolidation + staleness (into the existing monolithic memory)

User's `agents/memory/` is pre-refactor; **do not adopt** the pluggable
MemoryManager/DeerMem backend rework. Cherry-pick behaviors into
`updater.py` / `prompt.py` / `queue.py`:

1. **Consolidation** (upstream #3996): batch-LLM pass that merges fragmented
   facts into one synthesized fact before writing; respect
   `max_facts` / `fact_confidence_threshold`.
2. **Staleness review** (upstream #3860, #4143): LLM assigns per-fact
   `expected_valid_days` at write time; a review pass prunes silently-outdated
   facts and marks `staleFactsToExtend` for extension.
3. **Flush on graceful shutdown** (upstream #4181): flush the debounced queue
   on shutdown to prevent fact loss.
4. **Prevent task-scoped data** entering long-term memory (upstream #4604):
   filter messages tagged as task-scoped (subagent/tool-only turns) out of the
   memory-update candidate set.
5. **HTML-escape** memory fact content / context before prompt injection
   (upstream #4028/#4097/#4119/#4162).
6. **Queue fixes**: deferred single re-run flag instead of busy timer spin
   (#4073); duplicate-fact rejection inside create critical section (#4599).

**Config**: add `memory.consolidation` / `memory.staleness` fields (keep
existing schema fields untouched). Update `config.example.yaml` + bump
`config_version`.

**Tests**: extend `backend/tests/test_memory_updater.py` + new consolidation /
staleness / flush tests.

---

## Area 2 — read-before-write gate (upstream #3857/#3911)

Port `read_before_write_middleware.py` to user's middleware chain. Version gate:
modifying an existing file requires a prior `read_file` of the *current* version
(sha256 mark stamped on the `read_file` ToolMessage). Fail-open for
uninspectable content; per-(scope, path) lock serializing gate check + mutation.

- **Hook**: `build_lead_runtime_middlewares` (add near the tool-error/audit
  group; also included for subagents via `build_subagent_runtime_middlewares`).
- **Config**: `read_before_write.enabled` (default off to preserve behavior;
  on for new threads).
- **Dependency**: user's `tool_result_meta`-style `normalize_tool_result`
  helper (Area 3.1) is required by the gate's fail-open path.
- **Tests**: duplicate-append scenario regression + same-turn concurrent writes.

---

## Area 3 — Tool result meta + progress + sanitization

### 3.1 `tool_result_meta.py` (upstream #3601 family)

Port `ToolResultMeta` (status/error_type/recoverable_by_model/
recommended_next_action/source) + `normalize_tool_result`; stamp
`deerflow_tool_meta` in the user's `tool_error_handling_middleware.py` (283
lines) — this is the foundation both read-before-write and tool-progress read.

### 3.2 `tool_progress_middleware.py` (upstream RFC #3177)

Port the ACTIVE→WARNED→BLOCKED stagnation state machine per (thread, tool),
reading `deerflow_tool_meta` (no text parsing). Add
`tool_progress.enabled` config.

### 3.3 `tool_result_sanitization_middleware.py` (upstream #4002/#4099)

Neutralize prompt-injection control tokens (`<system-reminder>`, `--- END USER
INPUT ---`) in **remote-content tool results** (`web_fetch`/`web_search`/
`image_search`/`web_capture`) using the same `neutralize_untrusted_tags`
as `input_sanitization_middleware`; leave local tool output untouched.

### 3.4 `tool_output_synopsis.py` (upstream #3377)

Structured head/tail synopsis for oversized tool output (bounds the model-visible
footprint of large file reads/web results). **Lower priority** — optional phase.

---

## Area 4 — Security / CVE fixes

1. **Dependency bumps** for the four CVEs (backend `uv.lock`, frontend):
   - CVE-2026-49476, CVE-2026-49477, CVE-2026-35209, CVE-2026-33128.
   - Identify the affected packages by running `uv audit` / `npm audit`, bump,
     run backend + frontend suites.
2. **HTML-escape SOUL.md** (upstream #4137) before injection.
3. **Escape untrusted skill metadata** (upstream #4128) before it reaches the
   model prompt.
4. **Block forged framework tags** in the input guardrail (upstream #4155).
5. **Harden auth next-path** (upstream #4587) + CSRF cookie lifetime (#3872).
6. Verify user-side equivalents exist; where user already sanitizes, add tests
   pinning the behavior.

---

## Area 5 — Frontend UX (user-facing)

Pick from upstream's frontend deltas (all additive, no architectural change):

1. **Voice dictation** (upstream #4036) — mic input in composer → transcribed
   to text via Web Speech API / Whisper endpoint.
2. **Pin recent chats** (upstream #4442) — pinned threads in the sidebar.
3. **Composer drafts** (upstream #4282) — per-thread draft persistence in
   localStorage.
4. **Artifact inline editing** (upstream #4596) — edit artifact content inline
   and re-submit to agent.
5. **Context-window usage** — user *already* added `context_window` display;
   no action.

Each gated behind a feature flag (`localStorage`), `pnpm check` clean, unit
tests in `frontend/tests/`.

---

## Execution order

1. **Written analysis doc** (`docs/upstream-review.md`) — captures the full
   comparison + fit rationale for all six areas (deliverable #1).
2. **Phase A — subagent contract add-ons** (Area 0): stop_reason + token_budget,
   delegation ledger helper, step-history events, html-escape.
3. **Phase B — tool middleware** (Areas 2–3): tool_result_meta →
   read-before-write → sanitization → tool_progress.
4. **Phase C — memory** (Area 1).
5. **Phase D — security/CVE** (Area 4).
6. **Phase E — frontend UX** (Area 5).

Each phase: tests first, then `make test`, `make lint`, `make format`
(backend) / `pnpm check` (frontend), and AGENTS.md/README doc updates per repo
policy.

---

## Out of scope (documented, not executed)

E2B/BoxLite/Tenki sandbox providers, RBAC/authz, Helm chart, pluggable
MemoryManager/DeerMem backend rework, upstream's `configured_extensions` /
`durable_context` / `mcp_routing` / `skill_tool_policy` / `terminal_response`
middlewares (user has equivalents or different architecture), checkpoint
DeltaChannel dual-mode (Postgres-only; user's storage choice differs),
IM channel additions (GitHub webhook, DingTalk, Telegram fixes).
