#!/usr/bin/env python3
"""Reproduce selected DXMT hot-path behaviors without launching or changing Wine.

Compiles actual source blocks/templates at a pinned revision. Resource, shader
and Win32 types are substitutes; results are operation counts, never game FPS.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

REFERENCE = "7c8dee1c2d73415301ceb7d1fa810861cef4cd67"
FILES = ["src/dxmt/dxmt_binding_set.hpp", "src/dxmt/dxmt_tasks.hpp",
         "src/util/util_bit.hpp", "src/util/util_likely.hpp", "src/util/util_math.hpp",
         "src/util/util_hash.hpp", "src/d3d11/d3d11_pipeline.hpp",
         "src/d3d11/d3d11_context_imm.cpp"]

THREAD_SHIM = r'''
#pragma once
#include <atomic>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <thread>
#include <vector>
inline std::atomic<unsigned> created_threads = 0, completion_broadcasts = 0;
inline std::atomic<unsigned> priority_requests = 0;
inline constexpr unsigned reported_processors = 14;
inline constexpr int THREAD_PRIORITY_TIME_CRITICAL = 15;
inline int GetCurrentThread() { return 0; }
inline bool SetThreadPriority(int, int) { ++priority_requests; return true; }
namespace dxmt {
using mutex = std::mutex;
class thread : public std::thread {
public:
  template<class F> explicit thread(F&& f) : std::thread(std::forward<F>(f)) { ++created_threads; }
  thread(thread&&) = default;
  static unsigned hardware_concurrency() { return reported_processors; }
};
class condition_variable {
  std::condition_variable value;
public:
  void notify_all() { ++completion_broadcasts; value.notify_all(); }
  void notify_one() { value.notify_one(); }
  template<class P> void wait(std::unique_lock<mutex>& lock, P predicate) { value.wait(lock, predicate); }
};
}
'''

PREFIX = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <limits>
#include <unordered_map>
#include "dxmt_binding_set.hpp"
#include "dxmt_tasks.hpp"
#include "util_hash.hpp"
struct Binding { unsigned identity = 0; };
namespace dxmt {
template<> struct redundant_binding_trait<Binding> {
  static bool is_redundant(const Binding& a, const Binding& b) { return a.identity == b.identity; }
};
}
constexpr unsigned D3D11_BIND_CONSTANT_BUFFER = 16;
using UINT = unsigned;
using ManagedShader = uintptr_t;
using ManagedInputLayout = uintptr_t;
using WMTPixelFormat = unsigned;
using WMTPrimitiveTopologyClass = unsigned;
using SM50_INDEX_BUFFER_FORMAT = unsigned;
struct BlendTarget {
  unsigned RenderTargetWriteMask{}, BlendEnable{}, LogicOpEnable{}, BlendOp{}, BlendOpAlpha{},
           SrcBlend{}, SrcBlendAlpha{}, DestBlend{}, DestBlendAlpha{}, LogicOp{};
};
struct D3D11_BLEND_DESC1 {
  unsigned IndependentBlendEnable{}, AlphaToCoverageEnable{};
  BlendTarget RenderTarget[8]{};
};
struct IMTLD3D11BlendState { void GetDesc1(D3D11_BLEND_DESC1* p) { *p = {}; } };
struct IMTLD3D11StreamOutputLayout { int identity; };
'''

MAIN = r'''
std::mutex gate_mutex;
std::condition_variable gate_condition;
bool released = false;
std::atomic<unsigned> done_tasks = 0;
struct Task { std::atomic_bool done = false; };
namespace dxmt {
template<> struct task_trait<Task*> {
  Task* run_task(Task* task) {
    std::unique_lock lock(gate_mutex);
    gate_condition.wait(lock, [] { return released; });
    return task;
  }
  bool get_done(Task* task) { return task->done.load(); }
  void set_done(Task* task) { task->done.store(true); ++done_tasks; }
};
}
void release_tasks() {
  { std::lock_guard lock(gate_mutex); released = true; }
  gate_condition.notify_all();
}
int main() {
  MapProbe context;
  bool replacement = false;
  context.state_.ShaderStages[0].ConstantBuffers.bind(0, Binding{1}, replacement);
  context.state_.ShaderStages[4].ConstantBuffers.bind(0, Binding{2}, replacement);
  for (auto& stage : context.state_.ShaderStages) stage.ConstantBuffers.clear_dirty();
  context.discard(D3D11_BIND_CONSTANT_BUFFER);
  unsigned dirty_slots = 0;
  for (auto& stage : context.state_.ShaderStages)
    for (unsigned i = 0; i < 14; ++i) dirty_slots += stage.ConstantBuffers.test_dirty(i);
  unsigned dirty_active_tables = context.state_.ShaderStages[0].ConstantBuffers.any_dirty_masked(uint16_t(1)) +
                                 context.state_.ShaderStages[4].ConstantBuffers.any_dirty_masked(uint16_t(1));
  assert(dirty_slots == 84 && dirty_active_tables == 2);

  IMTLD3D11BlendState blend;
  MTL_GRAPHICS_PIPELINE_DESC desc{};
  desc.VertexShader = 1; desc.PixelShader = 2; desc.BlendState = &blend;
  desc.NumColorAttachments = 1; desc.ColorAttachmentFormats[0] = 80;
  desc.RasterizationEnabled = true; desc.SampleCount = 1;
  std::unordered_map<MTL_GRAPHICS_PIPELINE_DESC, int> pipelines;
  for (unsigned format = 0; format < 3; ++format) {
    desc.IndexBufferFormat = format;
    pipelines.insert({desc, format});
  }
  assert(pipelines.size() == 3);
  IMTLD3D11StreamOutputLayout layout_a{1}, layout_b{2};
  auto a = desc, b = desc;
  // Match the supported stream-output path: null managed GS, no rasterization.
  a.PixelShader = b.PixelShader = 0;
  a.GeometryShader = b.GeometryShader = 0;
  a.RasterizationEnabled = b.RasterizationEnabled = false;
  a.GSPassthrough = b.GSPassthrough = ~0u;
  a.SOLayout = &layout_a; b.SOLayout = &layout_b;
  bool layouts_alias = std::equal_to<MTL_GRAPHICS_PIPELINE_DESC>()(a, b) &&
                       std::hash<MTL_GRAPHICS_PIPELINE_DESC>()(a) == std::hash<MTL_GRAPHICS_PIPELINE_DESC>()(b);
  assert(layouts_alias);

  constexpr unsigned count = reported_processors * 2;
  std::array<Task, count> tasks;
  dxmt::task_scheduler<Task*> scheduler;
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
  for (unsigned i = 0; i < count; ++i) {
    scheduler.submit(&tasks[i]);
    while (scheduler.get_running_threads() < i + 1) {
      if (std::chrono::steady_clock::now() > deadline) { release_tasks(); return 2; }
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  }
  release_tasks();
  while (done_tasks.load() != count || scheduler.get_running_threads() != 0) {
    if (std::chrono::steady_clock::now() > deadline) return 3;
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  assert(created_threads == count && priority_requests == count && completion_broadcasts == count);
  std::cout << "{\"constant_buffer_dirty_slots\":" << dirty_slots
            << ",\"dirty_active_shader_tables\":" << dirty_active_tables
            << ",\"ordinary_index_format_cache_entries\":" << pipelines.size()
            << ",\"different_stream_output_layouts_alias\":" << (layouts_alias ? "true" : "false")
            << ",\"fixture_reported_processors\":" << reported_processors
            << ",\"fixture_created_workers\":" << created_threads
            << ",\"fixture_priority_requests\":" << priority_requests
            << ",\"completion_broadcasts_without_dependents\":" << completion_broadcasts << "}\n";
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="DXMT git checkout containing the reference")
    parser.add_argument("--reference", default=REFERENCE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dxmt-gameplay-audit-") as temporary:
        folder = Path(temporary)
        sources = {}
        for name in FILES:
            source = subprocess.run(["git", "show", f"{args.reference}:{name}"], cwd=args.source,
                                    check=True, capture_output=True, text=True).stdout
            sources[name] = source
            file = folder / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(source)
        shim = folder / "shim"
        shim.mkdir()
        (shim / "thread.hpp").write_text(THREAD_SHIM)
        (shim / "util_win32_compat.h").write_text("#pragma once\n")
        source = sources["src/d3d11/d3d11_context_imm.cpp"]
        start = source.index("        if (bind_flag & D3D11_BIND_CONSTANT_BUFFER) {")
        end = source.index("        if (bind_flag & D3D11_BIND_SHADER_RESOURCE)", start)
        marking = source[start:end]
        context = ("struct MapProbe { struct Stage { dxmt::BindingSet<Binding, 14> ConstantBuffers; }; "
                   "struct State { std::array<Stage,6> ShaderStages; } state_; "
                   "void discard(unsigned bind_flag) {\n" + marking + "\n} };\n")
        header = sources["src/d3d11/d3d11_pipeline.hpp"]
        start = header.index("struct MTL_GRAPHICS_PIPELINE_DESC {")
        end = header.index("struct MTL_COMPUTE_PIPELINE_DESC", start)
        descriptor = header[start:end]
        start = header.index("namespace std {")
        end = header.index("} // namespace std", start) + len("} // namespace std")
        key_functions = header[start:end]
        file = folder / "probe.cpp"
        file.write_text(PREFIX + context + descriptor + key_functions + MAIN)
        binary = folder / "probe"
        built = subprocess.run(["xcrun", "clang++", "-std=c++20", "-O2", "-I", str(shim),
                                "-I", str(folder / "src/dxmt"), "-I", str(folder / "src/util"),
                                str(file), "-o", str(binary)], capture_output=True, text=True)
        if built.returncode:
            raise RuntimeError(f"Audit probe compilation failed:\n{built.stderr}")
        result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=20)
        result = json.loads(result.stdout)
    result.update({"source_revision": args.reference,
                   "scope": "production blocks/templates; substitute resource/Win32 types and native threads",
                   "gameplay_cost_measured": False})
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
