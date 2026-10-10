# Gameplay code audit: 10 October 2026

The concrete fixes are now implemented in the user's downstream DXMT fork; see
[implementation and regression results](DXMT-FIXES-2026-10-10.md). The findings
below record the original audited source behavior.

The best first performance candidate is narrower invalidation of dynamic-buffer
bindings. The strongest hitch candidates are compiler scheduling and redundant
ordinary pipeline variants. A separate stream-output cache-key omission warrants
correctness testing. These are verified source behaviors, not measured causes of
Risk of Rain 2's frame time or verified FPS improvements.

## Scope and evidence

The primary audit target is DXMT revision
`7c8dee1c2d73415301ceb7d1fa810861cef4cd67`, matching the installed version stamp
`v0.80-244-g7c8dee1`. Relevant paths were also checked against the newer local
revision `e94c312f5c054263acf261cfa109edf13e757587`. The reviewed binding-set,
scheduler, pipeline-key, ordinary pipeline-builder and device-lock implementations
are unchanged between those commits. The existing HUD patch was left in place;
this audit made no further changes to DXMT or the installed wrapper.

The [gameplay capture](BASELINE-2026-10-09.md#user-confirmed-solo-gameplay) averaged
100.15 FPS from logged intervals, 9.99 ms between presents and 3.36 ms of GPU work.
Process CPU averaged 450.6%, including 266.0% system CPU. This supports examining
CPU work and waits, but does not locate the critical thread. The 120 FPS cap,
logging overhead and game simulation remain confounders; the difference between
presentation and GPU duration is not measured translation overhead.

The [audit probe](../tools/audit_dxmt_gameplay.py) exports source at the pinned
revision into a temporary directory and compiles the actual map-marking block,
BindingSet, task scheduler and pipeline hash/equality functions. Resource and
Win32 types are substitutes; scheduler workers use native threads with instrumented
notification/priority stubs. These probes validate operation counts and key
semantics. They do not measure Wine scheduling cost, Metal rendering or gameplay.

## 1. Dynamic maps invalidate unrelated bindings

In [Map](https://github.com/Sikarugir-App/dxmt/blob/7c8dee1c2d73415301ceb7d1fa810861cef4cd67/src/d3d11/d3d11_context_imm.cpp#L154),
`WRITE_DISCARD` of a constant buffer marks every constant-buffer slot in every
shader stage dirty, regardless of whether that stage uses the mapped resource.
Vertex-buffer and SRV paths have similarly broad invalidation. Dynamic texture
maps also dirty all stage SRVs.

This reaches
[UploadShaderStageResourceBinding](https://github.com/Sikarugir-App/dxmt/blob/7c8dee1c2d73415301ceb7d1fa810861cef4cd67/src/d3d11/d3d11_context_impl.cpp#L3277),
which allocates a new argument-table region and queues encoding whenever a dirty
bit intersects the shader's reflected slot mask. Encoding walks the reflected
bindings, resolves allocation addresses, registers residency and emits buffer
offset commands. It is more than the cost of setting the dirty bits themselves.

The probe reproduced 84 dirty constant-buffer slots across six stages after one
map-marking operation, and two active shader tables requiring upload despite
distinct bound resource identities. The marking block contains no resource-identity
check. An update to an unbound resource can therefore trigger both uploads;
an update used only by the vertex shader can also invalidate an unrelated pixel
shader table. Actual redundant upload frequency in this game remains unmeasured.

Proposed improvement: identify the slots that reference the renamed resource,
then dirty those slots/stages. For SRVs, compare underlying resources rather than
view identity, including multiple views of the same resource. Preserve full
invalidation at encoder/pipeline transitions and handle deferred command-list
restore, buffer suballocations and resource lifetime correctly. A reverse binding
index could avoid scanning every slot, but introduces maintenance cost; measure
that tradeoff before choosing it.

Measure first: discard maps per frame, maps of unbound resources, argument tables
rebuilt per stage, argument-buffer bytes and CPU time in binding encoding. Retain
a change only if warmed gameplay improves and rename/readback rendering remains
correct. This is the first candidate for steady-state CPU submission work.

## 2. Compiler completion wakes every worker without dependent work

The [scheduler completion path](https://github.com/Sikarugir-App/dxmt/blob/7c8dee1c2d73415301ceb7d1fa810861cef4cd67/src/dxmt/dxmt_tasks.hpp#L98)
always calls `worker_cond_.notify_all()` after a task completes, even when its
continuation list is empty. In that case it has added no dependent tasks to the
queue. Submission already issues its own notification. During a burst of shader
and pipeline jobs this creates avoidable wakeups and lock contention.

The scheduler also permits twice the processor count in workers and requests
`THREAD_PRIORITY_TIME_CRITICAL` in each worker. This Mac reports 14 logical cores
(10 performance, 4 efficiency); with that count the cap is 28 workers. The probe
reached 28 workers using blocked fixture tasks, recorded 28 priority requests,
and recorded 28 completion broadcasts with no dependent work. This does not prove
28 compiler workers ran during the saved game or that Wine granted their requested
host priority. Windows API priority semantics are described in
[Microsoft's documentation](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadpriority).

Proposed improvement: suppress completion notifications when no continuation was
enqueued; size notifications to available work. Test configurable worker limits
and priority separately. Reserving CPU capacity for game/encoding threads is a
hypothesis, not a universally correct lower thread count. Some compile tasks block
inside drivers and benefit from concurrency. Preserve dependency registration,
completion ordering, shutdown wakeups and progress for fan-out chains.

Measure first: active/idle workers, queue delay, continuation batch sizes, wakeups,
context switches, PSO wait time and cold versus warmed p99 frame intervals. This
candidate concerns compilation hitches; it may have little effect when all pipelines
are already ready.

## 3. Ordinary pipeline keys include irrelevant index format

[Descriptor construction](https://github.com/Sikarugir-App/dxmt/blob/7c8dee1c2d73415301ceb7d1fa810861cef4cd67/src/d3d11/d3d11_context_impl.cpp#L4603)
stores NONE, UINT16 or UINT32 according to the draw. The shared pipeline hash and
equality include that field. The
[ordinary graphics pipeline builder](https://github.com/Sikarugir-App/dxmt/blob/7c8dee1c2d73415301ceb7d1fa810861cef4cd67/src/d3d11/d3d11_pipeline.cpp#L13)
does not use `IndexBufferFormat` to construct its shaders or Metal descriptor.
Consequently, otherwise identical ordinary pipeline inputs can occupy three
entries. The probe reproduced those three distinct keys.

A cache miss schedules a new pipeline build. The encoding thread later calls
[GetPipeline](https://github.com/Sikarugir-App/dxmt/blob/7c8dee1c2d73415301ceb7d1fa810861cef4cd67/src/d3d11/d3d11_pipeline.cpp#L49),
which waits until compilation completes. Avoiding unnecessary variants can reduce
first-use work and cache footprint. The code already caches pipelines and skips
lookup when the current pipeline remains ready; this is not a per-draw compile.
Existing shader/Metal caches can also reduce the cost of rebuilding equivalent
pipelines, so the key count does not establish a proportional compile-time saving.

Proposed improvement: canonicalize the ordinary graphics pipeline key so index
format does not distinguish equivalent Metal state. Keep tessellation and geometry
keys separate: their shader variants can depend on index format. Verify identical
rendering while switching 16-bit/32-bit and indexed/non-indexed draws.

Measure first: misses attributable only to index format, duplicate build duration,
encoding-thread stalls and warmed cache size. This is primarily a first-use/hitch
candidate rather than a guaranteed steady-state FPS gain.

## 4. Stream-output layout is missing from pipeline equality

The same [pipeline key](https://github.com/Sikarugir-App/dxmt/blob/7c8dee1c2d73415301ceb7d1fa810861cef4cd67/src/d3d11/d3d11_pipeline.hpp#L80)
does not compare `SOLayout`. But the ordinary builder uses it to select a
`ShaderVariantVertexStreamOutput`, and shader compilation uses the layout's digest
and declarations. The stream-output wrapper's managed geometry shader is null,
so geometry-shader identity does not necessarily distinguish different layouts.

The probe reproduced equal keys and hashes for different non-null layout pointers
with otherwise identical state. A cache hit can therefore reuse a pipeline built
for another output declaration. This is a correctness candidate affecting games
that use the supported stream-output path. No such use has been established for
Risk of Rain 2, and the probe does not demonstrate an on-screen failure.

Proposed improvement: include stream-output layout semantics consistently in
equality and hashing. Test two different declarations/strides with identical
vertex shader, input layout and other pipeline state, then validate output bytes
and alternation between layouts. Do this before relying on pipeline-key
canonicalization for broader game compatibility.

## Conditional paths that need runtime evidence

- The [device lock](https://github.com/Sikarugir-App/dxmt/blob/7c8dee1c2d73415301ceb7d1fa810861cef4cd67/src/d3d11/d3d11_multithread.cpp#L9)
  spins without a blocking fallback. A staging Map can wait for the GPU while
  holding that lock, so other callers may burn CPU when multithread protection is
  enabled. Protection defaults off in D3D11; its actual state and contention in
  this game are unknown. Consider bounded spin followed by a wait only after
  observing contention. Disabling protection is not a correctness-preserving fix.
- Event query polling overrides `DONOTFLUSH` after 64 pending polls and can cause
  a submission. Repeated poll counts are not durations. End-query promotion,
  staging readbacks and forced flushes could fragment command batches; count
  actual commits, not calls to Flush that may do nothing. Microsoft documents
  both the [no-flush flag and its progress hazard](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/ne-d3d11-d3d11_async_getdata_flag).
  Removing these waits/flushes without a progress design risks hangs or stale data.
- Present has drawable and frame-latency waits. A cap or display pacing can be
  responsible for idle time. Measure those waits separately from binding encoding,
  compilation and game simulation before changing queue latency.
- Shader database readers/writers are externally locked by `ShaderCache`.
  SQLite's NOMUTEX setting alone is not evidence of a data race. Persistence runs
  in compile workers; batching/deferring writes is lower priority until lock or
  I/O wait is observed.

## Wine provenance and audit limits

Three public Wine synchronization files were read at
`455e3509b98a6919fd4ad1def4803e08c41c03b2` (2 October 2026). They include macOS
address waits and server-mediated object-wait fallbacks. That source cannot be
assumed to match the installed `wine sikarugir 11.0 (revision 1)`: the installed
ntdll contains `WINEMSYNC` support while the inspected public master files do not
contain its engine-specific implementation. Wrapper toggles and a binary string
establish availability/configuration, not which wait backend handled a gameplay
call. An exact Wine source/patch manifest and symbols are needed before proposing
a Wine synchronization patch. No Wine bottleneck is attributed from this audit.

This is a focused audit of the D3D11 path used by the baseline, not a full review
of Wine, D3D12, DXVK or Apple's D3DMetal. No replacement engine was compiled or
installed, and neither Steam client nor the game was relaunched. Full DXMT build
requirements are in [upstream's instructions](https://github.com/Sikarugir-App/dxmt/blob/main/docs/DEVELOPMENT.md).

## Reproduce and choose a runtime change

From this fork, with the pinned commit available in the DXMT checkout:

```sh
python3 tools/audit_dxmt_gameplay.py --source ../dxmt-runtime-source
```

The result reports 84 dirty slots, two dirty active tables, three index-format
keys, stream-output layout aliasing, and 28 worker/completion-notification counts
in the explicit 14-processor fixture. The source files are not modified.

The first runtime instrumentation should record per-frame binding-table rebuilds
and bytes, map/readback wait time, PSO wait duration, command-buffer count,
drawable/latency waits and compiler worker activity. Use sampled or buffered
telemetry so the logging does not become the bottleneck. Reproduce a fixed warmed
scene with the cap removed, then compare one change at a time. Require repeated
median/p95/p99 improvement, no increased hangs, and correct rendering before
claiming a gameplay optimization.
