#!/usr/bin/env python3
"""Compile regression fixtures around actual downstream DXMT code.

No Wine or game is launched. Resource/Metal/Win32 objects are substitutes;
BindingSet, scheduling, invalidation and cache methods come from the source.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from audit_dxmt_gameplay import FILES, THREAD_SHIM


def function(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
        end += 1
    return source[start:end].replace(" override {", " {")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reference", help="Git revision; default reads the working tree")
    parser.add_argument("--architecture", choices=["arm64", "x86_64"], default="arm64")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--sanitize", action="store_true", help="Enable AddressSanitizer and UBSan")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    with tempfile.TemporaryDirectory(prefix="dxmt-gameplay-validation-") as temporary:
        folder = Path(temporary)
        sources = {}
        for name in FILES + ["src/d3d11/d3d11_pipeline_cache.cpp"]:
            if args.reference:
                source = subprocess.run(["git", "show", f"{args.reference}:{name}"], cwd=args.source,
                                        check=True, capture_output=True, text=True).stdout
            else:
                source = (args.source / name).read_text()
            sources[name] = source
            file = folder / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(source)
        shim = folder / "shim"
        shim.mkdir()
        thread_shim = THREAD_SHIM.replace(
            "priority_requests = 0;", "priority_requests = 0, single_notifications = 0;").replace(
            "void notify_one() { value.notify_one(); }",
            "void notify_one() { ++single_notifications; value.notify_one(); }")
        thread_shim = thread_shim.replace("namespace dxmt {", """
inline std::atomic_bool pause_shutdown_wait = false, shutdown_wait_entered = false;
namespace dxmt {""").replace("value.wait(lock, predicate);", """
value.wait(lock, [&] {
  bool ready = predicate();
  // Simulate preemption after the predicate check, while still holding its lock.
  if (!ready && pause_shutdown_wait.exchange(false)) {
    shutdown_wait_entered.store(true);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  return ready;
});""")
        (shim / "thread.hpp").write_text(thread_shim)
        (shim / "util_win32_compat.h").write_text("#pragma once\n")

        context = sources["src/d3d11/d3d11_context_imm.cpp"]
        invalidation = function(context, "void\n  InvalidateDynamicResourceBindings(")
        # Verify the production Map routes all three renamed resource kinds here.
        mapping = function(context, "HRESULT\n  STDMETHODCALLTYPE\n  Map(")
        assert mapping.count("InvalidateDynamicResourceBindings(pResource, bind_flag);") == 1
        assert mapping.count("InvalidateDynamicResourceBindings(pResource, D3D11_BIND_SHADER_RESOURCE);") == 2
        assert ".set_dirty(" not in mapping
        (folder / "invalidation.inc").write_text(invalidation)

        header = sources["src/d3d11/d3d11_pipeline.hpp"]
        start = header.index("struct MTL_GRAPHICS_PIPELINE_DESC {")
        end = header.index("struct MTL_COMPUTE_PIPELINE_DESC", start)
        descriptor = header[start:end]
        start = header.index("namespace std {")
        end = header.index("} // namespace std", start) + len("} // namespace std")
        (folder / "pipeline_key.inc").write_text(descriptor + header[start:end])
        cache = sources["src/d3d11/d3d11_pipeline_cache.cpp"]
        methods = [function(cache, "void GetGraphicsPipeline("),
                   function(cache, "void GetGeometryPipeline("),
                   function(cache, "void GetTessellationPipeline(")]
        (folder / "pipeline_cache.inc").write_text("\n".join(methods))
        fixture = Path(__file__).parent / "fixtures/dxmt_gameplay.cpp"
        binary = folder / "probe"
        command = ["xcrun", "clang++", "-std=c++20", "-O2", "-arch", args.architecture,
                   "-I", str(shim), "-I", str(folder), "-I", str(folder / "src/dxmt"),
                   "-I", str(folder / "src/util"), str(fixture), "-o", str(binary)]
        if args.sanitize:
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode:
            raise RuntimeError(f"Regression fixture compilation failed:\n{built.stderr}")
        for _ in range(args.repeat):
            try:
                run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"Regression fixture timed out:\n{error.stderr}") from error
            if run.returncode:
                raise RuntimeError(f"Regression fixture failed ({run.returncode}):\n{run.stderr}")
            result = json.loads(run.stdout)
    result.update({"source_revision": args.reference or "working-tree",
                   "architecture": args.architecture, "repeat": args.repeat,
                   "sanitizers": ["address", "undefined"] if args.sanitize else [],
                   "scope": "extracted production code with substitute resource/Metal/Win32 objects",
                   "gameplay_cost_measured": False})
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
