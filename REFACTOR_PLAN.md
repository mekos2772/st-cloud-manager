# ST Cloud Manager Refactor Plan

## Purpose

This document captures a full refactor plan for `st-cloud-manager` based on a code scan of the current repository state.

The goal is not just to fix one broken route or one startup issue.
The goal is to make the system understandable, testable, and safe to extend.


## Executive Summary

The project currently works as a layered prototype that gradually accumulated production responsibilities:

- instance lifecycle orchestration
- runtime control for Docker and process modes
- reverse-proxy route generation
- API proxying
- Cloudflare integration
- activation key issuance
- trial queue management
- admin dashboard endpoints

Those concerns are not cleanly separated.
As a result, the codebase now has several recurring failure modes:

- one concept is represented differently in different modules
- one operation updates DB, files, runtime, and routing in different places
- path routing has more than one traffic entry path
- runtime-specific details leak into higher-level business logic
- fixing one issue often adds another compatibility branch instead of removing complexity

The codebase is not beyond repair.
It is refactorable.
But future work should now prefer structural cleanup over local patching.


## Current Snapshot

Recent refactor/fix work already moved the code in a better direction:

- `instance_model.py`
  Centralizes `domain`, `path_prefix`, and access URL normalization.
- `router_service.py`
  Centralizes route sync and runtime-mode selection for routing.
- `instance_repository.py`
  Centralizes core `instances` table persistence.
- `instance_service.py`
  Shares the creation pipeline between paid and trial instances.
- `path_proxy.py`
  Adds a manager-level `/st-*` fallback proxy.

This is useful progress, but the system is still only partially decomposed.


## What The Scan Found

### 1. `manager/app.py` is still too large

`app.py` currently mixes:

- lifecycle startup
- admin API
- activation API
- Cloudflare sync endpoint
- backup/restore
- API proxy endpoints
- `/st-*` fallback path routing
- static page serving

That means the HTTP layer still knows too much about internal orchestration.


### 2. `manager/instance_service.py` is still the main complexity sink

Even after the recent cleanup, it still contains:

- route resolution
- template variable construction
- lifecycle orchestration
- health checking
- trial queue behavior
- idle detection
- crash detection
- renewal
- deletion
- summary/statistics

That is too much for one module.


### 3. Runtime abstraction exists, but is incomplete

The project has two runtimes:

- `docker_service.py`
- `process_service.py`

They look like adapters, but they are not yet a strict interface.
Some behavior is still asymmetric:

- logging behavior differs
- route reload behavior is externalized
- health check semantics depend on host/path assumptions
- process mode relies on filesystem port markers
- Docker mode relies on container naming and labels

The service boundary is real, but not fully hardened.


### 4. Routing still has multiple truths

Today path-mode traffic can be influenced by:

- nginx-generated route files
- traefik-generated dynamic config
- manager fallback path proxy
- container/process local proxy.js

This is the single biggest architecture smell in the whole project.

Even if each piece is "reasonable", the combined system becomes hard to reason about.


### 5. Data model semantics improved, but are still fragile

The recent normalization fixed an important issue:

- `domain` should mean host only
- `path_prefix` should mean path only

That was the right direction.
But the current code still contains transitional compatibility logic for old rows and still relies on normalized dictionaries at runtime.

The long-term target should be:

- persisted schema semantics are strict
- runtime normalization is minimal
- no module should need to guess whether `domain` contains a path


### 6. Trial mode is still woven into instance lifecycle logic

Trial instances share most of the normal creation pipeline, but today trial behavior is spread across:

- queue handling
- activity heartbeat
- idle release
- resource gating
- expiry-like cleanup

This belongs in a dedicated `trial_service`, not mixed into the general instance orchestration module.


### 7. Repositories are only partially extracted

`instance_repository.py` now handles core instance persistence, which is good.
But we still have direct SQL embedded in service code for:

- trial queue
- summary/dashboard aggregation
- some activity updates
- some scheduler-related scans

The decomposition is started, not finished.


### 8. Config is still stringly-typed and spread across modules

System configuration currently lives across:

- `config.py`
- `settings_service.py`
- `cloudflare_service.py`

And most code still consumes it as untyped string dictionaries.

That creates a few recurring risks:

- inconsistent defaulting
- `"true"`/`"false"` string handling
- env-vs-DB precedence confusion
- duplicated field knowledge


### 9. The startup path still does too much implicit repair

At startup we now:

- init DB
- sync routes
- launch scheduler

