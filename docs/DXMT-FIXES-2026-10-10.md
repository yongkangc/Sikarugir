# Downstream DXMT fixes: 10 October 2026

The concrete candidates from the [gameplay audit](GAMEPLAY-AUDIT-2026-10-10.md)
are implemented in the user's [DXMT fork](https://github.com/yongkangc/dxmt/tree/gameplay-performance),
commit [`332cee27`](https://github.com/yongkangc/dxmt/commit/332cee27aae3496d2d93e8f062145eada13a776b).
The branch starts at `e94c312f5c054263acf261cfa109edf13e757587`.
These are source fixes with isolated regression validation. No replacement engine
was built or installed, and no game FPS improvement has been measured.

## Resulting behavior

| Change | Before | After |
| --- | --- | --- |
| Dynamic resource discard | All slots of the matching binding class in every stage become dirty | Only bound slots referencing the renamed resource become dirty; SRVs compare underlying resources |
| Compiler completion | Broadcasts even when it queues no dependent work | No notification for an empty continuation list; one wakeup for one job, a broadcast for fan-out |
| Ordinary pipeline index variants | NONE, UINT16 and UINT32 can build three equivalent pipelines | One cache entry/build for otherwise identical state; geometry/tessellation remain distinct |
| Stream-output cache key | Different output layouts can compare equal | Layout identity participates in both equality and hashing |
| Scheduler shutdown | Can notify between a false predicate check and the worker's wait | Shutdown updates the predicate under the same mutex, then broadcasts and joins |

Dynamic invalidation iterates bound entries, without a new reverse index or COM
queries. It preserves pending dirty bits, including null uploads after an unbind,
and leaves full invalidation at encoder/pipeline transitions intact. Both dynamic
texture discard paths use the same resource check. Multiple views and shader
stages referring to one resource all become dirty; future bindings still use the
normal binding-set invalidation.

Ordinary pipeline normalization uses a local descriptor copy, leaving caller
state intact. Only `GetGraphicsPipeline` normalizes index format;
`GetGeometryPipeline` and `GetTessellationPipeline` retain their original keys.
Semantic blend-state equality is preserved. Stream-output layout identity is
consistent with the existing shader-variant pointer identity and layout cache.

The existing [HUD formatting cleanup](../patches/dxmt-hud-formatting.patch) is also
included in the runtime branch. The compiler's worker cap and requested priority,
device locking policy, query flushing, frame pacing and Wine synchronization are
unchanged; their gameplay impact still needs measurement.

## Regression evidence on this Mac

[validate_dxmt_gameplay.py](../tools/validate_dxmt_gameplay.py) compiles actual
production BindingSet/scheduler templates, the invalidation helper, pipeline key
functions and all three graphics cache methods. Resource, Metal and Win32 objects
are substitutes; compiler scheduling runs with native threads. The test does not
compile shaders, validate output bytes, run Wine, or render a frame.

- Ten full runs passed as arm64 and ten as x86-64 under Rosetta.
- Five full arm64 runs passed with AddressSanitizer and UndefinedBehaviorSanitizer.
- With two different constant buffers bound to VS and PS, mapping an unbound
  constant buffer dirties zero slots; mapping the VS buffer dirties one slot and
  leaves the unrelated PS table clean. The original marking block dirtied 84 slots
  and both active tables.
- Tests cover multiple bindings/stages/views of a resource, SRV slots 64/127,
  unbind/rebind, pre-existing dirty bits and retained full-set invalidation.
- Three index formats produce one ordinary build and three builds each for
  geometry/tessellation; changing other relevant state still creates a new entry.
  Different stream-output layouts create different entries, and returning to the
  first layout reuses its original entry.
- Scheduler tests cover no-dependency completions, single/fan-out continuations,
  completed-dependency registration races, sequential dependencies, a 64-job
  chain, four concurrent producers, and idle shutdown.
- A test forces preemption after an idle worker checks a false wait predicate.
  The old destructor timed out; the mutex-protected shutdown passes. An earlier
  intermittent sanitizer timeout prompted this investigation. Neither finding
  identifies the cause of the previously observed game hang.
- The HUD probe again preserved all 27 debug output cases and removed non-debug
  sink calls. Its microbenchmark is not evidence of a game FPS increase.
- All 14 existing profiling/launch/parser tests passed.

Aggregate fixture results are in [DXMT-VALIDATION-2026-10-10.json](DXMT-VALIDATION-2026-10-10.json).
No raw game traces or process samples are published.

## Reproduce

Clone the user's DXMT branch, then run from this Sikarugir checkout:

```sh
python3 tools/validate_dxmt_gameplay.py --source /path/to/dxmt --reference 332cee27aae3496d2d93e8f062145eada13a776b --repeat 10
python3 tools/validate_dxmt_gameplay.py --source /path/to/dxmt --architecture x86_64 --repeat 10
python3 tools/validate_dxmt_gameplay.py --source /path/to/dxmt --sanitize --repeat 5
python3 -m unittest discover -s tools -p 'test_*.py'
```

Alternatively, [dxmt-gameplay-performance.patch](../patches/dxmt-gameplay-performance.patch)
contains the gameplay/cache/scheduler changes without the separate HUD patch.
It applies to both the installed version's source revision
`7c8dee1c2d73415301ceb7d1fa810861cef4cd67` and the newer branch base. Applying it
to temporary exports of both revisions also passed the source fixture. Existing
LGPL source notices and licenses are preserved.

## Remaining runtime validation

Full engine compilation needs Xcode 16+ with the Metal toolchain, LLVM 15, Wine
headers/tools and a Windows cross-compiler, as described in
[DXMT's build instructions](https://github.com/yongkangc/dxmt/blob/332cee27aae3496d2d93e8f062145eada13a776b/docs/DEVELOPMENT.md).
This Mac currently has Command Line Tools rather than that full build setup.
Steam and the game were not relaunched during these checks.

Build a matching engine and use an isolated test wrapper before replacing the
installed runtime. Check dynamic rename behavior and alternating stream-output
layouts with actual rendering/output checks, then compare a repeatable warmed
Risk of Rain 2 scene and a cold compilation workload. Record binding-table
rebuilds, PSO waits and median/p95/p99 frame intervals; retain performance changes
only when repeated runs improve without rendering errors or increased hangs.
