# Follow-ups

A running log of small nuances, rough edges, and "look into later" items found while
working — things not worth interrupting the current task for, but worth not forgetting.

Format: one entry per item. Keep it short — area, what was observed, why it might matter.

---

## 2026-07-06 — Investigate static runner orphan removals with lease-lookup diagnostics

**Area:** `scale_down` unused-runner cleanup + `dedicated_static` lease reconciliation (`get_server`/`reconcile_runner_leases`)

**Observed:** Unused static runners can be removed with `runner_server_found=False` / `provider=none`, which indicates lookup miss at cleanup time but does not explain whether the static host identity was unknown or the lease cache was cleared transiently.

**What we added:** Debug instrumentation now logs:
- dedicated_static reconcile summary (`hosts`, `matched`, `cleared`, seen static runner count)
- orphan static runner names from GitHub that are not in configured host identities
- dedicated_static `get_server` misses where static host exists but lease is not set
- per-provider lookup outcomes in scale_down before runner removal, plus runner status/busy/labels

**Next step:** Capture a full cycle where a suspected runner is removed and correlate `reconcile` + `get_server miss` + `provider_lookups` lines to determine whether the root cause is identity mismatch vs lease-cache clearing timing.

---

## 2026-06-26 — `supports_recycling` flag conflates two meanings (naming + strategy)

**Area:** `CloudProvider.supports_recycling` (Hetzner `True`, AWS `False`, dedicated_static `False`)

**Observed:** The single boolean gates the power-off → rename → rebuild/power-on *parking-and-
reuse* loop (4 sites in scale_down, 1 in scale_up). Two issues: (1) the name collides with the
shipped dedicated_static "recycling" feature (which runs `recycle.sh` and is `supports_recycling
= False`) — "the recycling provider doesn't support recycling"; (2) `False` encodes two different
reasons — AWS = capability *gap* (`rebuild_server` + reuse wiring unimplemented; could plausibly
do stop/start reuse later), static = *deliberately N/A* (always-on, reuse in place).

**Decision direction (deferred):** Don't build a granular capability framework speculatively —
all call sites ask one yes/no today, and the `NotImplementedError` power/rebuild methods already
mark capability gaps. Plan: rename to a behavior name (candidate `reuses_stopped_servers`; "park"
rejected as jargon, bare "reuse" rejected — static reuses hosts too) + per-provider docstrings
stating the reason. **Pivot question for when we resume:** is making AWS reuse servers via
stop/start on the roadmap? If yes, do the minimal granular split (separate "can rebuild" from
"participates in reuse pool"); if no, the single rename suffices.

---

## 2026-06-26 — SHIPPED: dedicated_static recycling via recycle.sh setup-step (`c758181`, `7ef6d48`)

**Area:** `CloudProvider.setup_script_name` + `DedicatedStaticCloudProvider` + `scale_up`

**What shipped:** static's per-lease setup-step now runs `recycle.sh` (cleanup) instead of
`setup.sh`, since static hosts are provisioned out of band. `setup_script_name()` is a
polymorphic provider method returning the script filename; static reads a `recycle-<name>`
label (else `recycle.sh`) and ignores `setup-`, cloud reads `setup-` (else `setup.sh`). A
mixed-fleet job may carry both labels with no conflict. `scale_up` joins + `check_setup_script`s
the name. `get_setup_script` removed (dead). Full design + dropped alternatives in
`recycling-notes.md`.

