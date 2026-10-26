# 项目文档导航

**无 Docker 执行与官方评分链已验收；当前先接入真实学生，再判断外部指导与能力内化。工程验收不等于 P0 完成。**

更新日期：2026-10-26（Asia/Shanghai）。本目录按用途分类；计划、事实、协议和历史记录分别维护。

## 从哪里开始

| 你现在想了解什么 | 唯一维护入口 |
|---|---|
| 我们要验证什么，什么结果能推翻 IDEA | [研究问题与假设](research/研究问题与假设.md) |
| 从现在到论文，整体怎样推进 | [研究路线图](plans/研究路线图.md) |
| 接下来具体做什么、哪些已经完成 | [近期实验 TODO](plans/近期实验TODO.md) |
| 查看计划文件之间的关系 | [规划输出索引](plans/规划输出索引.md) |
| 条件 B0–B4、数据隔离、指标和公平对照 | [评测与数据协议](experiments/评测与数据协议.md) |
| 服务器、环境和权限的当前事实 | [资源与环境](environment/资源与环境.md) |
| 不用 Docker 如何执行、如何重跑、有哪些边界 | [无 Docker 执行方案](environment/无Docker执行方案.md) |
| 最新隔离与真实环境验收结果 | [AppWorld 无 Docker 验收](environment/records/2026-10-26_AppWorld无Docker验收.md) |
| 早期官方原生 CPU 验收 | [AppWorld 原生验收](environment/records/2026-10-26_AppWorld原生验收.md) |
| Docker/Compose 的历史实查与管理员协助事项 | [AppWorld 部署预检](environment/records/2026-10-26_AppWorld部署预检.md) |
| 怎样登录、激活环境和指定 GPU | [服务器使用入门](environment/服务器使用入门.md) |
| 代码目前实现到哪里 | [项目架构](engineering/项目架构.md) |
| 论文与参考代码的分析 | [文献库](literature/README.md)、[专题分析](analysis/README.md) |

## 目录职责

```text
docs/
├── README.md                 文档总入口和维护规则
├── plans/                    可勾选的路线图、近期实验清单和输出索引
├── research/                 问题定义、暂定机制和主张边界
├── experiments/              稳定评测协议、实验记录模板
├── environment/              当前资源事实、使用说明、历史验收与原始快照
├── engineering/              代码架构、Git 操作约定
├── literature/               按问题聚类的论文阅读卡片
├── analysis/                 近邻工作比较与两个参考仓库的代码分析
└── archive/                  已被新计划替代的历史材料，不作为当前任务来源
```

## 当前状态摘要

| 已有证据 | 尚无证据 |
|---|---|
| toy runner、公开/私有日志、SFT JSONL 导出器 | 真实学生模型的一条 AppWorld episode |
| 22 节点独立 Conda 环境，9 项基础 Python/CUDA 检查通过 | 真实学生/教师访问方式和训练预算 |
| Bubblewrap + 管道 RPC；29 项隔离、10 项流程和 31 项项目测试通过 | 全量任务、其他 API/依赖 profile、形式化安全证明 |
| 单个官方 train 任务：正确解 5/5；空操作/重置/奖励伪造不完成；源数据不变 | 全部数据库与内存状态重置等价 |
| 真实 AppWorld adapter 与 core runner 固定 oracle 两步桥接 | 真实模型 policy、可审计学生 launcher |
| 固定的接口边界和配置草案 | 真实学生/教师、checkpoint 重载、B0/B1 或 SFT 收益 |

**只使用 22 节点；9901 暂停使用；sdk 不可用。**GPU 空闲状态必须在每次运行前重新查看，验收记录不代表当前可占卡。

Docker/Harbor 保留为后备方案，不再作为当前路线的前置条件。激活 Python 环境不会自动隔离后续所有命令；生成代码必须经无 Docker adapter 执行。

## 怎样维护

- `plans/研究路线图.md` 只维护阶段及进入/退出条件；细粒度执行只勾选 `plans/近期实验TODO.md`。
- 只有产物和证据存在时才标 `[x]`；部分完成的父任务保持 `[ ]`，勾选其已完成子任务。
- 环境与架构文档写事实，不混入另一份任务清单；实验定义统一查协议，不在每个计划里复制。
- 完成真实实验后，填写 [实验记录模板](experiments/实验记录模板.csv)，原始输出放独立 run 目录；不把计划表当结果表。
- 日期快照只记录当时事实；历史实验计划放 `archive/`。活跃计划不再同时维护固定名与日期名两份相同内容。
- 本轮合并了旧研究规划、服务器执行草案、pilot 协议及 `refine-logs/` 的重复计划；旧资源盘点中的过期“下一周任务”不再沿用。修改历史由 Git 保留。
