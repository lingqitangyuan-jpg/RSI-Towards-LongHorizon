# 无 Docker 执行方案

**当前用 Bubblewrap 隔离 Agent 生成的 Python，AppWorld 与官方评分器在外部可信进程管理；22 节点已通过单任务验收。**

更新日期：2026-10-26。结果见 [验收记录](records/2026-10-26_AppWorld无Docker验收.md)，后续任务见 [近期实验 TODO](../plans/近期实验TODO.md)。

## 1. 我们要解决什么问题

模型做 AppWorld 任务时，会生成 Python 代码调用日历、购物等模拟应用。这些代码可能出错，也可能尝试读取答案、修改评分或访问服务器文件。因此，需要给它一个受限制的运行位置，同时保留任务允许的 API。

AppWorld 已装在自己的 Python 环境里。Docker 原本负责执行隔离和服务组织，并不是“只有装 Docker 才能运行 AppWorld”。现在保留官方任务和评分方式，用另一种执行边界替换 Docker；管理员无需为当前路线配置 Docker 用户组、daemon 或 Compose。

## 2. 三部分各自负责什么

```text
可信控制器：准备任务、发送代码；未来在这里调用模型和 reviewer
    │ 标准输入/输出管道：传代码、公开 API 请求和结果
    ▼
Bubblewrap 执行端：运行生成的 Python，仅持有公开 API 代理
    │ 请求公开 API，不能直接读数据库
    ▼
可信 AppWorld world：执行公开 API，维护任务状态，不加载正确解
    │ 执行端退出、管道关闭后，导出当前任务状态
    ▼
独立 verifier 进程：调用官方 evaluate_task，对导出状态评分
```

- **执行端**是模型代码运行的地方。它看不到 home/.ssh、项目目录、AppWorld 数据库、正确解、评分器和 GPU。
- **AppWorld world**管理模拟应用。公开 file_system API 操作任务允许的模拟文件系统，不等于开放服务器文件系统。
- **verifier（评分器）**在执行端完全停止后评分。执行端打印“成功”或伪造 reward，不会改变官方判断。

“可信”指实现边界：控制器、API broker、官方 AppWorld 和评分器属于可信代码；不能把整个宿主 Python 都视为已隔离。

## 3. 为什么选 Bubblewrap

Bubblewrap 是 Linux 上构建受限运行环境的工具。22 节点已实测支持普通用户创建所需隔离空间，并已私有安装；没有启动 Docker 服务、监听网络端口或使用 sudo。

| 方案 | 当前状态 |
|---|---|
| Bubblewrap + 管道 RPC（远程过程调用） | 已实现、部署和有限验收，作为当前主路线 |
| Docker / Harbor | 参考其 client/world/verifier 边界；保留后备路线，不作为当前前置条件 |
| Apptainer / Podman / nsjail | 检查时未安装，尚未适配或验收 |
| Conda / venv | 选择 Python 和依赖，不能单独限制生成代码访问宿主文件 |
| PRoot | 本轮未采用或验收，不能用路径重映射直接代替当前边界 |

