# RSI 长程推理项目

[English README](README.en.md) · [默认中文入口](README.md)

本项目研究：外部 Harness/审查器诱导的长程行为，能否通过轨迹训练内化到学生模型权重，并在部署时移除审查器后保留。项目仍处于 P0/P1 准备阶段；当前可运行部分是确定性 toy demo，不是长程 benchmark 结果，也没有接入真实模型或训练后端。

## 快速开始

需要 Python 3.11 或更新版本；MVP 没有第三方 Python 依赖。

```powershell
python -m unittest discover -s tests -v
python scripts/run_demo.py demo --manifest configs/toy_benchmark.toml --output runs/demo-001
python scripts/run_demo.py export-sft --manifest configs/toy_benchmark.toml --task-id toy-train-001 --trajectory runs/demo-001/reviewed/toy-train-001/student_visible.jsonl --output runs/demo-001/toy-train-sft.jsonl
```

运行会分别保存公开轨迹和私有审计日志。SFT 导出器仅接收 train split 的 `student_visible.jsonl`，并为每步保留此前公开交互历史。运行目录和 SFT 文件均拒绝覆盖，便于保留来源。

## 目录

```text
configs/                 benchmark 与实验配置
docs/                    研究规划、设计决策、资源记录与实验协议
scripts/                 本地命令入口
src/rsi_horizon/         契约、runner、toy 环境、导出器与集成边界
tests/                   接口、隐私边界和端到端 smoke tests
runs/                    本地生成产物，不进入 Git
sources/                 参考仓库和论文的本地副本，不进入 Git
```

## 当前边界

项目已区分学生可见任务与控制器任务、公开交互日志与私有审计日志、动作预算与 verifier 结果。Harness-Zero 接口目前只检查本地参考代码入口，不会启动 Harbor/AppWorld。HyperAgents 风格的 archive 只记录候选资产 lineage，尚未实现自动候选生成、选择或 RSI 外循环。

算力环境初盘和待确认事项见[资源与环境盘点](docs/资源与环境盘点.md)；benchmark pilot 协议见[benchmark_pilot_protocol.md](docs/benchmark_pilot_protocol.md)；当前问题定义见[research_question.md](docs/research_question.md)。广义阶段路线见[研究工作规划](docs/研究工作规划.md)。

Git 仓库、分支和提交约定见 [Git workflow](docs/git_workflow.md)。

服务器准备和 A100 排队下的 P0/P1 执行方案见[服务器实验设计与配置](docs/服务器实验设计与配置.md)；服务器路径、调度器和模型字段均保留为待确认配置。
