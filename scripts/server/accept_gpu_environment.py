"""Bounded single-GPU smoke test; no models, downloads or training jobs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import site
import socket
import subprocess
import sys
import tempfile


def command(args: list[str], timeout: int = 45) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--gpu", required=True, help="Physical GPU UUID or nvidia-smi index")
    parser.add_argument("--report-name", default="gpu-smoke.json", help="JSON basename inside root/reports")
    args = parser.parse_args()
    if Path(args.report_name).name != args.report_name or not args.report_name.endswith(".json"):
        parser.error("--report-name must be a JSON basename, without directories")
    root = args.root.resolve()
    if Path("/home") in root.parents or root == Path("/home") or "sdk" in root.parts:
        raise SystemExit("Refusing /home or sdk")
    report_dir = root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"scope": "python_cuda_smoke_only", "status": "FAIL", "hostname": socket.gethostname(),
              "time_utc": datetime.now(timezone.utc).isoformat(), "root": str(root), "checks": {}}
    checks = report["checks"]
    report_path = report_dir / args.report_name
    try:
        expected = root / "envs" / "rsi-py312"
        assert Path(sys.prefix).resolve() == expected, f"Unexpected Python prefix: {sys.prefix}"
        assert (3, 12) <= sys.version_info[:2] < (3, 14), sys.version
        assert not site.ENABLE_USER_SITE, "Activate PYTHONNOUSERSITE=1"
        report.update(python=sys.version, executable=sys.executable, prefix=sys.prefix)
        checks["private_python"] = True
        caches = {}
        for variable in ("CONDA_ENVS_PATH", "CONDA_PKGS_DIRS", "PIP_CACHE_DIR", "XDG_CACHE_HOME",
                         "HF_HOME", "UV_CACHE_DIR", "TORCH_HOME", "TRITON_CACHE_DIR", "CUDA_CACHE_PATH", "TMPDIR"):
            path = Path(os.environ[variable]).resolve()
            assert path.is_relative_to(root), f"{variable} points outside project storage: {path}"
            path.mkdir(parents=True, exist_ok=True)
            caches[variable] = str(path)
        report["cache_paths"] = caches
        with tempfile.TemporaryDirectory(prefix="accept-", dir=os.environ["TMPDIR"]) as directory:
            probe = Path(directory) / "probe.txt"
            probe.write_text("private-storage-ok\n")
            assert probe.read_text() == "private-storage-ok\n"
        checks["storage_write_read"] = True
        row = command(["nvidia-smi", "--id=" + args.gpu,
                       "--query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu,driver_version",
                       "--format=csv,noheader,nounits"])
        fields = [item.strip() for item in row.split(",")]
        assert len(fields) == 7, row
        index, uuid, name, used, total, utilization, driver = fields
        processes = command(["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_memory",
                             "--format=csv,noheader,nounits"])
        assert uuid not in processes, f"Selected GPU already has a compute process: {uuid}"
        assert int(used) < 128 and int(utilization) <= 5, f"GPU no longer idle: {row}"
        report["physical_gpu"] = {"index": int(index), "uuid": uuid, "name": name,
                                  "memory_mib": int(total), "driver": driver,
                                  "memory_used_before_mib": int(used), "utilization_before": int(utilization)}
        os.environ["CUDA_VISIBLE_DEVICES"] = uuid
        os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        os.environ["OMP_NUM_THREADS"] = "2"
        import numpy as np
        import torch
        torch.set_num_threads(2)
        assert torch.cuda.is_available(), "torch.cuda.is_available() is False"
        assert torch.cuda.device_count() == 1, "Test must see exactly one GPU"
        checks["single_selected_cuda_device"] = True
        torch.manual_seed(17)
        torch.backends.cuda.matmul.allow_tf32 = False
        a, b = torch.randn(128, 128), torch.randn(128, 128)
        cpu = a @ b
        gpu = a.to("cuda:0") @ b.to("cuda:0")
        torch.cuda.synchronize()
        torch.testing.assert_close(gpu.cpu(), cpu, rtol=1e-4, atol=1e-4)
        checks["float32_matmul_cpu_gpu_agree"] = True
        assert torch.cuda.is_bf16_supported(), "BF16 not supported"
        bf16 = a.to(device="cuda:0", dtype=torch.bfloat16) @ b.to(device="cuda:0", dtype=torch.bfloat16)
        torch.cuda.synchronize()
        assert torch.isfinite(bf16).all().item(), "BF16 returned nonfinite values"
        torch.testing.assert_close(bf16.float().cpu(), cpu, rtol=0.03, atol=0.2)
        checks["bfloat16_matmul"] = True
        np.testing.assert_allclose(torch.tensor([1.0, 2.0]).numpy(), np.array([1.0, 2.0]))
        checks["numpy_interop"] = True
        # Exercise a tiny cuDNN convolution as well as the cuBLAS matmul.
        conv = torch.nn.Conv2d(2, 4, 3, bias=False).cuda()
        result = conv(torch.randn(1, 2, 16, 16, device="cuda:0"))
        torch.cuda.synchronize()
        assert torch.isfinite(result).all().item()
        checks["cudnn_convolution"] = True
        result.square().mean().backward()
        torch.cuda.synchronize()
        assert conv.weight.grad is not None and torch.isfinite(conv.weight.grad).all().item()
        checks["finite_backward_gradients"] = True
        report["runtime"] = {"torch": torch.__version__, "numpy": np.__version__,
                             "cuda": torch.version.cuda, "cudnn": torch.backends.cudnn.version(),
                             "logical_device": "cuda:0", "device_name": torch.cuda.get_device_name(0),
                             "peak_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2,
                             "fp32_max_abs_error": (gpu.cpu() - cpu).abs().max().item(),
                             "bf16_max_abs_error": (bf16.float().cpu() - cpu).abs().max().item()}
        # Metadata scans can be slow on the lab's shared disks; GPU work remains tiny.
        report["pip_check"] = command([sys.executable, "-m", "pip", "check"], timeout=180)
        checks["pip_dependencies"] = True
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Report: {report_path}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
