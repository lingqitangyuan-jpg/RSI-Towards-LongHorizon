# Benchmark 与环境 Pilot 协议

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: plan
- Origin Date: 2026-10-22
- Verification Status: UNVERIFIED
- Version Label: benchmark_pilot_v0.1

## 目的与阶段边界

本协议用于完成 P0：选择并验证一个真实、有状态的任务环境，至少跑通一条 `task reset → student action → environment observation → independent verifier` 链路，并留下可追溯材料。它不测试 SFT 是否有效，也不把 toy demo 计为 benchmark pilot。

## 候选比较（初筛，不是最终选择）

| 候选 | 与问题的匹配 | 当前可复用条件 | 主要未知/风险 | 当前处理 |
|---|---|---|---|---|
| AppWorld + Harness-Zero/Harbor 专用 adapter | 高：跨应用、有状态、多步任务，可能支持依赖结构分析 | 本地参考 checkout 有专用 Agent/Environment、build/validate、转换和 SFT 脚本入口 | 需官方数据与 Docker 镜像；Windows 系统盘空间紧；需核实 scenario 分组、可见信息、依赖深度和本机可运行性 | 第一候选，满足存储和容器条件后做最小 pilot |
| SpreadsheetBench 参考任务 | 中/待核验：适合验证工具环境和轨迹训练链，长程依赖是否足够需逐任务评估 | HZ 本地任务和 Harness 接口可供轻量适配检查 | 真实步骤依赖可能偏短；不能以任务数或长输出冒充长程 | 仅作较低成本工程备选/接口 smoke，不自动作为主 benchmark |
| ALFWorld/TextWorld 类轻量环境 | 潜在中高：可控地构造子目标和状态依赖 | 可独立 adapter，不依赖 HZ 的完整运行栈 | 当前尚未安装/适配；需重做官方基线、任务分组与比较，环境复杂度待核实 | AppWorld 安装受阻时再做小范围可行性比较 |
| USPTO | 低：本地 HZ 任务主要是单步逆合成 | 有现成任务与 verifier | 任务目标不匹配长程能力主张 | 不作主 benchmark |

## Pilot 进入条件

1. 可运行的 Linux/WSL 环境具备项目所需 Python 与容器后端，环境版本和来源可以固定。
2. 有合法可用的任务数据，官方安装/评分协议和数据许可已检查；训练、开发和最终测试的可用范围分清。
3. 目标任务可以 reset；验证分数不依赖学生容器可读取的答案；运行日志能把基础设施错误和能力失败区分开。
4. 有模型/teacher 访问途径，或先记录无模型环境校验的明确范围；API 成本和并发约束有上限。
5. 确认可用磁盘空间足以存放镜像、环境状态和日志。当前 C 盘约 10.6 GB；D/E 盘分别约有 238.2/158.5 GB 可用，但 Docker/WSL 是否能使用这些卷尚未核实。

## 单任务执行与审计步骤

对一个训练任务依次执行并保存：

1. 固定 benchmark/代码/容器/任务版本、task ID、split、seed、模型/提示 revision、运行配置和预算。
2. 启动干净环境并 reset；记录返回状态、初始公开观察和 reset 是否可重复。
3. 学生只接收公开 instruction/observation，提出动作；原始候选、通过 schema 校验后的动作及审查结果分字段记录。
4. 环境执行接受动作并返回公开 observation；核对动作与环境事件/轨迹中的工具调用一一对应。
5. episode 结束后通过独立 verifier 评分；保存原始评分和 verifier 版本，不将私有结果回传到学生历史或 SFT 样本。
6. 用同一 task/配置重跑一次 reset 或最小 deterministic fixture，检查来源追溯和稳定性；随机模型的输出一致性不作为 deterministic 要求。
7. 人工抽查完整轨迹、私有审计和导出样本。确认没有测试任务提示、ground truth、私有评分理由或未经同意的外部文件进入训练数据。

## 失败分类

每个预定任务保留分母，并将结果标为：`success`、`model_failure`、`action_schema_failure`、`environment_failure`、`timeout`、`verifier_failure` 或 `unknown`。infra 失败不得静默丢弃或只对两组的成功交集重算；重试策略在 pilot 前写定并保留初次与重试记录。

## 长程任务标注方案

- 主分档依据任务必须满足的前置条件/子目标依赖深度、跨步骤信息保持跨度；环境动作数、模型回合数、token 数另记，不互相替代。
- 可将官方 task metadata、可信参考计划和环境状态转移组合用于初始标注；如使用人工标注，保存标注规则、来源和抽查结果。
- 对依赖图记录节点、必要边、是否可交换、失败后可恢复性和能否从公开 observation 重新推断。没有证据时标 `unknown`，不以模型调用数推算依赖深度。
- 数据划分先按 scenario/task family/template 分组，再在组级切分 train/dev/test；检查相似 instruction、实体组合、状态初始值和参考计划，不能只随机拆 task ID。
- 至少分别报告：同族新实例；未见依赖组合或更深依赖档。只有 train 未覆盖相应深度时，才称长度外推。
- 冻结最终 test 清单、哈希和访问规则。B0/B1 的诊断只使用 train/dev；不能因多轮调 K 而反复观察 test。

## 记录与继续门槛

每次 pilot 对应一行[实验记录模板](实验记录模板.csv)，并保留运行配置、原始轨迹、公开轨迹、私有审计、verifier 结果和人工审计状态。至少报告任务成功率、分档成功率、有效/失败/重试任务数、平均步骤与 wall time、模型/API token 成本、容器/GPU资源和具体错误类型。

**P0 继续条件：** 一个真实任务 reset 至 verifier 的完整链路可追溯；动作、观察与评分对应；公开/私有字段隔离通过人工审计；基础设施、模型、动作 schema、超时和 verifier 失败能够区分。  
**进入 P1/B0-B1：** P0 通过后，固定一个学生与 `h0`，做小样本裸执行/固定 K 审查对照，能从逐步轨迹验证指导动作的依据与可执行性。  
**进入 P2：** 只有 B1 在开发任务上呈现可复核指导信号且 SFT 数据审计完成后，才进行训练和 reviewer-free 内化评测。

## 当前执行状态

状态：`BLOCKED_ON_ENVIRONMENT_SETUP`。Windows 主机可见 RTX 3060 Laptop 6 GB；WSL2 内 GPU 可见，但发行版为 Ubuntu 20.04/Python 3.8.10，未检测到 `uv` 或 Docker。C 盘剩余约 10.6 GB，D/E 盘约有 238.2/158.5 GB 可用，但容器/WSL 数据目录尚未确认。Harness-Zero 要求 Python 3.12–3.13，AppWorld 指南依赖 Docker 镜像。尚未下载数据、构建镜像或运行 benchmark。先确认 D/E 存储配置或远程 Linux，并决定 WSL升级/隔离 Python 或远程执行，再运行 pilot。