This is helpful for recovery, but it also means startup is currently doing operational reconciliation without a formal bootstrap model.

That suggests the project needs an explicit "state reconciliation" concept, not just scattered repair logic.


### 10. Test coverage is not where the architecture risk is

There are currently not enough end-to-end safety rails around the areas that are repeatedly breaking:

- instance creation
- path routing
- route regeneration after restart
- trial queue transitions
- renewal / deletion lifecycle
- process vs docker parity


## Root Cause Summary

The core issue is not bad code quality in the narrow sense.
The core issue is boundary drift.

Over time the project let these concepts blur together:

- instance model
- runtime control
- routing control
- deployment reconciliation
- admin API surface
- trial behavior

Once those boundaries blur, every bug fix has to answer too many questions at once.


## Refactor Goals

The refactor should optimize for five properties:

1. One concept, one owner.
2. One route model, one URL model, one runtime contract.
3. Business logic does not know infrastructure details unless strictly necessary.
4. Startup and reconciliation are explicit.
5. New behavior can be tested without manually exercising the full stack.


## Target Architecture

### 1. Domain layer

Create a thin domain layer with explicit models and transforms.

Suggested modules:

- `instance_model.py`
- `settings_model.py`
- `route_model.py`

Responsibilities:

- normalize and validate persisted rows
- define canonical meaning of `domain`, `path_prefix`, `url`
- provide helpers for serialization and consistency checks


### 2. Repository layer

Move all DB access behind repository modules.

Suggested modules:

- `instance_repository.py`
- `trial_repository.py`
- `key_repository.py`
- `summary_repository.py`
- `settings_repository.py`

Rules:

- service modules do not issue raw SQL
- repositories return normalized domain objects or plain DTOs
- aggregation queries live with repositories, not orchestration services


### 3. Runtime layer

Formalize a runtime adapter contract.

Suggested interface:

- `create_instance_runtime(...)`
- `start_instance_runtime(...)`
- `stop_instance_runtime(...)`
- `restart_instance_runtime(...)`
- `remove_instance_runtime(...)`
- `get_logs(...)`
- `inspect(...)`
- `health_check(...)`
- `reconcile_runtime(...)`
- `get_route_target(...)`

Implementations:

- `docker_runtime.py`
- `process_runtime.py`

Rules:

- upper layers do not know about pid files, docker labels, or `.st_port`
- runtime implementations can still use those internals


### 4. Routing layer

Formalize route management as its own subsystem.

Suggested modules:

- `router_service.py`
- `nginx_router.py`
- `traefik_router.py`
- `manager_fallback_router.py`

Key idea:

- one route snapshot is produced from repository state
- one routing backend applies that snapshot
- fallback behavior is explicit, not accidental

Decision to make:

- either keep manager fallback as a safety net only
- or make manager the primary path-mode router

Do not let both evolve without a declared precedence rule.


### 5. Orchestration layer

Split lifecycle orchestration from everything else.

Suggested modules:

- `instance_orchestrator.py`
- `instance_admin_service.py`
- `trial_service.py`
- `reconciliation_service.py`

Responsibilities:

- orchestrator: create/renew/delete/start/stop lifecycle
- admin service: admin-facing operational actions
- trial service: queue, idle release, trial-specific policy
- reconciliation service: startup repair and periodic alignment


### 6. HTTP layer

Make `app.py` thinner.

Suggested shape:

- `routes_public.py`
- `routes_admin.py`
- `routes_trial.py`
- `routes_backup.py`
- `routes_proxy.py`

Rules:

- route handlers call services
- route handlers should not know SQL or file layouts
- route handlers should not contain infrastructure decision logic


## Recommended Refactor Phases

### Phase 0: Freeze semantics

Status: partially done

Complete these first:

- lock `domain` to host-only semantics
- lock `path_prefix` to path-only semantics
- remove remaining legacy ambiguity helpers over time
- document runtime-mode and routing-mode invariants


### Phase 1: Finish repository extraction

Scope:

- move trial queue queries into `trial_repository.py`
- move dashboard counts and summary queries into `summary_repository.py`
- move settings persistence behind `settings_repository.py`

Exit criteria:

- no service file contains ad hoc SQL except during transitional migration code


### Phase 2: Split instance orchestration

Scope:

- create `instance_orchestrator.py`
- move shared creation pipeline into orchestrator
- move renewal/delete/start/stop logic there
- reduce `instance_service.py` until it can be retired or become a compatibility facade