参考：[Bubblewrap 官方说明](https://github.com/containers/bubblewrap)。它提供构建沙箱的工具，策略由调用方制定；装好一个二进制本身不代表完成安全隔离。

## 4. 当前执行策略

| 边界 | 实现 |
|---|---|
| 文件可见性 | 只读最小 Python runtime 和少量公开时间依赖；不挂载 home、项目、任务数据、Docker socket |
| 进程和网络 | user/mount/PID/network/IPC/UTS namespaces；seccomp 拒绝 fork/clone、socket、再次创建 namespace、ptrace 等；解释器启动后拒绝 exec |
| 权限与环境 | 无有效 capabilities、no-new-privileges；清空宿主环境，不传模型 API key |
| API | 只放行官方公开 API 文档中的应用和端点；拒绝 admin、私有方法和伪造评分控制请求 |
| Python 兼容性 | 跨步变量、import apis、requester.request、任务公开时间冻结；类型注解映射为代理，不挂载原生 AppWorld 包 |
| 评分 | 停止执行端并确认退出 → 导出状态 → 独立进程评分 → 核对输入哈希不变 |

参数见 [appworld_rootless_p0.toml](../../configs/server/appworld_rootless_p0.toml)：

| 项目 | 当前值 | 含义 |
|---|---:|---|
| 地址空间 | 512 MiB | 执行端进程地址空间，不是整个项目物理内存配额 |
| CPU 时间 | 15 秒 | 持续存在的执行端进程累计 CPU 时间，不是每步刷新 |
| 单步 wall time | 15 秒 | 含等待时间；慢 API 也受 controller deadline 约束 |
| 代码 | 每步 64 KiB | 输入上限 |
| API | 每步 1000 次 | 请求上限 |
| 步数 | 64 | 当前环境上限 |
| 输出 | 约 12,000 字符 | 执行端输出截断预算 |

这些限制针对执行端，**不是 controller/world/verifier 的整体 cgroup 配额**。多任务和并行运行仍需另做资源预算。

## 5. 安装位置

以下都在 `/nfs-data/sdf/lyf/rsi-long-horizon` 内：

| 相对路径 | 用途 |
|---|---|
| tools/bubblewrap-0.6.1-1ubuntu0.3/usr/bin/bwrap | 私有普通用户工具，0755，无 setuid/setgid |
| envs/appworld-native-py312 | 复用 AppWorld 0.1.3.post1 独立 venv |
| envs/rsi-py312 | 保留 Conda/PyTorch 环境，同时提供只读 Python runtime |
| appworld-native | 公共资产及一个官方 train 任务 |
| code/RSI-Towards-LongHorizon | 服务器工作树 |
| runs/rootless/<run_id> | 每次验收的独立输出 |

Bubblewrap 固定为 Ubuntu jammy-security 软件包 0.6.1-1ubuntu0.3，不声称是最新 upstream 版本。安装先用 Ubuntu archive keyring 验证签名，再核对索引、软件包和二进制；可复现入口已重新核验现有安装。

- deb SHA256：`706763714e5d19fc7ab2572a6418506e3a4a5d5960e99309707b58117e6b40cb`。
- 二进制 SHA256：`bf6cf3d4456665f5d80c14eb79a76e0ce87b39a0883e614a161658b4ec33492c`。
- 证据：[installation.json](server_snapshots/2026-10-26/appworld-rootless/installation.json)、[reproducible-installation.json](server_snapshots/2026-10-26/appworld-rootless/reproducible-installation.json)。

## 6. 下次如何使用

Windows 终端登录：

```powershell
ssh -p 22 lyf@211.81.55.158
```

服务器端重跑**无模型工程验收**：

```bash
source /nfs-data/sdf/lyf/rsi-long-horizon/scripts/activate_appworld_rootless.sh
cd /nfs-data/sdf/lyf/rsi-long-horizon/code/RSI-Towards-LongHorizon
export RSI_ROOTLESS_RUN_ID=rootless-p0-$(date -u +%Y%m%d-%H%M%S)
bash scripts/server/run_appworld_rootless.sh
```

每次生成新 run ID，拒绝覆盖旧结果。长时间任务沿用 screen 和日志/launch manifest；本次已完成的验收无需为了继续研究而重复执行。

**激活脚本只选择 Python、缓存和 backend 标记，不会自动隔离此后所有命令。** 直接运行 python arbitrary.py 仍在宿主执行。必须通过 RootlessAppWorldEnvironment 或上述验收入口，生成代码才进入 Bubblewrap；未来模型 policy 也必须走这个 adapter。

在相同 Linux 节点恢复私有工具时，可使用已保留的离线材料：

```bash
python scripts/server/prepare_bubblewrap.py install \
  --stage /nfs-data/sdf/lyf/rsi-long-horizon/incoming/bwrap-jammy-security-20261026 \
  --report /nfs-data/sdf/lyf/rsi-long-horizon/reports/bwrap-verify-$(date -u +%Y%m%d-%H%M%S).json
```

联网准备命令是 `python scripts/server/prepare_bubblewrap.py prepare --stage <新的本地目录>`，服务器再验证签名。安全源索引随时间更新，脚本发现与固定版本不符会停止；不能跳过校验强行升级，应保留已验证离线材料。

## 7. 范围与下一步

已通过 29 项隔离、10 项流程检查，用一个官方 train 任务验证正确解、空操作、重置和奖励伪造；固定官方解也通过现有 core runner 完成两步执行与评分。**这是工程验收，不是学生模型成绩、B0/B1 或 RSI 收益。**

限制包括：

1. 仅测试一个任务和只读、无网络、有限依赖的 Python profile；新增包、可写挂载、网络或不同 API 家族后需重新验收。
2. 与宿主共享 Linux 内核，有限探针不构成形式化安全证明，也不等于虚拟机。内核、工具或可信 broker 的缺陷仍可能影响边界。
3. 评分器属于可信控制面，本轮没有给整个作业施加统一资源沙箱。
4. 真实模型 policy、学生 episode 和训练后端尚未完成。下一步先明确学生访问方式，补 launcher 与公开轨迹审计，再选 3–5 个不同 API 任务检查兼容性并诊断 B0。

全程只用 22 节点；不使用 9901、sdk 或 sudo。官方解只用于验收，不进入训练数据。