**Dropped from this line of work:** the `supports_recycling=True` / recycle-loop participation
path (no-op in scale_up for static; crashes scale_down's `_native` park path) and the deferred
scale_down `recycle_server` provider-interface refactor. Neither is needed for static recycling.

**Still pending (unrelated):** the claim-marker prod watch below; modular per-provider config
validation; the `image-` swallow.

---

## 2026-06-25 — Residual stale-claim reclaim race (low) — after 1ff1baf

**Area:** `providers/dedicated_static._try_claim`

**Observed:** Claim acquisition is now atomic (`mkdir` lock dir — one racer wins, others get
EEXIST), closing the cross-process fresh-claim race. But reclaiming a *stale* claim (one
orphaned by a crashed/abandoned setup, older than `claim_timeout`) does `rmdir` + `mkdir`, which
is not atomic: if two controllers find the same claim stale at the same instant, one can clobber
the other's fresh reclaim.

**Why it might matter / fix direction:** narrow window — needs an orphaned claim *and*
simultaneous reclaim by two controllers (already an unusual mode). Self-heals next cycle. A
fully-atomic expiry would need rename-based CAS or a lock with TTL; not worth it unless
multi-controller becomes a normal operating mode.

---

## 2026-06-24 — BUG (RESOLVED 2026-06-24): static host double-dispatched during the setup window

**Area:** `providers/dedicated_static.reconcile_runner_leases` + `scale_up` cycle ordering

**Observed:** The dedicated_static lease has only two states (`lease_name` None / set), and
`reconcile_runner_leases` treats live GitHub runner registration as the sole source of truth.
It runs at the top of every scale_up cycle (scale_up.py:1665), *before* the server list is
built (1671), and clears any lease whose `static_name` is not currently a registered runner.
But `server_setup` (wait_ssh → setup.sh → recycle/startup → `config.sh` register) runs async
in `setup_worker_pool` and is never awaited by the cycle (the cycle only `.result()`s the fast
`worker_pool` create/recycle calls at 2008, then sleeps). So while a freshly-leased host is
still being provisioned — minutes for a cold host — the next cycle's reconcile clears its
lease, the in-flight host becomes invisible to capacity accounting (`futures` is reset per
cycle, 1604), and the still-queued job re-dispatches onto the *same* host. Repeats each cycle
until the runner finally registers.

**Impact:** repeated re-provisioning of a host mid-setup, concurrent `config.sh --replace` on
one box, wrong capacity math, jobs left unserved. Masked for warm hosts (fast idempotent setup
registers within one interval); bites cold hosts / short intervals / slow setup. **Recycling
widens the window** (adds recycle.sh time), so this should be fixed before/with recycling.

**Fix direction (first attempt, REJECTED):** an in-memory `pending` lease state with a timeout.
Rejected because in-memory state does not survive a service restart and is not reconstructible
from any durable source — it violates the "memory is a cache only" rule. Superseded by the
durable claim marker below.

**Resolved 2026-06-24 (durable claim marker):** The fix derives lease state from durable
sources, not memory. A host carries a claim marker (`~/.github-runner/claim`) whose
presence-and-mtime is the source of truth for "setup in flight": `create_server` optimistically
takes an in-memory lease, then (outside the lock) checks the marker via `find -mmin` exit codes
and `touch`es it to claim — skipping any host with a *fresh* marker (a live or crash-orphaned
setup) and retrying the next. `reconcile_runner_leases` stays purely GitHub-derived (active
lease ↔ registered runner); the marker, not reconcile, covers the pre-registration window.
`server_setup` releases the claim in a `finally` (`CloudProvider.release_claim`): cleared on
success, cleared + lease freed on failure. Marker freshness window = `max_server_ready_time +
max_runner_registration_time` (wired via the provider factory). Memory holds only a cache;
across restart the marker is re-read at claim time, and a crash-orphaned marker ages out after
the window and is reclaimed. Validated with a 6-scenario mocked-SSH check.

Known small edges (acceptable, not yet addressed): (a) sub-second probe→`touch` window if
reconcile clears the optimistic lease at that instant — self-heals via `config.sh --replace`;
(b) a setup exceeding the freshness window would let the marker go stale mid-setup — mitigated
by a generous window, could add a heartbeat `touch` later; (c) marker clear-on-success needs
SSH, so a host that dies right after registering keeps a stale marker until it ages out.
Note: `scale_up` and `scale_down` each build their own provider instances (independently via
`config.provider_factory`), so static lease state is per-loop — `scale_down` never holds claims.

**WATCH IN PROD (mock-tested only):** the claim-marker SSH path — `find -mmin` exit-code probe,
`mkdir -p`/`touch`, `rm -f`, `~` expansion under the SSH login shell, and `find`/`grep`
availability — has only been exercised against mocked SSH. The branch is being run in prod
directly (no main merge). First validation to do on a real static host: lease one, confirm the
log shows the `find`/`touch`/`rm` calls, the marker lands at `~/.github-runner/claim`, and a
queued job is not double-dispatched while the host is mid-setup. If `~` doesn't expand or a
tool is missing, claims will silently fail (host treated as unclaimable → skipped).

**Note — investigated and NOT a bug:** the adjacent worry that an *unused* static runner gets
its lease cleared while staying registered (→ reconcile re-leases forever) does not happen:
scale_down's unused-runner path sets `runner_server = None` in both branches (scale_down.py:779)
so `repo.remove_self_hosted_runner` always fires (788), deregistering the runner; reconcile then
clears the lease cleanly.

---

## 2026-06-24 — Modular per-provider config validation (not a priority)

**Area:** `config/parse.py` (`parse_config`)

**Observed:** All provider config-file validation is inline in one monolithic `parse_config`
(hetzner ~482-502, aws ~504+, dedicated_static ~607+). Provider *classes* only do runtime
validation (`validate_labels`, raising at `get_server_type`/`create_server`); they never see
the raw YAML or `label_prefix`. So provider-specific config rules live away from their
providers, and the function grows with each provider.

A concrete symptom surfaced while fixing the `label_prefix` type-label bug: the
`label_prefix → "<prefix>-type-"` normalization now exists in **both** `get_server_types`
(scale_up.py) and `parse_config` (parse.py) — a DRY violation that can drift.

**Why it might matter / fix direction:** consider delegating config validation to each provider
(e.g. a `Provider.validate_config(raw, context)` that receives `label_prefix`/`meta`), and
extracting a shared `type_label_prefix(label_prefix)` helper used by both the parser and
`get_server_types`. Low priority — current structure works; this is cleanliness/coupling.

---

## 2026-06-24 — BUG (RESOLVED 2026-06-25): keep-warm of static hosts re-resolves the provider by type

**Area:** `scale_up` "Maintaining dedicated static runners" loop → `create_runner_server` →
`get_server_types` / `_resolve_provider`

**Observed:** The keep-warm loop iterates a provider's own configured static hosts
(`for _p in providers: if _p.name == "dedicated_static": for configured_host in _p.list_servers()`)
but then calls `create_runner_server(name=configured_host.name, labels=<host labels>)`, which
resolves the provider afresh from the labels' `type-*` (first recognizer, cloud-first). It is
**not pinned to `_p`**. So if a static host's labels carry a cloud type (e.g. via a shared meta
expanding to `type-cx23`), keep-warm resolves to Hetzner/AWS and calls `create_server(name=
<static_name>)` on a cloud provider — **provisioning a real cloud VM named after the static
host** while the static host stays idle.