Exit criteria:

- the lifecycle path is readable top-to-bottom in one orchestrator module


### Phase 3: Separate trial mode

Scope:

- create `trial_service.py`
- move queue creation/processing there
- move idle release and heartbeat behavior there
- keep trial policy isolated from paid instance creation

Exit criteria:

- normal instance lifecycle and trial lifecycle no longer share a large module


### Phase 4: Formalize routing backend

Scope:

- define a route snapshot DTO
- make nginx and traefik consume that DTO
- explicitly decide how `manager_fallback_router` participates
- make route regeneration idempotent and inspectable

Exit criteria:

- one route snapshot can be logged, diffed, and applied


### Phase 5: Formalize runtime backend

Scope:

- define and document the runtime adapter contract
- remove runtime-specific conditionals from upper layers
- move restart/reconcile helpers into runtime implementations

Exit criteria:

- upper-layer services depend only on runtime interface, not implementation details


### Phase 6: Thin HTTP app

Scope:

- split `app.py` into route modules
- keep lifespan startup minimal
- move backup and admin handlers into dedicated route files

Exit criteria:

- `app.py` mostly wires routers and lifecycle hooks


### Phase 7: Add integration tests

Minimum matrix:

- create normal instance in path mode
- create trial instance in path mode
- route still works after manager restart
- renewal preserves access and API key
- delete cleans runtime + DB + route state
- process mode and docker mode both pass core flow


## Proposed Module Map

Target layout:

```text
manager/
  app.py
  routes/
    public.py
    admin.py
    trial.py
    backup.py
    proxy.py
  models/
    instance.py
    route.py
    settings.py
  repositories/
    instances.py
    trials.py
    keys.py
    settings.py
    summary.py
  services/
    instance_orchestrator.py
    instance_admin.py
    trial_service.py
    routing_service.py
    reconciliation_service.py
    api_proxy_service.py
  runtimes/
    base.py
    docker_runtime.py
    process_runtime.py
  routers/
    nginx_router.py
    traefik_router.py
    manager_fallback_router.py
```

This does not need to happen in one commit.
But this should be the intended end state.


## High-Risk Areas To Watch During Refactor

### Path routing

This remains the highest-risk area because it spans:

- DB state
- outer router state
- local proxy.js behavior
- manager fallback behavior

Any routing refactor must preserve a clear precedence order.


### Trial queue behavior

Trial mode currently piggybacks on normal instance lifecycle.
Be careful not to accidentally change:

- one-trial-per-IP policy
- queue ordering
- idle release semantics
- memory gating


### Runtime restart behavior

Process mode and Docker mode behave differently under restart and crash recovery.
Refactor should not assume parity before enforcing a contract.


### Backup/restore

Restore currently copies DB and users, then relies on startup/migration logic.
That is convenient but fragile.
Any repository refactor should keep restore compatibility in mind.


## Acceptance Criteria

The refactor should be considered successful only when all of these are true:

- new contributors can explain the architecture in one pass
- one module owns instance persistence
- one module owns routing sync
- one module owns lifecycle orchestration
- one module owns trial behavior
- `app.py` is not the main business-logic file
- path-mode creation works after restart without manual rescue
- runtime-specific details no longer leak into admin handlers
- the core lifecycle is covered by automated tests


## Rollout Strategy

Use an incremental rollout.
Do not attempt a full rewrite branch.

Recommended approach:

1. Introduce new module.
2. Redirect one code path to it.
3. Keep old wrapper temporarily.
4. Verify behavior.
5. Delete dead code only after the next step is stable.

This project is already live enough that "big bang" replacement would create more anxiety than clarity.


## Suggested Next Concrete Steps

If work continues immediately, the next best sequence is:

1. Create `trial_repository.py`
2. Move trial queue SQL out of `instance_service.py`
3. Create `summary_repository.py`
4. Move summary/dashboard SQL out of `instance_service.py`
5. Create `instance_orchestrator.py`
6. Move create/start/stop/restart/renew/delete there
7. Reduce `instance_service.py` to a compatibility facade


## Current Assessment

The project is refactorable and worth continuing.

The recent work already corrected some important structural mistakes:

- path-mode domain semantics
- route sync centralization
- instance persistence extraction
- creation pipeline reuse

But the codebase is still mid-transition.
It is not yet in the "easy to trust" state.

That means future effort should keep prioritizing structural simplification over feature growth until the lifecycle, routing, and trial paths are cleanly separated.
