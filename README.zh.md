# RSI 长程推理项目

[English README](README.en.md) · [默认中文入口](README.md)

本项目研究：外部 Harness（执行框架）/审查器诱导的长程行为，能否通过轨迹训练内化到学生模型权重，并在部署时移除审查器后保留。项目仍处于 P0/P1 准备阶段；Agent 闭环仍是确定性 toy demo，尚未接入真实学生模型或训练后端。另有一个真实 AppWorld 官方训练任务已通过原生 CPU 工程验收。

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

完整文档入口见 [docs/README.md](docs/README.md)。当前执行清单见 [近期实验 TODO](docs/plans/近期实验TODO.md)；研究问题见 [研究问题与假设](docs/research/研究问题与假设.md)；评测定义见 [评测与数据协议](docs/experiments/评测与数据协议.md)。服务器已验收事实见 [资源与环境](docs/environment/资源与环境.md)。

获准使用的 22 节点已安装独立 Python 3.12/AppWorld 0.1.3.post1 环境，80 个包依赖检查通过。官方 train 任务 `07b42fd_1` 的正确解通过 5/5 项评分；空操作在初始化及重置后都不完成任务；初始公开 profile 一致、原始数据哈希不变。本轮模型调用为 0、GPU 使用为 0。详见 [原生验收记录](docs/environment/records/2026-10-26_AppWorld原生验收.md) 和 [结果 JSON](docs/environment/server_snapshots/2026-10-26/appworld-native/acceptance.json)。

原生脚本只执行可信官方代码，尚未验收模型代码隔离，也不是学生 launcher。Docker 使用权限、Compose 和 Harbor/容器路线仍待配置；9901 继续停用。原 PyTorch 环境独立保留。官方解答源码、解包数据库和含解答的原始交互日志不提交 Git、不导出为训练轨迹。

Git 仓库、分支和提交约定见 [Git 工作流](docs/engineering/Git工作流.md)。
