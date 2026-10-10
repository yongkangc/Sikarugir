// Regression fixtures for extracted DXMT production code. See the Python runner.
#include <array>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <unordered_map>
#include "dxmt_binding_set.hpp"
#include "dxmt_tasks.hpp"
#include "util_hash.hpp"

using UINT = unsigned;
constexpr UINT D3D11_BIND_VERTEX_BUFFER = 1, D3D11_BIND_CONSTANT_BUFFER = 4,
               D3D11_BIND_SHADER_RESOURCE = 8;
template<class T> struct Pointer {
  T* value{};
  T* ptr() const { return value; }
  T* operator->() const { return value; }
};
struct ID3D11Resource {};
struct D3D11ResourceCommon : ID3D11Resource {};
D3D11ResourceCommon* GetResourceCommon(ID3D11Resource* resource) {
  return static_cast<D3D11ResourceCommon*>(resource);
}
struct View { Pointer<D3D11ResourceCommon> resource_; };
struct BufferBinding { Pointer<D3D11ResourceCommon> Buffer; };
struct ViewBinding { Pointer<View> SRV; };
namespace dxmt {
template<> struct redundant_binding_trait<BufferBinding> {
  static bool is_redundant(const BufferBinding& a, const BufferBinding& b) {
    return a.Buffer.ptr() == b.Buffer.ptr();
  }
};
template<> struct redundant_binding_trait<ViewBinding> {
  static bool is_redundant(const ViewBinding& a, const ViewBinding& b) {
    return a.SRV.ptr() == b.SRV.ptr();
  }
};
}
struct MapProbe {
  struct Stage {
    dxmt::BindingSet<BufferBinding, 14> ConstantBuffers;
    dxmt::BindingSet<ViewBinding, 128> SRVs;
  };
  struct State {
    struct IA { dxmt::BindingSet<BufferBinding, 32> VertexBuffers; } InputAssembler;
    std::array<Stage, 6> ShaderStages;
  } state_;
#include "invalidation.inc"
  void clear() {
    state_.InputAssembler.VertexBuffers.clear_dirty();
    for (auto& stage : state_.ShaderStages) {
      stage.ConstantBuffers.clear_dirty(); stage.SRVs.clear_dirty();
    }
  }
};
template<class E, size_t N> unsigned count_dirty(const dxmt::BindingSet<E, N>& bindings) {
  unsigned count = 0;
  for (unsigned slot = 0; slot < N; ++slot) count += bindings.test_dirty(slot);
  return count;
}
unsigned total_dirty(const MapProbe& context) {
  unsigned count = count_dirty(context.state_.InputAssembler.VertexBuffers);
  for (auto& stage : context.state_.ShaderStages)
    count += count_dirty(stage.ConstantBuffers) + count_dirty(stage.SRVs);
  return count;
}
void binding_tests() {
  D3D11ResourceCommon a, b, c, unbound;
  {
    MapProbe baseline;
    bool replaced = false;
    baseline.state_.ShaderStages[0].ConstantBuffers.bind(0, BufferBinding{{&a}}, replaced);
    baseline.state_.ShaderStages[4].ConstantBuffers.bind(0, BufferBinding{{&b}}, replaced);
    baseline.clear();
    baseline.InvalidateDynamicResourceBindings(&unbound, D3D11_BIND_CONSTANT_BUFFER);
    assert(total_dirty(baseline) == 0);
    baseline.InvalidateDynamicResourceBindings(&a, D3D11_BIND_CONSTANT_BUFFER);
    assert(total_dirty(baseline) == 1);
    assert(!baseline.state_.ShaderStages[4].ConstantBuffers.any_dirty());
  }
  View a1{{&a}}, a2{{&a}}, b1{{&b}}, c1{{&c}};
  MapProbe context;
  auto& stages = context.state_.ShaderStages;
  auto& vertices = context.state_.InputAssembler.VertexBuffers;
  bool replaced = false;
  vertices.bind(0, BufferBinding{{&a}}, replaced);
  vertices.bind(31, BufferBinding{{&b}}, replaced);
  stages[0].ConstantBuffers.bind(0, BufferBinding{{&a}}, replaced);
  stages[0].ConstantBuffers.bind(3, BufferBinding{{&a}}, replaced);
  stages[2].ConstantBuffers.bind(13, BufferBinding{{&a}}, replaced);
  stages[4].ConstantBuffers.bind(0, BufferBinding{{&b}}, replaced);
  stages[0].SRVs.bind(0, ViewBinding{{&a1}}, replaced);
  stages[0].SRVs.bind(64, ViewBinding{{&a2}}, replaced);
  stages[0].SRVs.bind(127, ViewBinding{{&b1}}, replaced);
  stages[4].SRVs.bind(12, ViewBinding{{&a2}}, replaced);
  stages[5].SRVs.bind(90, ViewBinding{{&c1}}, replaced);
  constexpr auto all = D3D11_BIND_VERTEX_BUFFER | D3D11_BIND_CONSTANT_BUFFER | D3D11_BIND_SHADER_RESOURCE;
  context.clear();
  context.InvalidateDynamicResourceBindings(&unbound, all);
  assert(total_dirty(context) == 0);
  context.InvalidateDynamicResourceBindings(&c, D3D11_BIND_CONSTANT_BUFFER | D3D11_BIND_VERTEX_BUFFER);
  assert(total_dirty(context) == 0);
  // Preserve prior invalidation, including an unbound slot awaiting null upload.
  stages[4].ConstantBuffers.set_dirty(7);
  context.InvalidateDynamicResourceBindings(&a, D3D11_BIND_CONSTANT_BUFFER);
  assert(total_dirty(context) == 4);
  assert(stages[0].ConstantBuffers.test_dirty(0) && stages[0].ConstantBuffers.test_dirty(3));
  assert(stages[2].ConstantBuffers.test_dirty(13) && stages[4].ConstantBuffers.test_dirty(7));
  assert(!stages[4].ConstantBuffers.test_dirty(0));
  context.clear();
  context.InvalidateDynamicResourceBindings(&a, all);
  assert(total_dirty(context) == 7); // 1 VB, 3 CBs, 3 SRVs across distinct views/stages.
  assert(vertices.test_dirty(0) && !vertices.test_dirty(31));
  assert(stages[0].SRVs.test_dirty(0) && stages[0].SRVs.test_dirty(64));
  assert(stages[4].SRVs.test_dirty(12) && !stages[0].SRVs.test_dirty(127));
  assert(!stages[5].SRVs.test_dirty(90));
  context.clear();
  context.InvalidateDynamicResourceBindings(&b, D3D11_BIND_SHADER_RESOURCE);
  assert(total_dirty(context) == 1 && stages[0].SRVs.test_dirty(127));
  // Bind/unbind/rebind uses the current resource; do not clear null-upload dirtiness.
  stages[0].SRVs.unbind(0);
  vertices.bind(0, BufferBinding{{&b}}, replaced);
  context.clear();
  context.InvalidateDynamicResourceBindings(&a, D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_VERTEX_BUFFER);
  assert(total_dirty(context) == 2);
  stages[0].SRVs.unbind(64);
  context.InvalidateDynamicResourceBindings(&unbound, all);
  assert(stages[0].SRVs.test_dirty(64));
  context.clear();
  // Whole-set invalidation remains available for encoder/state transitions.
  stages[0].ConstantBuffers.set_dirty();
  assert(count_dirty(stages[0].ConstantBuffers) == 14);
}

