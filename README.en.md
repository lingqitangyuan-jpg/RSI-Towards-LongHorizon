# RSI for Long-Horizon Reasoning

[简体中文 README](README.zh.md)

This project asks whether long-horizon behaviors induced by an external harness or reviewer can be internalized into a student model through trajectory training, and retained after the reviewer is removed at deployment. The project is in the P0/P1 preparation stage. The only executable end-to-end example is a deterministic toy demo; it is not long-horizon benchmark evidence and does not use a real model or training backend.

## Quick start

Requires Python 3.11 or newer. The MVP has no third-party Python dependencies.

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

See [the resource and environment inventory](docs/资源与环境盘点.md), the [benchmark pilot protocol](docs/benchmark_pilot_protocol.md), the current [research question](docs/research_question.md), and the broader [research plan](docs/研究工作规划.md).

See [the Git workflow](docs/git_workflow.md) for the repository, branch, and commit conventions.
