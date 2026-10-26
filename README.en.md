# RSI for Long-Horizon Reasoning

[简体中文 README](README.zh.md)

This project asks whether long-horizon behaviors induced by an external harness or reviewer can be internalized into a student model through trajectory training, and retained after the reviewer is removed at deployment. The project is in the P0/P1 preparation stage. A rootless AppWorld adapter has passed single-task engineering acceptance and a fixed-oracle bridge through the core runner. A pinned Qwen3.5-9B policy and inference launcher are implemented; the separate inference environment and tokenizer have passed CPU checks. Full-weight GPU inference and a real student episode are pending. The training backend is not implemented.

## Quick start

Requires Python 3.11 or newer. The toy demo below has no third-party Python dependencies. The server AppWorld backend has separate Linux and package requirements.

```powershell
python -m unittest discover -s tests -v
python scripts/run_demo.py demo --manifest configs/toy_benchmark.toml --output runs/demo-001
python scripts/run_demo.py export-sft --manifest configs/toy_benchmark.toml --task-id toy-train-001 --trajectory runs/demo-001/reviewed/toy-train-001/student_visible.jsonl --output runs/demo-001/toy-train-sft.jsonl
```

Runs keep student-visible trajectories separate from private audit logs. The SFT exporter accepts only `student_visible.jsonl` from a training split and includes the preceding public interaction history for each step. Run directories and SFT outputs are immutable once written.

## Project map

```text
configs/                 benchmark and experiment configurations
docs/                    research plan, design decisions, resource notes, protocols
scripts/                 local command entry points
src/rsi_horizon/         contracts, runner, toy environment, exporter, adapters
tests/                   contract, privacy, and end-to-end smoke tests
runs/                    generated local artifacts; excluded from Git
sources/                 local reference repositories and papers; excluded from Git
```

## Current scope

The scaffold separates student-visible tasks from controller-side task records, public trajectories from private audits, and action budgets from verifier results. The Harness-Zero integration currently checks expected entry points in a local reference checkout; it does not launch Harbor or AppWorld. The HyperAgents-inspired archive records candidate lineage only; automatic candidate generation, selection, and an RSI outer loop are not implemented.

See [the documentation index](docs/README.md) for the current plan, research question, evaluation protocol, server facts, and engineering notes. The active execution list is [docs/plans/近期实验TODO.md](docs/plans/近期实验TODO.md).

On the authorized port-22 node, a separate Python 3.12/AppWorld 0.1.3.post1 environment passed dependency checks for 80 packages. For official train task `07b42fd_1`, the official solution passed all 5 verifier checks; no-op cases before and after reinitialization both failed the task. Initial public profiles matched and source data hashes were unchanged. This used no model calls or GPU. See the [native acceptance record](docs/environment/records/2026-10-26_AppWorld原生验收.md) and [result JSON](docs/environment/server_snapshots/2026-10-26/appworld-native/acceptance.json).

The native scripts execute trusted official code only. Generated Python now runs through a separate Bubblewrap backend: a read-only minimal runtime, namespaces, seccomp and pipe-based public API RPC. A trusted controller owns AppWorld state; a separate verifier grades an exported snapshot after the client exits. On port 22, 29 isolation checks, 10 workflow checks and 31 project tests passed. The official solution scored 5/5; no-op, reset no-op and reward-spoof cases all failed the task. A fixed official oracle also completed two steps through the existing core runner. This used zero model calls, GPUs, Docker or sudo.

See the [rootless design and operating guide](docs/environment/无Docker执行方案.md), [acceptance record](docs/environment/records/2026-10-26_AppWorld无Docker验收.md), and [machine-readable evidence](docs/environment/server_snapshots/2026-10-26/appworld-rootless/acceptance.json). Docker/Harbor remains an unvalidated fallback, not a prerequisite for this route. Activating the environment does not sandbox arbitrary commands; generated code must go through the adapter.

Acceptance covers one train fixture and the current read-only, network-disabled Python profile. It is a tested shared-kernel boundary, not a formal security proof, full-benchmark validation, real student result or RSI improvement. The next step is full-weight GPU/episode acceptance of the implemented model policy/launcher, followed by a small cross-API diagnostic set. See the [student choice and integration](docs/experiments/学生模型选择与接入.md) and [code guide with pseudocode, architecture and data-flow diagrams](docs/engineering/项目代码说明.md). Port 9901 remains out of use. The original PyTorch environment is separate. No official solution source, unpacked database or raw solution-containing interaction log is committed or exported for training.