**Why it matters:** the static-maintenance loop can spin up cloud VMs; static hosts never get
warmed. Masked only when static host labels resolve uniquely to the static provider.

**Resolved 2026-06-25:** `create_runner_server` gained a `provider` param; when set it resolves
each type against only `[provider]` (a one-element candidate list — `_resolve_provider` itself is
unchanged), so an unsupported type raises and is skipped rather than diverting to a cloud
provider. The keep-warm loop passes `provider=_p`; on-demand/standby pass `None` (resolve across
all). The static type leases the host; cloud-type labels are skipped (no cloud VM). The pinning
primitive (resolve against a one-element list) is already covered by the existing
`resolve_provider_single_match` / `no_match_raises` tests.

**Still open — related residual (after 557d96b):** keep-warm still *attempts* a host that's busy/mid-setup
each cycle — create_server now returns None and the attempt cancels quietly (no error), but the
`🍀 Trying to create / Validating / Creating …` info-line cascade still prints. The proper fix
is the same as above: keep-warm should target the specific host and skip when it isn't
claimable, rather than running the generic resolve→create path and bailing. Until then, the
info cascade is cosmetic noise (worse at short intervals; fine at 90s+).

---

## 2026-06-24 — RESOLVED — `get_runner_server_name` truncated hyphenated static groups

**Area:** `server.get_runner_server_name` vs dedicated_static `static_name`

**Observed:** `get_runner_server_name = "-".join(runner_name.split("-")[:5])`. A static name is
`github-runner-static-{group}-{endpoint_hash}`; it only round-trips if `{group}` is a single
hyphen-free segment. A hyphenated group (e.g. `my-group`) shifts segments and truncates the
hash, so `provider.get_server(get_runner_server_name(...))` won't find the host.

**Considered and rejected — rsplit:** the runner suffix is `-<type>-<location>`, but AWS
locations are themselves hyphenated (`us-east-1a`), so a fixed-segment `rsplit("-", 2)` would
slice the region. The default `[:5]` already tolerates hyphenated locations (they sit past
field five and are dropped) — it only ever broke for static.

**Considered and rejected — move to a provider method:** it is the inverse of `build_runner_name`
(a provider method), so moving it looked symmetric. But several callers (metric and log labels)
have no provider in hand, and a default-truncated static name in logs hurts readability. It also
dispatches purely on the name prefix, so a provider method would be redundant. Kept as a free,
convention-aware helper alongside `get_runner_server_type`.

**Resolved 2026-06-24 (commit a4501c4):** added a prefix guard — a static runner registers under
its bare, stable name with no `-<type>-<location>` suffix, so the runner name *is* the server
name; return it unchanged. Default path untouched. Tests in `scale_up_helpers` cover default
strip, hyphenated AWS location, and static single/multi-segment groups.
