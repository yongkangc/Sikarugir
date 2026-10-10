#!/usr/bin/env python3
"""Audit and regression-test DXMT state-setting hot paths from actual source.

No game or Wine is started. API/resource/encoder objects are substitutes.
The fixture compiles production BindingSet, setters, hazard resolution and upload
decisions. Results count queued work and state transitions, not game FPS.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from validate_dxmt_gameplay import function

FILES = ["src/dxmt/dxmt_binding_set.hpp", "src/util/util_bit.hpp",
         "src/util/util_likely.hpp", "src/util/util_math.hpp",
         "src/d3d11/d3d11_context_impl.cpp", "src/d3d11/d3d11_context_def.cpp"]
BASELINE = "332cee27aae3496d2d93e8f062145eada13a776b"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--reference", help="Git revision; default reads working files")
    parser.add_argument("--expect", choices=["baseline", "fixed"], default="fixed")
    parser.add_argument("--architecture", choices=["arm64", "x86_64"], default="arm64")
    parser.add_argument("--sanitize", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dxmt-hotpath-audit-") as temporary:
        root = Path(temporary)
        sources = {}
        for name in FILES:
            if args.reference:
                data = subprocess.check_output(["git", "show", f"{args.reference}:{name}"], cwd=args.source)
            else:
                data = (args.source / name).read_bytes()
            sources[name] = data
            file = root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        context = sources["src/d3d11/d3d11_context_impl.cpp"].decode()
        methods = [
            ("vertex.inc", "void\n  SetVertexBuffers("),
            ("layout.inc", "void\n  STDMETHODCALLTYPE\n  IASetInputLayout("),
            ("constant.inc", "template <PipelineStage Stage>\n  void\n  SetConstantBuffer("),
            ("get_constant.inc", "template <PipelineStage Stage>\n  void\n  GetConstantBuffer("),
            ("hazard.inc", "template <PipelineStage Stage, typename TViewOrBuffer>\n  bool\n  ResolveSRVHazard("),
            ("upload.inc", "template <PipelineStage stage, PipelineKind kind>\n  void\n  UploadShaderStageResourceBinding("),
        ]
        for file, marker in methods:
            (root / file).write_text(function(context, marker))
        predraw = function(context, "template <bool IndexedDraw>\n  DrawCallStatus\n  PreDraw(")
        tail = predraw[predraw.index("    if (cmdbuf_state == CommandBufferState::TessellationRenderPipelineReady)"):]
        (root / "graphics_upload.inc").write_text("DrawCallStatus UploadGraphicsResources() {\n"
                                                  "    DrawCallStatus status = 0;\n" + tail)
        index_methods = function(context, "void\n  InvalidateRenderPipeline(")
        if "GetDrawIndexBufferFormat()" in context:
            index_methods += function(context, "template <bool IndexedDraw>\n  SM50_INDEX_BUFFER_FORMAT\n  GetDrawIndexBufferFormat(")
            index_methods += function(context, "template <bool IndexedDraw>\n  void\n  UpdateRenderPipelineIndexFormat(")
            assert "Desc.IndexBufferFormat = GetDrawIndexBufferFormat<IndexedDraw>();" in context
        # Compile the actual early-return paths. A miss marks where real PSO setup follows.
        for name, cutoff in [("Geometry", "    if (!SwitchToRenderEncoder())"),
                             ("Tessellation", "    auto HS =")]:
            marker = f"template <bool IndexedDraw>\n  DrawCallStatus\n  Finalize{name}RenderPipeline()"
            start = context.index(marker)
            end = context.index(cutoff, start)
            prefix = context[start:end]
            if args.expect == "fixed":
                assert prefix.index("UpdateRenderPipelineIndexFormat<IndexedDraw>();") < prefix.index("if (cmdbuf_state")
            index_methods += prefix + "    return DrawCallStatus::Invalid;\n  }\n"
        (root / "index_pipeline.inc").write_text(index_methods)
        deferred = sources["src/d3d11/d3d11_context_def.cpp"].decode()
        start = deferred.index("HRESULT\n  STDMETHODCALLTYPE\n  Map(")
        end = deferred.index("    if (auto dynamic = GetDynamicBuffer", start)
        (root / "deferred_guard.inc").write_text(deferred[start:end].replace(" override {", " {") +
                                                 "    return S_OK;\n  }\n")
        fixture = Path(__file__).parent / "fixtures/dxmt_hotpath.cpp"
        binary = root / "probe"
        command = ["xcrun", "clang++", "-std=c++20", "-O2", "-arch", args.architecture,
                   "-DEXPECT_FIXED=" + str(int(args.expect == "fixed")),
                   "-I", str(root), "-I", str(root / "src/dxmt"),
                   "-I", str(root / "src/util"), str(fixture), "-o", str(binary)]
        if args.sanitize:
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode:
            raise RuntimeError(f"Hot-path fixture compilation failed:\n{built.stderr}")
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        if run.returncode:
            raise RuntimeError(f"Hot-path fixture failed ({run.returncode}):\n{run.stderr}")
        result = json.loads(run.stdout)
    result.update({"source_revision": args.reference or "working-tree",
                   "source_files_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()},
                   "architecture": args.architecture,
                   "expectation": args.expect,
                   "sanitizers": ["address", "undefined"] if args.sanitize else [],
                   "scope": "production methods/templates; substitute API/resource/encoder objects",
                   "gameplay_cost_measured": False})
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
