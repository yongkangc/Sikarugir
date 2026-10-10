// Regression fixtures for extracted production DXMT hot paths. No Metal/Wine.
#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <utility>
#include "dxmt_binding_set.hpp"

#define STDMETHODCALLTYPE
#define IID_PPV_ARGS(p) p
#define ERR(...)
using UINT = unsigned;
using mutex_t = std::recursive_mutex;
using HRESULT = int;
using D3D11_MAP = unsigned;
struct D3D11_MAPPED_SUBRESOURCE {};
constexpr HRESULT E_INVALIDARG = -1, S_OK = 0;
using SM50_INDEX_BUFFER_FORMAT = unsigned;
constexpr unsigned SM50_INDEX_BUFFER_FORMAT_NONE = 0, SM50_INDEX_BUFFER_FORMAT_UINT16 = 1,
                   SM50_INDEX_BUFFER_FORMAT_UINT32 = 2, DXGI_FORMAT_R32_UINT = 42, DXGI_FORMAT_R16_UINT = 57;
enum PipelineStage { Vertex, Hull, Domain, Geometry, Pixel, Compute };
enum class PipelineKind { Ordinary, Geometry, Tessellation };
constexpr unsigned D3D11_BIND_VERTEX_BUFFER = 1, D3D11_BIND_SHADER_RESOURCE = 8;
template<class T> struct Pointer {
  T* value{};
  Pointer() = default;
  Pointer(T* p) : value(p) {}
  Pointer& operator=(T* p) { value = p; return *this; }
  T* ptr() const { return value; }
  T* operator->() const { return value; }
  explicit operator bool() const { return value; }
};
template<class T, bool = true> using Com = Pointer<T>;
template<class T> T forward_rc(T& object) { return object; }
struct Buffer {
  uint64_t byte_length = 1024;
  uint64_t length() { return byte_length; }
};
struct D3D11_BUFFER_DESC { UINT ByteWidth; };
struct Resource {
  unsigned flags = D3D11_BIND_VERTEX_BUFFER | D3D11_BIND_SHADER_RESOURCE;
  bool allowed = true, hazard_free = false;
  Buffer storage;
  unsigned desc_reads = 0;
  unsigned bindFlags() const { return flags; }
  bool hazardsFree() const { return hazard_free; }
  Pointer<Buffer> buffer() { return &storage; }
  void GetDesc(D3D11_BUFFER_DESC* out) { ++desc_reads; out->ByteWidth = storage.length(); }
  void QueryInterface(Resource** out) { *out = this; }
};
using ID3D11Buffer = Resource;
using ID3D11Resource = Resource;
using D3D11ResourceCommon = Resource;
D3D11ResourceCommon* GetResourceCommon(ID3D11Buffer* p) { return p; }
struct Layout {};
using ID3D11InputLayout = Layout;
using IMTLD3D11InputLayout = Layout;
template<class T> Pointer<T> com_cast(T* object) { return object; }
struct BufferBinding {
  void* RawPointer{}; Com<Resource, false> Buffer;
  UINT Stride{}, Offset{}, FirstConstant{}, NumConstants{};
};
struct View { Resource* resource; unsigned bindFlags() const { return resource->flags; } };
struct ViewBinding { void* RawPointer{}; Pointer<View> SRV; };
struct SimpleBinding { unsigned identity; };
namespace dxmt {
template<> struct redundant_binding_trait<BufferBinding> {
  static bool is_redundant(const BufferBinding& a, const BufferBinding& b) { return a.RawPointer == b.RawPointer; }
};
template<> struct redundant_binding_trait<ViewBinding> {
  static bool is_redundant(const ViewBinding& a, const ViewBinding& b) { return a.RawPointer == b.RawPointer; }
};
template<> struct redundant_binding_trait<SimpleBinding> {
  static bool is_redundant(const SimpleBinding& a, const SimpleBinding& b) { return a.identity == b.identity; }
};
}
bool CheckOverlap(View* a, View* b) { return a && b && a->resource == b->resource; }
struct MTL_SHADER_REFLECTION {
  uint16_t ConstantBufferSlotMask{}, SamplerSlotMask{};
  uint64_t SRVSlotMaskHi{}, SRVSlotMaskLo{}, UAVSlotMask{};
  unsigned NumConstantBuffers{}, NumArguments{}, ArgumentTableQwords{};
};
struct ManagedShader {
  MTL_SHADER_REFLECTION info;
  const MTL_SHADER_REFLECTION& reflection() { return info; }
  const unsigned* constant_buffers_info() { return nullptr; }
  const unsigned* arguments_info() { return nullptr; }
};
struct Shader {
  ManagedShader managed;
  ManagedShader* GetManagedShader() { return &managed; }
};
struct ArgumentEncodingContext {
  unsigned vertex_offset_updates = 0, vertex_binds = 0, srv_null_binds = 0;
  unsigned resource_uploads = 0, constant_uploads = 0;
  unsigned current_uav_bindings = 0, table_uav_bindings = 0;
  std::array<unsigned, 32> vertex_offsets{}, vertex_strides{};
  std::array<unsigned, 14> constant_offsets{};
  void bindVertexBufferOffset(unsigned slot, unsigned offset, unsigned stride) {
    ++vertex_offset_updates; vertex_offsets[slot] = offset; vertex_strides[slot] = stride;
  }
  void bindVertexBuffer(unsigned slot, unsigned offset, unsigned stride, Pointer<Buffer>) {
    ++vertex_binds; vertex_offsets[slot] = offset; vertex_strides[slot] = stride;
  }
  template<PipelineStage> void bindConstantBufferOffset(unsigned slot, unsigned offset) {
    constant_offsets[slot] = offset;
  }
  template<PipelineStage> void bindConstantBuffer(unsigned slot, unsigned offset, Pointer<Buffer>) {
    constant_offsets[slot] = offset;
  }
  template<PipelineStage> void bindBuffer(unsigned, Pointer<Buffer>, uint64_t, unsigned) { ++srv_null_binds; }
  template<PipelineStage, PipelineKind> void encodeConstantBuffers(const MTL_SHADER_REFLECTION*, const unsigned*, uint64_t) {
    ++constant_uploads;
  }
  template<PipelineStage, PipelineKind> void encodeShaderResources(const MTL_SHADER_REFLECTION*, const unsigned*, uint64_t) {
    ++resource_uploads; table_uav_bindings = current_uav_bindings;
  }
};
struct Context {
  using DrawCallStatus = unsigned;
  enum class CommandBufferState { RenderPipelineReady, GeometryRenderPipelineReady, TessellationRenderPipelineReady };
  CommandBufferState cmdbuf_state = CommandBufferState::RenderPipelineReady;
  struct Stage {
    dxmt::BindingSet<BufferBinding, 14> ConstantBuffers;
    dxmt::BindingSet<ViewBinding, 128> SRVs;
    dxmt::BindingSet<SimpleBinding, 16> Samplers;
    Pointer<Shader> Shader;
  };
  struct State {
    struct IA {
      dxmt::BindingSet<BufferBinding, 32> VertexBuffers;
      Pointer<Layout> InputLayout;
      unsigned IndexBufferFormat = DXGI_FORMAT_R16_UINT;
    } InputAssembler;
    std::array<Stage, 6> ShaderStages;
    struct UAVState { dxmt::BindingSet<SimpleBinding, 64> UAVs; } ComputeStageUAV, OutputMerger;
  } state_;
  mutex_t mutex;
  ArgumentEncodingContext enc;
  unsigned commands = 0, invalidations = 0, allocations = 0;
  bool ValidateIAHazard(Resource* resource) { return resource->allowed; }
  void InvalidateRenderPipeline() { ++invalidations; }
  uint64_t PreAllocateArgumentBuffer(size_t, size_t) { return allocations++; }
  template<class F> void EmitST(F&& callback) { ++commands; callback(enc); }
#include "vertex.inc"
#include "layout.inc"
#include "constant.inc"
#include "get_constant.inc"
#include "hazard.inc"
#include "upload.inc"
#include "graphics_upload.inc"
};

