#!/usr/bin/env python3
"""Single-GPU, offline text-only Qwen worker. JSON lines on stdin/stdout."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import tomllib


class ContextBudgetExceeded(ValueError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    rpc = sys.stdout

    def emit(value):
        rpc.write(json.dumps(value, ensure_ascii=True) + "\n")
        rpc.flush()

    with contextlib.redirect_stdout(sys.stderr):
        import torch
        import transformers
        from transformers import AutoTokenizer, Qwen3_5ForCausalLM
        if transformers.__version__ != config["transformers_version"]:
            raise RuntimeError("Transformers version differs from the frozen profile")
        if not os.environ.get("CUDA_VISIBLE_DEVICES", "").startswith("GPU-") or torch.cuda.device_count() != 1:
            raise RuntimeError("One explicit physical GPU UUID must be selected")
        root = Path(config["model_path"])
        manifest = json.loads((root / "rsi-model-manifest.json").read_text(encoding="utf-8"))
        if manifest["revision"] != config["revision"] or manifest["model_id"] != config["model_id"]:
            raise RuntimeError("Local model identity does not match config")
        torch.manual_seed(config["seed"])
        tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True, trust_remote_code=False)
        model, loading = Qwen3_5ForCausalLM.from_pretrained(root, local_files_only=True,
            trust_remote_code=False, use_safetensors=True, dtype=torch.bfloat16,
            attn_implementation=config["attention"], output_loading_info=True)
        if loading.get("missing_keys") or loading.get("mismatched_keys") or loading.get("error_msgs"):
            raise RuntimeError("Pretrained text parameters did not load completely")
        unexpected = loading.get("unexpected_keys", [])
        if any(not x.startswith(("model.visual.", "visual.", "mtp.", "model.mtp.")) for x in unexpected):
            raise RuntimeError("Unexpected non-vision/non-MTP checkpoint keys")
        model = model.to("cuda:0").eval()
        emit({"kind": "ready", "model_id": config["model_id"], "revision": config["revision"],
              "transformers": transformers.__version__, "torch": torch.__version__,
              "model_class": type(model).__name__, "gpu_uuid": os.environ["CUDA_VISIBLE_DEVICES"],
              "chat_template_sha256": hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
              "parameter_count": sum(p.numel() for p in model.parameters()),
              "missing_text_parameters": 0, "unused_vision_or_mtp_tensors": len(unexpected),
              "initial_gpu_allocated_bytes": torch.cuda.memory_allocated()})
        for line in sys.stdin:
            request = json.loads(line)
            try:
                generation = dict(request["generation"])
                thinking = generation.pop("enable_thinking")
                prompt = tokenizer.apply_chat_template(request["messages"], tokenize=False,
                            add_generation_prompt=True, enable_thinking=thinking)
                inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to("cuda:0")
                input_tokens = int(inputs.input_ids.shape[-1])
                if input_tokens + generation["max_new_tokens"] > config["max_context_tokens"]:
                    raise ContextBudgetExceeded("No implicit history truncation is permitted")
                if not generation["do_sample"]:
                    for key in ["temperature", "top_p", "top_k"]:
                        generation.pop(key, None)
                torch.cuda.reset_peak_memory_stats()
                started = time.monotonic()
                with torch.inference_mode():
                    output = model.generate(**inputs, **generation, use_cache=True,
                                            pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id)
                tokens = output[0, input_tokens:]
                text = tokenizer.decode(tokens, skip_special_tokens=False)
                for terminal in [tokenizer.eos_token, tokenizer.pad_token]:
                    if terminal:
                        text = text.removesuffix(terminal)
                emit({"kind": "generation", "id": request["id"], "text": text,
                      "input_tokens": input_tokens, "output_tokens": len(tokens),
                      "finish_reason": "length" if len(tokens) >= generation["max_new_tokens"] else "eos",
                      "elapsed_seconds": time.monotonic() - started,
                      "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated()})
            except Exception as exc:
                import traceback
                traceback.print_exc(file=sys.stderr)
                emit({"kind": "error", "id": request["id"], "error_type": type(exc).__name__})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
