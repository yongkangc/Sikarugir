# Second gameplay hot-path audit: 10 October 2026

This audit found and fixed redundant state-setting work and several incorrect
binding/pipeline transitions. Runtime changes are in
[yongkangc/dxmt](https://github.com/yongkangc/dxmt/compare/332cee27aae3496d2d93e8f062145eada13a776b...6c0f379f930c6b0ba09291ba9142d8d09d92b523),
ending at `6c0f379f930c6b0ba09291ba9142d8d09d92b523` on `gameplay-performance`.
The inspected upstream main still resolves to `e94c312f5c054263acf261cfa109edf13e757587`.
The baseline for this pass is the previous downstream commit `332cee27`.

No engine was compiled or installed, and no game was launched. The fixes have
source-level regression evidence; their FPS impact and rendered output still
need full runtime validation. This is a focused review of D3D11 draw/dispatch
preparation, binding setters, hazard resolution, immediate/deferred maps and
command-queue synchronization, not an exhaustive review of Wine or DXMT.

## Findings and resulting behavior

| Finding | Original source behavior | Fixed behavior |
| --- | --- | --- |
| Redundant vertex-buffer binds | Queues an offset/stride command for each already-bound slot even when neither changes | Emits a command only when offset or stride changes |
| Redundant input-layout binds | Invalidates the ready pipeline on every call, including repeated null | Preserves the ready pipeline when resolved layout identity is unchanged |
| Stale replacement hazard mask | Hazard bit changes only on the first bind, not when another resource replaces it | Updates the bit on replacement, in both directions |
| Missing UAV null-table refresh | Last UAV unbind can return early because no UAV remains bound | Reflected UAV dirty bits trigger a refresh even when the result is null |
| Shared graphics UAV dirty ordering | The pixel stage clears shared dirtiness before later stages consume it | Clear occurs after all active graphics stages have prepared their tables |
| Stale constant-buffer range | Whole-buffer calls on the same buffer retain a prior offset/count | Restore offset zero and whole-buffer size metadata; unbound query outputs are initialized |
| Wrong index-dependent ready pipeline | Geometry/tessellation fast paths can reuse a pipeline for another index width or indexedness | Check the effective index variant before taking the ready fast path |
| Deferred map coverage | Still dirties all slots; resource is accessed before null-argument validation | Shares targeted invalidation with immediate maps and rejects null arguments first |

### Repeated binding work

The vertex setter's replacement check already detects identical buffer identity.
Its unchanged-resource branch, however, always emits `bindVertexBufferOffset`.
The fixture starts with two bound buffers, then repeats their identical strides
and offsets 1,000 times: **2,000 queued commands before, zero after**. Real changes
still emit one update, set the dirty bit and reach the encoding context. Invalid
hazardous inputs still unbind rather than being skipped as redundant.

The input-layout setter resolves a COM interface and always invalidates the
pipeline. A subsequent draw then performs pipeline lookup/preparation again,
even if the cache reuses an existing compiled pipeline. For 1,000 repeated binds,
the fixture records **1,000 invalidations before, zero after**. Layout changes and
transitions to null still invalidate. These operation counts are not timing or
FPS measurements; COM resolution itself remains in the setter.

### Hazard masks

`BindingSet::bind` previously updated its hazard bit only when the slot was
unbound. Replacing a read-only resource with one that can also be written leaves
the hazard iterator blind to the new resource. Replacing a hazardous resource
with a safe one leaves unnecessary checks pending. This affects the actual
`ResolveSRVHazard` and `ResolveIAHazard` loops, which iterate those bits.

The probe reproduces both incorrect directions, including high SRV slots 64/127.
It also compiles and invokes the actual SRV resolver: a conflicting output view
fails to unbind the replacement before the fix, and correctly unbinds it after.
The probe substitutes overlap/resource objects rather than validating D3D11
subresource or driver behavior.

### UAV state and per-use work

`UploadShaderStageResourceBinding` used the presence of a currently bound UAV,
but omitted UAV dirty bits from its upload decision. Removing the final reflected
UAV could therefore leave the old GPU argument table active, despite updating
the encoding context's CPU binding state. Dirty reflected slots now force a new
table containing the null binding. Slots not reflected by that shader do not
force an upload.

Shared OM UAV dirtiness must survive until every active graphics stage has
observed it. Pixel used to clear it inside the per-stage upload, but geometry,
hull and domain uploads occur later. Clearing now happens at the end of graphics
draw preparation. Tests cover three active VS/PS/GS stages and draws without PS;
each active table refreshes once after unbind, then unchanged null state causes
no further table upload. Compute retains its own dirty-bit consumption.

The existing bound-UAV per-use path is intentionally preserved. Resource encoding
also records read/write access, residency and dependencies. Removing it solely
because the UAV pointer is unchanged could lose synchronization or allocation
tracking. Separating table reuse from access tracking needs a different design
and real GPU validation.

### Constant-buffer and index variants

A ranged constant-buffer bind followed by a legacy whole-buffer bind on the same
resource retained `FirstConstant` and `NumConstants` because null arrays were
treated as “leave unchanged.” This contradicts the whole-buffer behavior of
[VSSetConstantBuffers1](https://learn.microsoft.com/en-us/windows/win32/api/d3d11_1/nf-d3d11_1-id3d11devicecontext1-vssetconstantbuffers1).
The shared setter serves all six shader stages. It now resets the offset and
size metadata using the resource's cached logical buffer length, which is
initialized from `ByteWidth`; it avoids a new `GetDesc` call on repeated binds.
For a 1,024-byte fixture buffer, the observed range changes from first/count
16/16 to 0/64. The encoding offset changes from 256 bytes to zero. Unbound getter
outputs also change from caller-provided sentinel values to initialized zeros,
consistent with [the unbound-slot API behavior](https://learn.microsoft.com/en-us/windows/win32/api/d3d11_1/nf-d3d11_1-id3d11devicecontext1-vsgetconstantbuffers1).

Geometry/tessellation shader variants depend on index format, but their ready
fast paths considered only the pipeline-kind state. Changing 16-/32-bit format,
or alternating indexed and non-indexed draws, could bypass the distinct cache
keys established by the pipeline descriptor. The new check invalidates the ready
pipeline when that effective variant changes. Descriptor construction uses the
same format helper. Ordinary draws retain their existing fast path without this
extra per-draw check. The fixture compiles the real early-return paths and checks
that changed variants require pipeline setup while repeated identical variants
keep the fast path; it does not validate generated shaders or index fetch output.

### Deferred contexts

The previous dynamic-map change applied to the immediate context. This pass
moves that unchanged helper to the shared context base and routes all three
deferred discard paths through it. All six immediate/deferred call sites are
checked, and the existing resource/view/dirty-bit regression fixture still passes.
Deferred Map now validates resource and output pointers before the first resource
lookup. The null-argument probe compiles that entry section; full command-list
recording/replay and dynamic-allocation lifetimes remain runtime checks.

## Validation and reproduction

[audit_dxmt_hotpath.py](../tools/audit_dxmt_hotpath.py) exports source or reads the
working tree, then compiles production methods and BindingSet with substitute
API/resource/encoder objects. The old behaviors are assertions in baseline mode,
not a manually rewritten implementation. Fixed-mode checks also exercise changed
strides/offsets, null/replaced layouts, reflected/unreflected UAV slots 0/63,
multi-stage UAV updates, preserved bound-UAV work, constant-buffer getters and
indexed/non-indexed transitions.

The final fixture passes as native arm64, x86-64 under Rosetta, and arm64 with
AddressSanitizer/UndefinedBehaviorSanitizer. The prior gameplay regression fixture
also passes, including scheduler dependencies, shutdown, cache-key distinctions
and targeted invalidation. All 14 profiling/launch/parser tests pass.
Aggregate results and source hashes are in [HOTPATH-VALIDATION-2026-10-10.json](HOTPATH-VALIDATION-2026-10-10.json).

```sh
python3 tools/audit_dxmt_hotpath.py --source /path/to/dxmt --reference 332cee27aae3496d2d93e8f062145eada13a776b --expect baseline
python3 tools/audit_dxmt_hotpath.py --source /path/to/dxmt --reference 6c0f379f930c6b0ba09291ba9142d8d09d92b523
python3 tools/audit_dxmt_hotpath.py --source /path/to/dxmt --architecture x86_64
python3 tools/audit_dxmt_hotpath.py --source /path/to/dxmt --sanitize
python3 tools/validate_dxmt_gameplay.py --source /path/to/dxmt
```

[dxmt-hotpath-state.patch](../patches/dxmt-hotpath-state.patch) is incremental from
`332cee27`. For the installed-source revision or newer upstream base, apply the
earlier gameplay patch first; the HUD patch is independent. Both source fixtures
are checked against the combined gameplay/hot-path patches on those revisions.
No raw game traces, runtime binaries or game files are published.

## Remaining investigation

This pass does not establish which path limits Risk of Rain 2. A matching full
engine build and repeated fixed-scene gameplay comparison remain necessary.
Measure redundant bind frequency, command counts, pipeline lookups, argument-table
bytes, CPU submission time, and frame median/p95/p99. Validate actual resource
rename, UAV output and alternating index variants before replacing the installed
runtime.

Several more involved paths remain candidates: shared graphics/compute hazard
tracking; enforcement of constant-buffer range lengths in shader loads; redundant
blend/depth/index state updates; and separation of UAV argument-table reuse from
per-use access tracking. Device-lock contention, command-buffer batching and
driver waits still require runtime evidence. These are not counted as fixed or
attributed as this game's bottleneck.