struct IndexPipelineProbe {
  enum class DrawCallStatus { Invalid, Ordinary, Geometry, Tessellation };
  enum class CommandBufferState { Idle, RenderEncoderActive, RenderPipelineReady,
    TessellationRenderPipelineReady, GeometryRenderPipelineReady };
  struct State { struct IA { unsigned IndexBufferFormat = DXGI_FORMAT_R16_UINT; } InputAssembler; } state_;
  CommandBufferState cmdbuf_state = CommandBufferState::Idle, previous_render_pipeline_state = CommandBufferState::Idle;
  SM50_INDEX_BUFFER_FORMAT render_pipeline_index_format = SM50_INDEX_BUFFER_FORMAT_NONE;
#include "index_pipeline.inc"
};
struct DeferredGuardProbe {
#include "deferred_guard.inc"
};

unsigned hazard_count(const auto& bindings) {
  unsigned count = 0;
  for (auto it = bindings.hazard_begin(); it != bindings.hazard_end(); ++it) ++count;
  return count;
}
struct Results {
  unsigned redundant_vertex_commands, redundant_layout_invalidations;
  unsigned missing_hazard_slots, stale_hazard_slots, unresolved_hazard_slots;
  unsigned uav_unbind_refreshes, legacy_cb_first, legacy_cb_count, unbound_cb_first, unbound_cb_count;
  bool geometry_index_change_rebuild, tessellation_index_change_rebuild, deferred_null_guard;
  unsigned graphics_unbind_refreshes;
};
void index_pipeline_tests(Results& result) {
  using State = IndexPipelineProbe::CommandBufferState;
  using Status = IndexPipelineProbe::DrawCallStatus;
  for (auto kind : {State::GeometryRenderPipelineReady, State::TessellationRenderPipelineReady}) {
    IndexPipelineProbe context;
    context.cmdbuf_state = kind;
    context.render_pipeline_index_format = SM50_INDEX_BUFFER_FORMAT_UINT16;
    auto lookup = [&]<bool indexed>() {
      return kind == State::GeometryRenderPipelineReady ? context.FinalizeGeometryRenderPipeline<indexed>()
                                                       : context.FinalizeTessellationRenderPipeline<indexed>();
    };
    auto status = lookup.template operator()<true>();
    assert(status != Status::Invalid); // Same key keeps the ready fast path.
    context.state_.InputAssembler.IndexBufferFormat = DXGI_FORMAT_R32_UINT;
    status = lookup.template operator()<true>();
    bool rebuilt = status == Status::Invalid;
    assert(rebuilt == bool(EXPECT_FIXED));
    if (kind == State::GeometryRenderPipelineReady) result.geometry_index_change_rebuild = rebuilt;
    else result.tessellation_index_change_rebuild = rebuilt;
    if (EXPECT_FIXED) assert(context.previous_render_pipeline_state == kind);
    context.cmdbuf_state = kind; // Model successful pipeline setup for the new key.
    context.render_pipeline_index_format = SM50_INDEX_BUFFER_FORMAT_UINT32;
    assert(lookup.template operator()<true>() != Status::Invalid);
    assert((lookup.template operator()<false>() == Status::Invalid) == bool(EXPECT_FIXED));
    context.cmdbuf_state = kind; context.render_pipeline_index_format = SM50_INDEX_BUFFER_FORMAT_NONE;
    assert(lookup.template operator()<false>() != Status::Invalid);
    assert((lookup.template operator()<true>() == Status::Invalid) == bool(EXPECT_FIXED));
  }
  DeferredGuardProbe deferred;
  Resource resource;
  D3D11_MAPPED_SUBRESOURCE mapped;
  result.deferred_null_guard = deferred.Map(nullptr, 0, 4, 0, &mapped) == E_INVALIDARG;
  assert(result.deferred_null_guard == bool(EXPECT_FIXED));
  assert((deferred.Map(&resource, 0, 4, 0, nullptr) == E_INVALIDARG) == bool(EXPECT_FIXED));
  assert(deferred.Map(&resource, 0, 4, 0, &mapped) == S_OK);
}
void vertex_test(Results& result) {
  Context context;
  Resource a, b;
  ID3D11Buffer* buffers[] = {&a, &b};
  UINT strides[] = {16, 32}, offsets[] = {0, 256};
  context.SetVertexBuffers(0, 2, buffers, strides, offsets);
  auto commands = context.commands;
  context.state_.InputAssembler.VertexBuffers.clear_dirty();
  for (unsigned i = 0; i < 1000; ++i) context.SetVertexBuffers(0, 2, buffers, strides, offsets);
  result.redundant_vertex_commands = context.commands - commands;
  assert(result.redundant_vertex_commands == (EXPECT_FIXED ? 0 : 2000));
  assert(!context.state_.InputAssembler.VertexBuffers.any_dirty());
  strides[1] = 48; offsets[1] = 512;
  commands = context.commands;
  context.SetVertexBuffers(0, 2, buffers, strides, offsets);
  assert(context.commands - commands == (EXPECT_FIXED ? 1 : 2));
  assert(context.enc.vertex_strides[1] == 48 && context.enc.vertex_offsets[1] == 512);
  assert(context.state_.InputAssembler.VertexBuffers.test_dirty(1));
  assert(!context.state_.InputAssembler.VertexBuffers.test_dirty(0));
  // Null strides/offsets retain existing values in this implementation.
  context.SetVertexBuffers(0, 2, buffers, nullptr, nullptr);
  assert(context.enc.vertex_strides[1] == 48 && context.enc.vertex_offsets[1] == 512);
  a.allowed = false; context.SetVertexBuffers(0, 1, buffers, strides, offsets);
  assert(!context.state_.InputAssembler.VertexBuffers.test_bound(0));
  assert(context.enc.vertex_strides[0] == 0);
  a.allowed = true; context.SetVertexBuffers(0, 1, buffers, strides, offsets);
  assert(context.state_.InputAssembler.VertexBuffers.test_bound(0));
}
void layout_test(Results& result) {
  Context context;
  Layout a, b;
  context.IASetInputLayout(&a);
  auto invalidations = context.invalidations;
  for (unsigned i = 0; i < 1000; ++i) context.IASetInputLayout(&a);
  result.redundant_layout_invalidations = context.invalidations - invalidations;
  assert(result.redundant_layout_invalidations == (EXPECT_FIXED ? 0 : 1000));
  context.IASetInputLayout(&b); assert(context.state_.InputAssembler.InputLayout.ptr() == &b);
  context.IASetInputLayout(nullptr); assert(!context.state_.InputAssembler.InputLayout);
  invalidations = context.invalidations;
  context.IASetInputLayout(nullptr);
  assert(context.invalidations - invalidations == (EXPECT_FIXED ? 0 : 1));
}
void hazard_test(Results& result) {
  dxmt::BindingSet<SimpleBinding, 128> bindings;
  bool replaced = false;
  bindings.bind(127, SimpleBinding{1}, replaced, false);
  bindings.bind(127, SimpleBinding{2}, replaced, true);
  result.missing_hazard_slots = 1 - hazard_count(bindings);
  assert(result.missing_hazard_slots == (EXPECT_FIXED ? 0 : 1));
  bindings.unbind(127);
  bindings.bind(64, SimpleBinding{3}, replaced, true);
  bindings.bind(64, SimpleBinding{4}, replaced, false);
  result.stale_hazard_slots = hazard_count(bindings);
  assert(result.stale_hazard_slots == (EXPECT_FIXED ? 0 : 1));
  bindings.unbind(64); assert(hazard_count(bindings) == 0);
  Context context;
  Resource read_only, output_resource;
  View first{&read_only}, replacement{&output_resource}, output{&output_resource};
  auto& srvs = context.state_.ShaderStages[Pixel].SRVs;
  srvs.bind(127, ViewBinding{&first, &first}, replaced, false);
  srvs.bind(127, ViewBinding{&replacement, &replacement}, replaced, true);
  context.ResolveSRVHazard<Pixel>(&output);
  result.unresolved_hazard_slots = srvs.test_bound(127);
  assert(result.unresolved_hazard_slots == (EXPECT_FIXED ? 0 : 1));
  assert(context.enc.srv_null_binds == (EXPECT_FIXED ? 1 : 0));
}
template<PipelineStage stage> unsigned uav_test(unsigned slot) {
  Context context;
  Shader shader;
  shader.managed.info.UAVSlotMask = uint64_t(1) << slot;
  shader.managed.info.NumArguments = 1; shader.managed.info.ArgumentTableQwords = 2;
  context.state_.ShaderStages[stage].Shader = &shader;
  auto& uavs = stage == Compute ? context.state_.ComputeStageUAV.UAVs : context.state_.OutputMerger.UAVs;
  auto upload = [&] {
    if constexpr (stage == Compute) context.UploadShaderStageResourceBinding<stage, PipelineKind::Ordinary>();
    else context.UploadGraphicsResources();
  };
  bool replaced = false;
  uavs.bind(slot, SimpleBinding{1}, replaced);
  context.enc.current_uav_bindings = 1;
  upload();
  assert(context.enc.resource_uploads == 1 && context.enc.table_uav_bindings == 1);
  upload();
  assert(context.enc.resource_uploads == 2); // Keep per-use access tracking for bound UAVs.
  uavs.unbind(slot); context.enc.current_uav_bindings = 0;
  upload();
  unsigned refreshes = context.enc.resource_uploads - 2;
  assert(refreshes == (EXPECT_FIXED ? 1 : 0));
  assert(context.enc.table_uav_bindings == (EXPECT_FIXED ? 0 : 1));
  upload();
  assert(context.enc.resource_uploads == 2 + refreshes);
  uavs.bind((slot + 1) % 64, SimpleBinding{2}, replaced);
  upload();
  assert(context.enc.resource_uploads == 2 + refreshes); // Unreflected slots do not trigger uploads.
  return refreshes;
}
unsigned graphics_uav_test() {
  Context context;
  context.cmdbuf_state = Context::CommandBufferState::GeometryRenderPipelineReady;
  Shader shader;
  shader.managed.info.UAVSlotMask = 1;
  shader.managed.info.NumArguments = 1; shader.managed.info.ArgumentTableQwords = 2;
  for (auto stage : {Vertex, Pixel, Geometry}) context.state_.ShaderStages[stage].Shader = &shader;
  auto& uavs = context.state_.OutputMerger.UAVs;
  bool replaced = false;
  uavs.bind(0, SimpleBinding{1}, replaced);
  context.enc.current_uav_bindings = 1;
  context.UploadGraphicsResources();
  assert(context.enc.resource_uploads == 3);
  uavs.unbind(0); context.enc.current_uav_bindings = 0;
  context.UploadGraphicsResources();
  unsigned refreshed = context.enc.resource_uploads - 3;
  assert(refreshed == (EXPECT_FIXED ? 3 : 0));
  context.UploadGraphicsResources();
  assert(context.enc.resource_uploads == 3 + refreshed);
  // No pixel shader: avoid leaving OM dirtiness pending forever after the VS refresh.
  context.state_.ShaderStages[Pixel].Shader = nullptr;
  uavs.bind(0, SimpleBinding{2}, replaced); context.enc.current_uav_bindings = 1;
  context.UploadGraphicsResources();
  unsigned uploads = context.enc.resource_uploads;
  uavs.unbind(0); context.enc.current_uav_bindings = 0;
  context.UploadGraphicsResources();
  assert(context.enc.resource_uploads == uploads + (EXPECT_FIXED ? 2 : 0));
  uploads = context.enc.resource_uploads;
  context.UploadGraphicsResources(); assert(context.enc.resource_uploads == uploads);
  return refreshed;
}
void constant_test(Results& result) {
  Context context;
  Resource a;
  ID3D11Buffer* buffers[] = {&a};
  UINT first[] = {16}, count[] = {16};
  context.SetConstantBuffer<Vertex>(0, 1, buffers, first, count);
  assert(context.enc.constant_offsets[0] == 256);
  context.state_.ShaderStages[Vertex].ConstantBuffers.clear_dirty();
  unsigned commands = context.commands;
  context.SetConstantBuffer<Vertex>(0, 1, buffers, first, count);
  assert(context.commands == commands && !context.state_.ShaderStages[Vertex].ConstantBuffers.any_dirty());
  context.SetConstantBuffer<Vertex>(0, 1, buffers, nullptr, nullptr);
  auto& entry = context.state_.ShaderStages[Vertex].ConstantBuffers[0];
  result.legacy_cb_first = entry.FirstConstant; result.legacy_cb_count = entry.NumConstants;
  assert(entry.FirstConstant == (EXPECT_FIXED ? 0 : 16));
  assert(entry.NumConstants == (EXPECT_FIXED ? 64 : 16));
  assert(context.enc.constant_offsets[0] == (EXPECT_FIXED ? 0 : 256));
  if (EXPECT_FIXED) {
    context.state_.ShaderStages[Vertex].ConstantBuffers.clear_dirty();
    commands = context.commands;
    context.SetConstantBuffer<Vertex>(0, 1, buffers, nullptr, nullptr);
    assert(context.commands == commands && !context.state_.ShaderStages[Vertex].ConstantBuffers.any_dirty());
  }
  first[0] = 32; count[0] = 32;
  context.SetConstantBuffer<Vertex>(0, 1, buffers, first, count);
  assert(entry.FirstConstant == 32 && entry.NumConstants == 32 && context.enc.constant_offsets[0] == 512);
  ID3D11Buffer* returned_buffer = nullptr;
  UINT bound_first = 0, bound_count = 0;
  context.GetConstantBuffer<Vertex>(0, 1, &returned_buffer, &bound_first, &bound_count);
  assert(returned_buffer == &a && bound_first == 32 && bound_count == 32);
  buffers[0] = nullptr;
  context.SetConstantBuffer<Vertex>(0, 1, buffers, nullptr, nullptr);
  UINT returned_first = 0xdead, returned_count = 0xbeef;
  context.GetConstantBuffer<Vertex>(0, 1, nullptr, &returned_first, &returned_count);
  result.unbound_cb_first = returned_first; result.unbound_cb_count = returned_count;
  assert(returned_first == (EXPECT_FIXED ? 0 : 0xdead));
  assert(returned_count == (EXPECT_FIXED ? 0 : 0xbeef));
  returned_buffer = &a;
  context.GetConstantBuffer<Vertex>(0, 1, &returned_buffer, nullptr, nullptr);
  assert(!returned_buffer);
}
int main() {
  Results result{};
  vertex_test(result); layout_test(result); hazard_test(result); constant_test(result); index_pipeline_tests(result);
  result.uav_unbind_refreshes = uav_test<Compute>(0);
  uav_test<Compute>(63); uav_test<Pixel>(0); uav_test<Pixel>(63);
  result.graphics_unbind_refreshes = graphics_uav_test();
  std::cout << "{\"redundant_vertex_commands\":" << result.redundant_vertex_commands
            << ",\"redundant_layout_invalidations\":" << result.redundant_layout_invalidations
            << ",\"missing_hazard_slots\":" << result.missing_hazard_slots
            << ",\"stale_hazard_slots\":" << result.stale_hazard_slots
            << ",\"unresolved_hazard_slots\":" << result.unresolved_hazard_slots
            << ",\"uav_unbind_refreshes\":" << result.uav_unbind_refreshes
            << ",\"legacy_cb_first_constant\":" << result.legacy_cb_first
            << ",\"legacy_cb_num_constants\":" << result.legacy_cb_count
            << ",\"unbound_cb_first_constant\":" << result.unbound_cb_first
            << ",\"unbound_cb_num_constants\":" << result.unbound_cb_count
            << ",\"geometry_index_change_requires_rebuild\":" << (result.geometry_index_change_rebuild ? "true" : "false")
            << ",\"tessellation_index_change_requires_rebuild\":" << (result.tessellation_index_change_rebuild ? "true" : "false")
            << ",\"deferred_null_guard_before_resource_access\":" << (result.deferred_null_guard ? "true" : "false")
            << ",\"graphics_uav_unbind_stage_refreshes\":" << result.graphics_unbind_refreshes << "}\n";
}