using ManagedShader = uintptr_t;
using ManagedInputLayout = uintptr_t;
using WMTPixelFormat = unsigned;
using WMTPrimitiveTopologyClass = unsigned;
using SM50_INDEX_BUFFER_FORMAT = unsigned;
constexpr unsigned SM50_INDEX_BUFFER_FORMAT_NONE = 0;
struct BlendTarget {
  unsigned RenderTargetWriteMask{}, BlendEnable{}, LogicOpEnable{}, BlendOp{}, BlendOpAlpha{},
           SrcBlend{}, SrcBlendAlpha{}, DestBlend{}, DestBlendAlpha{}, LogicOp{};
};
struct D3D11_BLEND_DESC1 {
  unsigned IndependentBlendEnable{}, AlphaToCoverageEnable{};
  BlendTarget RenderTarget[8]{};
};
struct IMTLD3D11BlendState {
  D3D11_BLEND_DESC1 desc{};
  void GetDesc1(D3D11_BLEND_DESC1* p) { *p = desc; }
};
struct IMTLD3D11StreamOutputLayout { int identity; };
#include "pipeline_key.inc"
struct MTLCompiledGraphicsPipeline { MTL_GRAPHICS_PIPELINE_DESC desc; };
struct MTLCompiledGeometryPipeline { MTL_GRAPHICS_PIPELINE_DESC desc; };
struct MTLCompiledTessellationMeshPipeline { MTL_GRAPHICS_PIPELINE_DESC desc; };
unsigned ordinary_builds = 0, geometry_builds = 0, tessellation_builds = 0;
std::unique_ptr<MTLCompiledGraphicsPipeline> CreateGraphicsPipeline(void*, MTL_GRAPHICS_PIPELINE_DESC* desc) {
  ++ordinary_builds; return std::make_unique<MTLCompiledGraphicsPipeline>(MTLCompiledGraphicsPipeline{*desc});
}
std::unique_ptr<MTLCompiledGeometryPipeline> CreateGeometryPipeline(void*, MTL_GRAPHICS_PIPELINE_DESC* desc) {
  ++geometry_builds; return std::make_unique<MTLCompiledGeometryPipeline>(MTLCompiledGeometryPipeline{*desc});
}
std::unique_ptr<MTLCompiledTessellationMeshPipeline> CreateTessellationMeshPipeline(void*, MTL_GRAPHICS_PIPELINE_DESC* desc) {
  ++tessellation_builds; return std::make_unique<MTLCompiledTessellationMeshPipeline>(MTLCompiledTessellationMeshPipeline{*desc});
}
#define D3D11_ASSERT(condition) assert(condition)
struct CacheProbe {
  dxmt::mutex mutex_, mutex_gs_, mutex_ts_;
  void* device{};
  std::unordered_map<MTL_GRAPHICS_PIPELINE_DESC, std::unique_ptr<MTLCompiledGraphicsPipeline>> pipelines_;
  std::unordered_map<MTL_GRAPHICS_PIPELINE_DESC, std::unique_ptr<MTLCompiledGeometryPipeline>> pipelines_gs_;
  std::unordered_map<MTL_GRAPHICS_PIPELINE_DESC, std::unique_ptr<MTLCompiledTessellationMeshPipeline>> pipelines_ts_;
  struct Scheduler { unsigned submissions = 0; void submit(void*) { ++submissions; } } scheduler_;
#include "pipeline_cache.inc"
};
void pipeline_tests() {
  IMTLD3D11BlendState blend, equivalent_blend;
  MTL_GRAPHICS_PIPELINE_DESC desc{};
  desc.VertexShader = 1; desc.PixelShader = 2; desc.BlendState = &blend;
  desc.NumColorAttachments = 1; desc.ColorAttachmentFormats[0] = 80;
  desc.RasterizationEnabled = true; desc.SampleCount = 1;
  CacheProbe cache;
  MTLCompiledGraphicsPipeline* first = nullptr;
  for (unsigned format = 0; format < 3; ++format) {
    desc.IndexBufferFormat = format;
    MTLCompiledGraphicsPipeline* ordinary;
    cache.GetGraphicsPipeline(&desc, &ordinary);
    if (first) assert(ordinary == first); else first = ordinary;
    assert(desc.IndexBufferFormat == format); // Caller state is never normalized in place.
    assert(ordinary->desc.IndexBufferFormat == SM50_INDEX_BUFFER_FORMAT_NONE);
    MTLCompiledGeometryPipeline* geometry;
    cache.GetGeometryPipeline(&desc, &geometry);
    assert(geometry->desc.IndexBufferFormat == format);
    MTLCompiledTessellationMeshPipeline* tessellation;
    cache.GetTessellationPipeline(&desc, &tessellation);
    assert(tessellation->desc.IndexBufferFormat == format);
  }
  assert(cache.pipelines_.size() == 1 && ordinary_builds == 1);
  assert(cache.pipelines_gs_.size() == 3 && geometry_builds == 3);
  assert(cache.pipelines_ts_.size() == 3 && tessellation_builds == 3);
  assert(cache.scheduler_.submissions == 7);
  desc.BlendState = &equivalent_blend;
  MTLCompiledGraphicsPipeline* same;
  cache.GetGraphicsPipeline(&desc, &same);
  assert(same == first && ordinary_builds == 1); // Preserve semantic blend equality/hash contract.
  auto distinct = desc;
  distinct.SampleMask = 1;
  cache.GetGraphicsPipeline(&distinct, &same);
  assert(same != first);
  distinct = desc; distinct.VertexShader = 3;
  cache.GetGraphicsPipeline(&distinct, &same);
  assert(same != first && ordinary_builds == 3);

  IMTLD3D11StreamOutputLayout layout_a{1}, layout_b{2};
  auto a = desc, b = desc;
  a.PixelShader = b.PixelShader = 0;
  a.GeometryShader = b.GeometryShader = 0;
  a.RasterizationEnabled = b.RasterizationEnabled = false;
  a.GSPassthrough = b.GSPassthrough = ~0u;
  a.SOLayout = &layout_a; b.SOLayout = &layout_b;
  auto equal = std::equal_to<MTL_GRAPHICS_PIPELINE_DESC>();
  auto hash = std::hash<MTL_GRAPHICS_PIPELINE_DESC>();
  assert(!equal(a, b) && equal(a, a) && hash(a) == hash(a));
  auto no_layout = a; no_layout.SOLayout = nullptr;
  assert(!equal(a, no_layout));
  MTLCompiledGraphicsPipeline *pa, *pb;
  cache.GetGraphicsPipeline(&a, &pa); cache.GetGraphicsPipeline(&b, &pb);
  assert(pa != pb && pa->desc.SOLayout == &layout_a && pb->desc.SOLayout == &layout_b);
  cache.GetGraphicsPipeline(&a, &same); assert(same == pa);
  assert(cache.pipelines_.size() == 5 && ordinary_builds == 5);
}

