# Conformance — `langsys-django`

| | |
|---|---|
| **SDK** | `langsys-django`, the Django binding over the `langsys` Python core |
| **Spec revision read** | langsys2 5cff03a1…, docs/sdk-spec.mdx blob 5c5c0723f88fb8e6b13f58876c7adca8b6b35691 |
| **Profiles** | server, binding, all — derived: binding over langsys-python |
| **specVersion** | 8.0.1 |
| **Spec pin, re-derived** | `git -C ~/Documents/dev/langsys2 rev-parse 5cff03a17751e7dae9dcf1af52a9454d027c9006:docs/sdk-spec.mdx` → `5c5c0723f88fb8e6b13f58876c7adca8b6b35691`, re-derived on 2026-09-12 when this file was written. It is pinned by commit, never by branch, and `test_the_pinned_rule_list_is_the_spec_blob_itself` re-derives it on every run where langsys2 is present |
| **SDK revision** | branch `feature/838_write_key_gating`, cut from `main` @ `34a6a87` |
| **Core consumed** | The `langsys-python` working copy on `feature/838_write_key_gating`, through a local symlink excluded from git and an editable install. The binding needs the core's request scopes, which it has from `1229ca2`; the suite was last run against that commit's clean tree, all 97 passing. Delegated rows are graded against the core's conformance file as of that commit (last changed at `506dd86`), which cites the same spec blob |
| **Published** | Never |
| **Suite** | 97 tests in 8 files, all passing. `ruff` and `mypy --strict` are clean |
| **Runtime** | Django 4.2.30 on CPython 3.9.6 |