struct Task {
  std::atomic_bool done = false;
  std::atomic_uint attempts = 0;
  Task* dependency = nullptr;
  Task* second_dependency = nullptr;
  bool blocked = false, completed_dependency_once = false;
};
std::mutex gate_mutex;
std::condition_variable gate_condition;
bool released = false;
namespace dxmt {
template<> struct task_trait<Task*> {
  Task* run_task(Task* task) {
    unsigned attempt = ++task->attempts;
    if (task->blocked) {
      std::unique_lock lock(gate_mutex);
      gate_condition.wait(lock, [] { return released; });
    }
    if (task->dependency && (!task->dependency->done.load() ||
        (task->completed_dependency_once && attempt == 1))) return task->dependency;
    if (task->second_dependency && !task->second_dependency->done.load()) return task->second_dependency;
    return task;
  }
  bool get_done(Task* task) { return task->done.load(); }
  void set_done(Task* task) { assert(!task->done.exchange(true)); }
};
}
template<class P> void wait_for(P predicate) {
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
  while (!predicate()) {
    if (std::chrono::steady_clock::now() > deadline) {
      std::cerr << "scheduler progress timeout\n"; std::_Exit(2);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
}
template<size_t N> void wait_done(dxmt::task_scheduler<Task*>& scheduler, std::array<Task, N>& tasks) {
  wait_for([&] {
    for (auto& task : tasks) if (!task.done.load()) return false;
    return scheduler.get_running_threads() == 0;
  });
}
void scheduler_tests() {
  std::cerr << "scheduler: independent completions\n";
  // Keep the existing 28-worker limit and priority policy; isolate completion wakeups.
  {
    constexpr unsigned count = reported_processors * 2;
    std::array<Task, count> tasks;
    dxmt::task_scheduler<Task*> scheduler;
    unsigned broadcasts = completion_broadcasts, singles = single_notifications;
    for (unsigned i = 0; i < count; ++i) {
      tasks[i].blocked = true;
      scheduler.submit(&tasks[i]);
      wait_for([&] { return scheduler.get_running_threads() == i + 1; });
    }
    { std::lock_guard lock(gate_mutex); released = true; }
    gate_condition.notify_all();
    wait_done(scheduler, tasks);
    assert(completion_broadcasts == broadcasts);
    assert(single_notifications == singles + count); // Submission only.
    assert(created_threads == count && priority_requests == count);
  }
  // A single newly runnable continuation wakes one worker, then completes.
  std::cerr << "scheduler: single continuation\n";
  {
    std::array<Task, 2> tasks;
    tasks[1].dependency = &tasks[0];
    dxmt::task_scheduler<Task*> scheduler;
    unsigned broadcasts = completion_broadcasts, singles = single_notifications;
    scheduler.submit(&tasks[1]);
    wait_for([&] { return tasks[1].attempts.load() && scheduler.get_running_threads() == 0; });
    scheduler.submit(&tasks[0]);
    wait_done(scheduler, tasks);
    assert(tasks[1].attempts == 2);
    assert(completion_broadcasts == broadcasts && single_notifications == singles + 3);
  }
  // Fan-out, with dependents registered before the root completes.
  std::cerr << "scheduler: fan-out\n";
  {
    std::array<Task, 9> tasks;
    dxmt::task_scheduler<Task*> scheduler;
    unsigned broadcasts = completion_broadcasts;
    for (unsigned i = 1; i < tasks.size(); ++i) {
      tasks[i].dependency = &tasks[0]; scheduler.submit(&tasks[i]);
    }
    wait_for([&] {
      for (unsigned i = 1; i < tasks.size(); ++i) if (!tasks[i].attempts.load()) return false;
      return scheduler.get_running_threads() == 0;
    });
    scheduler.submit(&tasks[0]); wait_done(scheduler, tasks);
    assert(completion_broadcasts == broadcasts + 1);
    for (unsigned i = 1; i < tasks.size(); ++i) assert(tasks[i].attempts == 2);
  }
  // Dependencies completed between run_task and registration retry immediately.
  std::cerr << "scheduler: completed dependency race\n";
  {
    std::array<Task, 2> tasks;
    tasks[0].done = true; tasks[1].dependency = &tasks[0];
    tasks[1].completed_dependency_once = true;
    dxmt::task_scheduler<Task*> scheduler;
    unsigned broadcasts = completion_broadcasts;
    scheduler.submit(&tasks[1]); wait_done(scheduler, tasks);
    assert(tasks[1].attempts == 2 && completion_broadcasts == broadcasts);
  }
  // A job can suspend on a second dependency after its first continuation resumes.
  std::cerr << "scheduler: second dependency\n";
  {
    std::array<Task, 3> tasks;
    tasks[2].dependency = &tasks[0]; tasks[2].second_dependency = &tasks[1];
    dxmt::task_scheduler<Task*> scheduler;
    scheduler.submit(&tasks[2]);
    wait_for([&] { return tasks[2].attempts == 1 && scheduler.get_running_threads() == 0; });
    scheduler.submit(&tasks[0]);
    wait_for([&] { return tasks[2].attempts == 2 && scheduler.get_running_threads() == 0; });
    scheduler.submit(&tasks[1]); wait_done(scheduler, tasks);
    assert(tasks[2].attempts == 3);
  }
  // Reverse-submitted dependency chain and concurrent producers.
  std::cerr << "scheduler: chain\n";
  {
    std::array<Task, 64> tasks;
    dxmt::task_scheduler<Task*> scheduler;
    for (unsigned i = tasks.size() - 1; i > 0; --i) {
      tasks[i].dependency = &tasks[i - 1]; scheduler.submit(&tasks[i]);
    }
    scheduler.submit(&tasks[0]); wait_done(scheduler, tasks);
  }
  std::cerr << "scheduler: concurrent producers\n";
  {
    std::array<Task, 128> tasks;
    dxmt::task_scheduler<Task*> scheduler;
    std::vector<std::thread> producers;
    unsigned broadcasts = completion_broadcasts;
    for (unsigned producer = 0; producer < 4; ++producer)
      producers.emplace_back([&, producer] {
        for (unsigned i = producer; i < tasks.size(); i += 4) scheduler.submit(&tasks[i]);
      });
    for (auto& producer : producers) producer.join();
    wait_done(scheduler, tasks);
    assert(completion_broadcasts == broadcasts);
  }
  // Shutdown still wakes idle workers and joins them.
  std::cerr << "scheduler: idle shutdown\n";
  for (unsigned i = 0; i < 20; ++i) { dxmt::task_scheduler<Task*> scheduler; }
  // Force the window between an idle worker's predicate check and actual wait.
  std::cerr << "scheduler: shutdown predicate/wait race\n";
  pause_shutdown_wait = true;
  auto scheduler = std::make_unique<dxmt::task_scheduler<Task*>>();
  wait_for([] { return shutdown_wait_entered.load(); });
  scheduler.reset();
}

int main() {
  binding_tests(); pipeline_tests(); scheduler_tests();
  std::cout << R"({"binding_regressions":"passed","pipeline_regressions":"passed","scheduler_regressions":"passed","shutdown_wait_race":"passed","unbound_constant_buffer_dirty_slots":0,"constant_buffer_dirty_slots_for_single_binding":1,"ordinary_index_variants_builds":1,"geometry_index_variants_builds":3,"tessellation_index_variants_builds":3,"completion_broadcasts_without_dependents":0,"shader_and_render_validation":false})" << '\n';
}