**The spec never names Django.** It covers this package only as a "framework variant" of the server
SDKs, so the profile above is derived: a binding (the Profiles table's definition) whose core is a
server SDK. Reviewer has flagged the gap to the operator. Every profile-based `n/a` below is checked
against its rule's own Profiles line by `test_profile_rows_agree_with_each_rules_profiles_line`.

**Evidence tiers follow Reviewer's fleet-wide addendum.** `live`, `contract` and `mock` apply only
where the governed property depends on what the API answers. Everything this binding owns is an
in-process property: when the core's flush and reset run relative to Django's lifecycle, which
locale a render sees, what the tag hands the core, and what the code does not contain. So every
binding-owned row is `n/a (pure)`. Every property that does depend on the API is the core's, and its
row is `delegated` with tier `-` and the core's own grade named in the evidence.

## What surfaced while writing this

Twelve things. The first four were Reviewer's read-only findings at `34a6a87`, and each was
reproduced as a red test before it was fixed. The tenth was measured in the FastAPI lane first, and the
eleventh was asked for by Reviewer after the Rails lane reproduced it. The rest were found by running
code, not by reading it.

1. **The middleware decided capability itself (BIND-2, GATE-2).** `auto_flush and client.can_write`
   collapsed an unreachable authorize into "no" and called `clear_pending()`, discarding a queue the
   core would have held. The measurement was zero POSTs and an empty queue. *(Reviewer.)*
2. **The write decision was never reset at the request boundary (GATE-3).** A later request acted on
   an earlier request's observed decision, and the OBS-1 notice fired once per process instead of
   once per request. *(Reviewer.)*
3. **Registration ran on the request path (SRV-3, REG-3).** The flush ran inside the middleware,
   before the response was returned. *(Reviewer.)*
4. **`AUTO_FLUSH` was a binding-only discovery switch (BIND-4).** Setting it to `False` silently
   discarded every queue, and it named something different from the core's `auto_flush`, which is an
   exit hook. *(Reviewer.)*
5. **A streamed response rendered in the base language, and its misses were never sent.** The
   middleware reset the locale variable when `__call__` returned, and a `StreamingHttpResponse` body is
   rendered after that. The inline flush had already run by then with nothing queued, and nothing
   flushed afterwards. Found while writing the SRV-1 test.
6. **`{% t %}` turned an undefined template variable into `""` (BIND-1, ICU-2, ICU-3).** Django
   resolves a missing variable to `string_if_invalid`, so the core received a present, empty
   argument. That rendered `Hi !` where the core renders the visible gap `Hi {name}!`, and for a plural
   it rendered the raw ICU source, which is the outcome the ICU family exists to prevent. Measured
   through the tag with a recording client, then fixed with a tag node that resolves keyword arguments
   with `ignore_failures=True`.
7. **The core's debounce breaks SRV-3's order of events.** Through Django's handler, with the core
   default of 0.4s, a render that continued past the debounce produced
   `posted, rendered, response-returned`. A concurrent request's post-response flush can also send
   another request's misses mid-render, because the core's queue is process-wide. Neither is fixable
   from a binding without taking over the core's scheduling. Ruling (c): the core grows a request-scope
   seam, routed by Reviewer to the Python lane. The core's request scopes (`1229ca2`) close it, and the
   middleware opens one for every request.
8. **Two of my own tests could not fail at first, and mutation is what showed it.** The SRV-2
   concurrency test used one barrier and stayed green against a process-global locale: the request that
   finished first restored the other's value on its way out, so the shared value was never observed. It
   now uses two barriers. The first debounce order test raced a sleep against the core's timer and
   passed once in a full run; it now waits on the POST itself.
9. **Two gaps in my own probes.** After the tag fix, the `catalog-read` probe matched Django's
   `var.resolve(context)`; it is now narrowed to the core's function form `resolve(`. The probes also
   read the repo path rather than the imported package, so a mutation could not make one fire; they now
   read the package as imported.
10. **`t()` and `{% t %}` cannot pass a placeholder named `category` or `phrase` (BIND-1).** The
    binding's `t(phrase, category=None, **params)` shape takes both names for itself, while the
    core's `translate` takes `params` as a dict and has no collision. `t("Browse {category}",
    category=name)` silently uses the value as the catalog category: the placeholder is served
    unfilled and the phrase queues under that category. Passing a category and `category=`
    raises `TypeError`, which in a template is a render-time error. Measured in the FastAPI lane
    first and confirmed here. It is not fixed, because the shape should be one decision across the
    core and both bindings.
11. **No response says it varies by locale (BIND-4).** The middleware negotiates from the cookie and
    `Accept-Language`, so the same URL serves two languages, yet no response carries `Vary`. A shared
    cache in front of the site can then serve one visitor's language to the next, as the Rails lane
    reproduced on a CDN. Measured here by `test_gap_accept_language_negotiation_sends_no_vary_header`
    and `test_gap_cookie_negotiation_sends_no_vary_header`, each with the control that the two
    responses really differ. Not fixed: it is part of the ambient-locale question the operator is
    ruling on.
12. **A live core tree is not a pinned one.** One full run here went red on SRV-2, with both requests
    receiving a mix of both languages, while the Python lane's mutation harness was rewriting core
    source files in place. The test passed five times alone, and in the full suite, once the files were
    restored. The failure is consistent with the core being mid-mutation, but which mutation was
    active is not recoverable. Reviewer made isolated-copy mutation runs a fleet norm, and the core's
    harness has run on an isolated copy since `506dd86`.

## Summary

Counted from the status table by `test_the_summary_counts_are_the_tables_counts`. It fails if the
table and these numbers disagree.

| Status | Count | |
|---|---|---|
| `implemented` | 13 | GATE-3, REG-3, SRV-1, SRV-2, SRV-3, BIND-2, BIND-3, BIND-5, BIND-6, WIRE-5, CONF-1–3 |
| `partial` | 2 | BIND-1: the `category`/`phrase` keyword collision waits on one signature decision across the core and both bindings. BIND-4: three locale-negotiation keys, and the missing `Vary` header, wait on the operator's ambient-locale ruling |
| `delegated` | 42 | core-owned behaviour, each with an absence probe and a firing control |
| `n/a (profile)` | 20 | browser-only rules |
| `n/a (architecture)` | 2 | SRV-4, SRV-5 |

**Delegated rows whose core row is not green at 8.0.1.** The binding cannot close these, and they are
listed rather than left to be found. Reviewer's ruling: the checker resolves each delegated row against
the core's current grade, so these count red for this lane until the core's rows are green. They stay
`delegated` because the binding takes part in none of them:
- Not green in the core's conformance file at `506dd86`: GATE-7, TOK-3 and TOK-4 (`partial`), and
  TOK-2 (`held (strip ruling)`).
- Provisional there, on mock evidence: GATE-2, GATE-5, REG-8 and REG-9.

## Status

| Rule | Status | Tier | Evidence |
|---|---|---|---|
| GATE-1 | delegated | - | core `GATE-1` (implemented, live). Probe `capability` matches nothing in this binding and fires on core `client.py` and on this binding's `middleware.py` at `34a6a87`, which branched on `can_write` |
| GATE-2 | delegated | - | core `GATE-2` (**provisional**, mock): the core holds the queue when capability is unknown. Probe `queue-mutation` matches nothing here and fires on core `client.py` and on `middleware.py` at `34a6a87`, whose `clear_pending()` discarded a held queue. End to end, `test_BIND2_capability_unknown_holds_the_queue_through_the_binding` was red at `34a6a87` (finding 1), and mutation M1 reddens it |
| GATE-3 | implemented | n/a (pure) | The core's declared wrapper obligation. On `request_finished` the binding calls the core's `reset_write_decision()` after the flush, so the flush uses the decision this request observed and the next request starts without it. `test_GATE3_an_observed_write_decision_does_not_survive_the_request` covers the latch, on the discriminating vector where `key_type` and `write_enabled` disagree. `test_GATE3_the_unusable_capability_notice_rearms_at_each_request` uses a legitimate `ip_write` answer. Both were red at `34a6a87`, and mutation M3 reddens both. No carve-out is taken |
| GATE-4 | delegated | - | core `GATE-4` (implemented, n/a (pure)). Probe `cache`: this binding writes no cache, so it has nothing to strip. It fires on core `catalog.py` |
| GATE-5 | delegated | - | core `GATE-5` (**provisional**, mock; the core keeps no "already registered" store). Probe `queue-mutation`: this binding keeps no queue or bookkeeping. It fires on core `client.py` and on `middleware.py` at `34a6a87` |
| GATE-6 | delegated | - | core `GATE-6` (n/a (architecture): no report lane exists, per HINT-2). Probe `registration-request` shows no registration is constructed here and fires on core `registration.py`. Probe `report-lane` shows there is no report lane and fires on the TypeScript core's `api.ts` and on a synthetic call. Choosing the lane is the core's alone. |
| GATE-7 | delegated | - | core `GATE-7` (**partial**, n/a (pure)). Probe `catalog-read`: this binding never reads the catalog, so it cannot detect a miss the core does not see. It fires on core `client.py`. Every entry point this binding adds reaches the core: `test_GATE7_every_entry_point_reaches_the_core_queue` covers the tag, filter and helper, and `test_SRV3_a_streamed_body_is_complete_before_its_misses_are_sent` covers a streamed render. Mutation M7 reddens the first. This row delegates to a core row that is not green |
| GATE-8 | delegated | - | core `GATE-8` (implemented, n/a (pure)). Probe `capability`: nothing here reads `write_enabled` or `key_type`, so there is no absence to misread. It fires on core `client.py` and on `middleware.py` at `34a6a87` |
| CAT-1 | delegated | - | core `CAT-1` (implemented, n/a (pure)). Probe `catalog-read` matches nothing here and fires on core `client.py` |
| CAT-2 | delegated | - | core `CAT-2` (implemented, n/a (pure)). Probe `catalog-read` matches nothing here and fires on core `client.py` |
| CAT-3 | delegated | - | core `CAT-3` (implemented, n/a (pure)). Probe `block-identity`: there is no content-block handling here. It fires on core `client.py` |
| REG-1 | delegated | - | core `REG-1` (implemented, live). Probe `registration-request`: this binding constructs no registration. It fires on core `registration.py`. Through the handler, `test_SRV3_a_read_only_key_pushes_nothing` shows the request hook opens no send path around the core's gate |
| REG-2 | delegated | - | core `REG-2` (implemented, n/a (pure)). Probe `scheduling`: there is no timer, sleep or debounce here. It fires on core `client.py`. The core's debounce is left as the core configures it; inside a request, the core's request scope holds a miss until the response is out (SRV-3) |
| REG-3 | implemented | n/a (pure) | The core's declared wrapper obligation. The core's public `flush_pending()` runs on every `request_finished`, not only at process exit. `test_REG3_the_queue_is_registered_at_the_end_of_every_request` runs with no debounce, no exit hook and no explicit flush, so the request boundary is its only send path. Mutation M2 reddens it along with every other request-boundary test. The public manual flush and the best-effort exit hook remain the core's, and the README points work outside a request at `get_client().flush_pending()` |
| REG-4 | n/a (profile: browser) | - | Browser teardown via `keepalive`. A server has no page teardown |
| REG-5 | n/a (profile: browser) | - | The browser teardown flush path. A server has no page teardown |
| REG-6 | delegated | - | core `REG-6` (implemented, n/a (pure)). Probe `queue-mutation`: this binding never snapshots or clears the queue. It fires on core `client.py` and on `middleware.py` at `34a6a87`, which cleared it |
| REG-7 | delegated | - | core `REG-7` (implemented, n/a (pure)). Probe `send-concurrency` matches nothing here and fires on core `client.py`. The request hook calls the core's flush, which declines while a send is in flight |
| REG-8 | delegated | - | core `REG-8` (**provisional**, mock). Probe `scheduling`: there is no retry or backoff here. It fires on core `client.py` |
| REG-9 | delegated | - | core `REG-9` (**provisional**, mock). Probe `batching` matches nothing here and fires on core `registration.py` |
| REG-10 | delegated | - | core `REG-10` (implemented, live). Probe `failure-shape`: there is no swallowed exception and no success-shaped result here. It fires on core `client.py` and on `middleware.py` at `34a6a87`, which wrapped its flush in `except Exception` |
| REG-11 | delegated | - | core `REG-11` (implemented, n/a (pure)). Probe `ellipsis` matches nothing here and fires on core `client.py` |
| REG-12 | delegated | - | core `REG-12` (implemented, n/a (pure)). Probe `block-identity` matches nothing here and fires on core `client.py` |
| HINT-1 | n/a (profile: browser) | - | The report payload. A server SDK never reports (HINT-2) |
| HINT-2 | delegated | - | core `HINT-2` (implemented, n/a (pure): no report lane exists). Probe `report-lane` matches nothing here. It fires on the TypeScript core's `api.ts`, which posts to `discovery/hint`, and on a synthetic call, so a report lane added to this binding would turn it red |
| HINT-3 | n/a (profile: browser) | - | URL capture timing for reports. A server has no report lane |
| HINT-4 | n/a (profile: browser) | - | Per-URL session dedup in `sessionStorage`, which is browser state |
| HINT-5 | n/a (profile: browser) | - | Report jitter. A server has no report lane |
| HINT-6 | n/a (profile: browser) | - | URL normalisation for reports. The backend mirror is not an SDK rule |
| HINT-7 | n/a (profile: browser) | - | Report-lane reliability. A server has no report lane |
| HINT-8 | n/a (profile: browser) | - | No reporting during server rendering. A server has no report lane |
| HINT-9 | n/a (profile: browser) | - | The `auto_discovery` policy for reports. A server has no report lane |
| HINT-10 | n/a (profile: browser) | - | Declining credential-bearing URLs in reports. A server has no report lane |
| HINT-11 | n/a (profile: browser) | - | Testing both fragment shapes in reports. A server has no report lane |
| HINT-12 | n/a (profile: browser) | - | Normalisation parity across report legs. The server mirror is backend behaviour |
| ICU-1 | delegated | - | core `ICU-1` (implemented, n/a (pure)). Probe `interpolation`: there is no interpolation here. It fires on core `interpolate.py` |
| ICU-2 | delegated | - | core `ICU-2` (implemented, n/a (pure)). Probe `interpolation` fires on core `interpolate.py`. The template path is where a binding could narrow this rule: `{% t %}` now passes an undefined variable as missing. See `test_ICU2_an_undefined_template_variable_is_an_absent_argument` and `test_ICU2_a_context_value_of_none_is_an_absent_argument`, with `test_control_an_explicit_empty_string_is_still_a_value` keeping emptiness distinct from absence. At `34a6a87` the first rendered `Hi !`, and mutation M8 reddens it |
| ICU-3 | delegated | - | core `ICU-3` (implemented, n/a (pure)). Probe `interpolation` fires on core `interpolate.py`. At `34a6a87`, `test_ICU3_a_plural_over_an_undefined_variable_recovers_instead_of_leaking_its_source` leaked the raw plural source through the tag, and mutation M8 reddens it |
| ICU-4 | delegated | - | core `ICU-4` (implemented, n/a (pure)). Probe `interpolation` matches nothing here and fires on core `interpolate.py` |
| ICU-5 | delegated | - | core `ICU-5` (implemented, n/a (pure)). Probe `interpolation` matches nothing here and fires on core `interpolate.py` |
| CID-1 | delegated | - | core `CID-1` (implemented, n/a (pure)). Probe `block-identity`: no id is derived here. It fires on core `client.py` |
| CID-2 | delegated | - | core `CID-2` (implemented, n/a (pure)). Probe `block-identity` matches nothing here and fires on core `client.py` |
| CID-3 | delegated | - | core `CID-3` (implemented, n/a (pure)). Probe `block-identity` fires on core `client.py`. This binding reads ids only through the core, which carries the tolerating half |
| CID-4 | delegated | - | core `CID-4` (implemented, n/a (pure)). Probe `block-identity` matches nothing here and fires on core `client.py` |
| TOK-1 | delegated | - | core `TOK-1` (implemented, n/a (pure)). Probe `tokenizer`: there is no tokenizer here. It fires on core `client.py`. Page and block translation are reached only as `get_client().translate_page(…)`, which is the core by reference |
| TOK-2 | delegated | - | core `TOK-2` (**held (strip ruling)**, n/a (pure)). Probe `tokenizer` matches nothing here and fires on core `client.py`. This row delegates to a core row that is not green |
| TOK-3 | delegated | - | core `TOK-3` (**partial**, n/a (pure)); this row delegates to a core row that is not green. Probe `tokenizer` matches nothing here and fires on core `client.py` |
| TOK-4 | delegated | - | core `TOK-4` (**partial**, n/a (pure)); this row delegates to a core row that is not green. Probe `tokenizer` matches nothing here and fires on core `client.py` |
| TOK-5 | delegated | - | core `TOK-5` (implemented, n/a (pure)). Probe `interpolation` matches nothing here and fires on core `interpolate.py` |
| MARK-1 | delegated | - | core `MARK-1` (implemented, n/a (pure)). Probe `identity-stamping`: no host is stamped here. It fires on core `client.py` |
| MARK-2 | delegated | - | core `MARK-2` (implemented, n/a (pure)). Probe `identity-stamping` matches nothing here and fires on core `client.py` |
| SSR-1 | n/a (profile: browser) | - | Where the browser SDK collects under the client strategy. This is a server binding |
| SSR-2 | n/a (profile: browser) | - | The browser SDK degrading its strategy when a grant is configured. This is a server binding |
| SSR-3 | n/a (profile: browser) | - | The browser SDK's server-strategy precondition. This is a server binding |
| SRV-1 | implemented | n/a (pure) | `{% t %}`, the `t` filter and `t()` render the request locale's current translations into the served bytes. `test_SRV1_served_bytes_carry_the_request_locale` asserts on `response.content`; its control is a phrase absent from the catalog in the same render, which emits the base language and registers. `test_SRV1_a_streamed_render_carries_the_request_locale` covers the streamed path, which rendered the base language at `34a6a87`. Mutation M4 reddens it. These are the origin's bytes; that a shared cache can serve them to a visitor in another language, for want of a `Vary` header, is recorded under BIND-4 |
| SRV-2 | implemented | n/a (pure) | The request locale lives in a `ContextVar`, never a process global, and the core keys its catalog by locale. `test_SRV2_concurrent_requests_never_see_each_others_locale` pins the interleave with two barriers. Mutation M5, a process-global locale, reddens it and `test_middleware_resets_locale_after_request`. With a single barrier this test stayed green under M5 (item 8) |
| SRV-3 | implemented | n/a (pure) | The middleware opens one of the core's request scopes for every request and ends it when the response is closed, once the body, streamed or not, has gone out; `request_finished` then flushes. A miss recorded inside the scope is sent by no flush before that: not the core's debounce (`test_SRV3_the_core_debounce_never_sends_before_the_response_is_complete`), not a concurrent request's flush (`test_SRV3_another_requests_flush_never_sends_a_render_still_in_progress`), and not before a slow stream completes (`test_SRV3_a_streamed_body_holds_its_misses_until_it_is_complete`). `test_SRV3_registration_happens_only_after_the_response_is_complete` and `test_SRV3_a_streamed_body_is_complete_before_its_misses_are_sent` pin the order at the request boundary, and `test_SRV3_a_view_that_raises_still_releases_its_misses` covers a view that leaves no response to close. Read-only half: `test_SRV3_a_read_only_key_pushes_nothing`, with `test_SRV3_control_a_write_key_on_the_same_render_pushes` as its control. Mutations M10 (ending the scope when the middleware returns), M11 (no scope), M12 (a scope never ended) and M13 (a view that raises leaves it open) each redden a named test |
| SRV-4 | n/a (architecture: Django templates emit terminal HTML and nothing hydrates against it, so there is no catalog to hand off; live if this binding ever ships a client entry that hydrates) | - | 8.0.1 scopes SRV-4 to an SDK in a hydration hand-off and says a terminal-HTML server SDK rows it `n/a` structurally. The core rows it `not implemented` against v8, before that scoping |
| SRV-5 | n/a (architecture: no tag here captures a rendered child subtree, since `{% t %}` takes its phrase as an argument; live if a block tag that captures its rendered body is added) | - | The once-per-subtree half belongs to a DOM-walking path. Here that is the core's `translate_page`, reached by reference only |
| BIND-1 | partial | n/a (pure) | Only timing and shape are adapted: the HTTP request's locale goes into the core's `LocaleSource`, streamed chunks render in that locale, and each request runs inside one of the core's request scopes, and the core's public flush and reset run when Django ends it. With the binding deleted, calling the core directly changes only where the locale comes from and when those two public calls happen, except for the collision below. One guarantee this binding had narrowed was undefined template arguments reaching the core as `""` (item 6). That is fixed, and mutation M8 reddens `test_ICU2_an_undefined_template_variable_is_an_absent_argument` and `test_ICU3_a_plural_over_an_undefined_variable_recovers_instead_of_leaking_its_source`. The other adaptations are covered by M4, which reddens `test_SRV1_a_streamed_render_carries_the_request_locale`, and M5, which reddens `test_SRV2_concurrent_requests_never_see_each_others_locale`. **Still narrowed:** the `t(phrase, category=None, **params)` shape cannot pass a placeholder named `category` or `phrase`, which the core's `params` dict can (item 10). The silent misroute is pinned by `test_gap_a_category_keyword_is_taken_as_the_catalog_category` and `test_gap_the_tag_takes_a_category_keyword_as_the_catalog_category`. The loud forms are pinned by `test_gap_a_category_beside_a_category_keyword_raises`, `test_gap_in_a_template_the_collision_raises_while_rendering` and `test_gap_a_phrase_keyword_collides_too`. `test_workaround_the_core_takes_any_parameter_name` is the control. Waits on: one signature decision across the core, FastAPI and Django |
| BIND-2 | implemented | n/a (pure) | Probe `capability` matches nothing here and fires on `middleware.py` at `34a6a87`, where the defect itself lives. `test_BIND2_capability_unknown_holds_the_queue_through_the_binding` was red at `34a6a87`. Mutation M1, restoring the `can_write` branch in the request hook, reddens that test along with the `capability` and `queue-mutation` probes |
| BIND-3 | implemented | n/a (pure) | Probe `network-client` shows there is no HTTP client here and fires on core `http.py`. Probe `scheduling` shows there is no timer, sleep, retry or backoff and fires on core `client.py`. Opening and ending the core's request scope, and calling its public flush when Django ends a request, is lifecycle timing (BIND-1), not scheduling: the binding sets no timer and leaves the core's debounce as it is. Both probes run as `test_probe_matches_nothing_in_this_binding`, and their controls as `test_probe_fires_on_its_control` |
| BIND-4 | partial | n/a (pure) | `AUTO_FLUSH` is removed (item 4). Probe `binding-discovery-switch` fires on `conf.py` at `34a6a87`. `test_BIND4_no_binding_setting_switches_registration_off` was red at `34a6a87`, and mutation M6 reddens it, along with the `binding-discovery-switch` and `queue-mutation` probes. The remaining keys: `API_KEY`, `PROJECT_ID`, `API_URL` and `BASE_LOCALE` are core constructor arguments, and `SUPPORTED` is an argument to the core's `detect_preferred_locale`. `QUERY_PARAM`, `COOKIE_NAME` and `COOKIE_MAX_AGE` only configure where an HTTP request carries the core's locale value, not product behaviour. **Pending the operator's ruling** on the ambient-locale question, which Reviewer routed together with the Rails lane's CDN cross-serve: as ruled for Rails, the three keys stay and this row is partial until then. **No `Vary` header** (item 11): `test_gap_accept_language_negotiation_sends_no_vary_header` and `test_gap_cookie_negotiation_sends_no_vary_header` show the same URL serving two languages with neither response carrying `Vary`. Waits on: the operator's ambient-locale ruling |
| BIND-5 | implemented | n/a (pure) | Probe `cache` matches nothing here and fires on core `catalog.py`. No lookup is memoised here, so presence cannot be lost in this layer. The single shared client is an instance holder, not a lookup cache. The probe runs as `test_probe_matches_nothing_in_this_binding` |
| BIND-6 | implemented | n/a (pure) | `test_BIND6_public_names_are_django_idioms_over_core_values` pins `__all__` to six names and asserts that `t` mirrors the core's own alias. `test_BIND6_the_core_is_reachable_by_reference_not_through_a_wrapper` asserts that `get_client()` returns the core instance itself. The tag is a `SimpleNode` subclass, which is a Django idiom and not a new behaviour name |
| GRANT-1 | n/a (profile: browser) | - | A grant lends write capability to a browser session, and a server already holds a key. This binding sets no header of any kind (probe `auth-header`), so it never sends `X-Write-Grant` |
| GRANT-2 | n/a (profile: browser) | - | Resolving a browser grant per request. A server holds a key |
| GRANT-3 | n/a (profile: browser) | - | Re-authorizing on a new browser grant. A server holds a key |
| GRANT-4 | n/a (profile: browser) | - | The `X-Write-Grant` header. This binding sets no header |
| CACHE-1 | delegated | - | core `CACHE-1` (implemented, n/a (pure)). Probe `cache`: no cache key is built here. It fires on core `catalog.py` |
| OBS-1 | delegated | - | core `OBS-1` (implemented, n/a (pure)). Probe `diagnostics`: this binding logs nothing of its own. It fires on core `client.py` and on `middleware.py` at `34a6a87`. The request boundary is what re-arms the core's once-per-session notice: see `test_GATE3_the_unusable_capability_notice_rearms_at_each_request` |
| WIRE-1 | delegated | - | core `WIRE-1` (implemented, live). Probe `auth-header` matches nothing here and fires on core `http.py` |
| WIRE-2 | delegated | - | core `WIRE-2` (implemented, n/a (pure)). Probe `response-parsing`: no API response is parsed here. It fires on core `http.py` |
| WIRE-3 | delegated | - | core `WIRE-3` (implemented, live). Probe `identifier-normalisation`: there is no lowercasing or sentinel here. It fires on core `catalog.py`. The middleware hands the core `canonicalize_locale` output, and the core lowercases it on the wire |
| WIRE-4 | delegated | - | core `WIRE-4` (implemented, live). Probe `raising`: nothing on this binding's paths raises. It fires on core `http.py`. The request hook calls `flush_pending()`, which the core guarantees never raises (REG-10) |
| WIRE-5 | implemented | n/a (pure) | The seam is `LANGSYS["API_URL"]`, documented in the README settings reference along with its ordering constraint. `test_WIRE5_API_URL_sends_the_client_to_the_double` proves a request arrives at the double. `test_WIRE5_a_redirect_after_the_client_is_built_is_too_late_until_reset` redirects too late and observes the request still reaching the old host until `reset_client()`, which is the failure the rule names. Mutation M9, dropping `API_URL` on its way to the core, reddens both |
| CONF-1 | implemented | n/a (pure) | No row here claims server acceptance. Every binding-owned row is an in-process property, and every API-dependent property is delegated with the core's tier named. Every-path clause: the binding's entry points are `{% t %}`, the `t` filter, `t()` and a streamed render. `test_GATE7_every_entry_point_reaches_the_core_queue`, `test_SRV1_a_streamed_render_carries_the_request_locale` and `test_SRV3_a_streamed_body_is_complete_before_its_misses_are_sent` prove each path rather than one |
| CONF-2 | implemented | n/a (pure) | Every row carries a tier from the checker vocabulary, and delegated rows take `-`. This is enforced by `test_every_status_and_tier_is_in_the_checker_vocabulary` and `test_a_delegated_row_names_its_core_row_and_a_probe_that_covers_it`. No row claims `contract` |
| CONF-3 | implemented | n/a (pure) | Every binding-owned runtime row names a mutation that reddens a named test. The mutations are M1–M13, applied by the committed `_dev_/mutations.py` to a copy of `src/` imported ahead of the package, against the green baseline M0; their results are in the table below. Every probe carries a firing control, and `test_probe_fires_on_its_control` fails when no control can run |

## Mutation evidence

Produced by `_dev_/mutations.py`; each line is from a run, not recalled. The conformance-file tests
are excluded from these runs because no mutation touches `CONFORMANCE.md`.

| Mutation | Suite | Reddens |
|---|---|---|
| M0 baseline, no edit | 87 passed | nothing |
| M1 restore the can_write branch in the request hook | 3 failed, 84 passed, 4 errors | `test_probes.py::test_probe_matches_nothing_in_this_binding[capability]`, `test_probes.py::test_probe_matches_nothing_in_this_binding[queue-mutation]`, `test_request_lifecycle.py::test_BIND2_capability_unknown_holds_the_queue_through_the_binding`; errors: `test_request_lifecycle.py::test_SRV2_concurrent_requests_never_see_each_others_locale`, `test_wrapper.py::test_middleware_locale_from_accept_language`, `test_wrapper.py::test_middleware_locale_from_query_and_cookie_persist`, `test_wrapper.py::test_middleware_resets_locale_after_request`. The errors come from the restored branch asking authorize, which those tests' doubles do not serve |
| M2 no flush at the request boundary | 12 failed, 75 passed | `test_request_lifecycle.py::test_BIND4_no_binding_setting_switches_registration_off`, `test_request_lifecycle.py::test_GATE3_an_observed_write_decision_does_not_survive_the_request`, `test_request_lifecycle.py::test_GATE3_the_unusable_capability_notice_rearms_at_each_request`, `test_request_lifecycle.py::test_GATE7_every_entry_point_reaches_the_core_queue`, `test_request_lifecycle.py::test_REG3_the_queue_is_registered_at_the_end_of_every_request`, `test_request_lifecycle.py::test_SRV1_served_bytes_carry_the_request_locale`, `test_request_lifecycle.py::test_SRV3_a_streamed_body_holds_its_misses_until_it_is_complete`, `test_request_lifecycle.py::test_SRV3_a_streamed_body_is_complete_before_its_misses_are_sent`, `test_request_lifecycle.py::test_SRV3_another_requests_flush_never_sends_a_render_still_in_progress`, `test_request_lifecycle.py::test_SRV3_control_a_write_key_on_the_same_render_pushes`, `test_request_lifecycle.py::test_SRV3_registration_happens_only_after_the_response_is_complete`, `test_request_lifecycle.py::test_SRV3_the_core_debounce_never_sends_before_the_response_is_complete` |
| M3 no reset at the request boundary | 2 failed, 85 passed | `test_request_lifecycle.py::test_GATE3_an_observed_write_decision_does_not_survive_the_request`, `test_request_lifecycle.py::test_GATE3_the_unusable_capability_notice_rearms_at_each_request` |
| M4 streamed body not rendered in the request locale | 2 failed, 85 passed | `test_request_lifecycle.py::test_SRV1_a_streamed_render_carries_the_request_locale`, `test_request_lifecycle.py::test_SRV3_a_streamed_body_is_complete_before_its_misses_are_sent` |
| M5 process-global locale instead of a ContextVar | 2 failed, 85 passed | `test_request_lifecycle.py::test_SRV2_concurrent_requests_never_see_each_others_locale`, `test_wrapper.py::test_middleware_resets_locale_after_request` |
| M6 restore an AUTO_FLUSH switch | 3 failed, 84 passed | `test_probes.py::test_probe_matches_nothing_in_this_binding[binding-discovery-switch]`, `test_probes.py::test_probe_matches_nothing_in_this_binding[queue-mutation]`, `test_request_lifecycle.py::test_BIND4_no_binding_setting_switches_registration_off` |
| M7 the t filter bypasses the core | 2 failed, 85 passed | `test_request_lifecycle.py::test_GATE7_every_entry_point_reaches_the_core_queue`, `test_wrapper.py::test_template_tag_and_filter` |
| M8 the tag takes Django's empty string for a missing argument | 2 failed, 85 passed | `test_template_arguments.py::test_ICU2_an_undefined_template_variable_is_an_absent_argument`, `test_template_arguments.py::test_ICU3_a_plural_over_an_undefined_variable_recovers_instead_of_leaking_its_source` |
| M9 the settings API_URL never reaches the core | 2 failed, 85 passed, 2 errors | `test_settings_seam.py::test_WIRE5_API_URL_sends_the_client_to_the_double`, `test_settings_seam.py::test_WIRE5_a_redirect_after_the_client_is_built_is_too_late_until_reset`; errors: `test_settings_seam.py::test_WIRE5_API_URL_sends_the_client_to_the_double`, `test_settings_seam.py::test_WIRE5_a_redirect_after_the_client_is_built_is_too_late_until_reset`. The errors are at teardown: the requests went to the default API host, which no double serves |
| M10 end the request scope when the middleware returns | 1 failed, 86 passed | `test_request_lifecycle.py::test_SRV3_a_streamed_body_holds_its_misses_until_it_is_complete` |
| M11 no request scope: end it as soon as it opens | 3 failed, 84 passed | `test_request_lifecycle.py::test_SRV3_a_streamed_body_holds_its_misses_until_it_is_complete`, `test_request_lifecycle.py::test_SRV3_another_requests_flush_never_sends_a_render_still_in_progress`, `test_request_lifecycle.py::test_SRV3_the_core_debounce_never_sends_before_the_response_is_complete` |
| M12 never end the request scope | 12 failed, 75 passed | `test_request_lifecycle.py::test_BIND4_no_binding_setting_switches_registration_off`, `test_request_lifecycle.py::test_GATE3_an_observed_write_decision_does_not_survive_the_request`, `test_request_lifecycle.py::test_GATE3_the_unusable_capability_notice_rearms_at_each_request`, `test_request_lifecycle.py::test_GATE7_every_entry_point_reaches_the_core_queue`, `test_request_lifecycle.py::test_REG3_the_queue_is_registered_at_the_end_of_every_request`, `test_request_lifecycle.py::test_SRV1_served_bytes_carry_the_request_locale`, `test_request_lifecycle.py::test_SRV3_a_streamed_body_holds_its_misses_until_it_is_complete`, `test_request_lifecycle.py::test_SRV3_a_streamed_body_is_complete_before_its_misses_are_sent`, `test_request_lifecycle.py::test_SRV3_another_requests_flush_never_sends_a_render_still_in_progress`, `test_request_lifecycle.py::test_SRV3_control_a_write_key_on_the_same_render_pushes`, `test_request_lifecycle.py::test_SRV3_registration_happens_only_after_the_response_is_complete`, `test_request_lifecycle.py::test_SRV3_the_core_debounce_never_sends_before_the_response_is_complete` |
| M13 a view that raises leaves its scope open | 1 failed, 86 passed | `test_request_lifecycle.py::test_SRV3_a_view_that_raises_still_releases_its_misses` |

## Absence probes

`tests/test_probes.py` holds 22 probes. Each is a pattern over this binding's code, with comments and
docstrings stripped by `ast`, and must match nothing. Each also has a control it must fire on: the
core, this binding at `34a6a87`, the TypeScript core, or a synthetic line. A control that is
unavailable on a machine is skipped, but a probe with no runnable control fails. The probes read the
package as imported, so a mutated copy is what they probe.

## Gaps, ranked by cost

1. **No `Vary` header, so a shared cache can serve the wrong language.** The middleware negotiates
   from the cookie and `Accept-Language`, so one URL serves several languages, and no response says so.
   Behind a CDN, or any shared cache that caches HTML, one visitor's language can be served to the
   visitors after them on every page, which the Rails lane reproduced. The fix is one
   `patch_vary_headers` call, but it belongs to the ambient-locale question the operator is ruling on.
2. **BIND-1: a placeholder named `category` or `phrase` cannot pass through `t()` or `{% t %}`.**
   The silent form costs most: `{% t "Browse {category}" category=cat.name %}` serves the
   placeholder unfilled to every visitor and registers the phrase under a category named after the
   value. The loud form, a category plus `category=`, is a render-time `TypeError`. Python callers
   can pass the value through `get_client().translate(…, params={…})` (README); in a template the
   only workaround is renaming the placeholder. A positional-only signature would silently
   reroute today's `t("Save", category="UI")` calls, so the shape is one decision across the core
   and both bindings, which Reviewer is taking to the Python lane.
3. **Delegated rows whose core row is not green count red for this lane.** In the core's conformance
   file at `506dd86`, GATE-7, TOK-3 and TOK-4 are `partial` and TOK-2 is `held (strip ruling)`, while
   GATE-2, GATE-5, REG-8 and REG-9 are `provisional`. All of these are core-side, and this lane's green
   follows the core's.
4. **BIND-4 waits on the operator's ambient-locale ruling.** `QUERY_PARAM`, `COOKIE_NAME` and
   `COOKIE_MAX_AGE` stay until then, as ruled for Rails. If the ruling reads BIND-4 as zero added keys,
   they become fixed defaults, which is a small change.
5. **The profile is derived, not named.** The spec covers Django only as a "framework variant". Every
   `n/a` here rests on reading this package as a binding over a server core. Reviewer has flagged this
   to the operator.
6. **Local evidence runs against a live core tree, not a pin.** The core's harness no longer rewrites
   that tree (`506dd86`), but a local run still loads whatever the core branch holds at that moment.
   Pinned runs, like Reviewer's, are the authoritative ones.
