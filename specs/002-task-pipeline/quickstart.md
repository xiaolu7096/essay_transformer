# 快速验收：任务流水线

**日期**：2026-09-27 | **契约**：[api-tasks.md](./contracts/api-tasks.md)、
[api-credentials.md](./contracts/api-credentials.md) | **数据模型**：[data-model.md](./data-model.md)

本手册的主验收路径全部在**假模式**下完成：零第三方密钥、零外部
网络。真实模式（花自己的 API 额度）只在最后作为可选项。

## 1. 前置准备（仓库根 `e:\vibe_stuffs\essay_transformer\mvp_test`）

1. 沿用 Spec 001 的 `.venv`（已含 web 依赖）；本特性新增依赖安装：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r web\requirements.txt
   ```

2. `web/.env` 在 Spec 001 基础上补齐。先生成 Fernet 主密钥：

   ```powershell
   .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   再写入配置（真实模式 MASTER_KEY 必填；假模式可留空，启动会用
   临时密钥并告警）：

   ```env
   WEB_JWT_SECRET=<沿用 Spec001 的随机串>
   WEB_MASTER_KEY=<上一步生成的 Fernet 密钥>
   WEB_FAKE_MODE=1
   WEB_MAX_CONCURRENT_TASKS=2
   WEB_UPLOAD_MAX_MB=50
   ```

3. 前端依赖已在 Spec 001 安装；如重装过：`cd webui; npm install`。

## 2. 自动化测试（离线，必过）

```powershell
.\.venv\Scripts\python.exe -m pytest web\tests -v
```

预期：全部通过（Spec 001 的 24 项 + 本特性新增：凭据加密与越权、
链接/上传提交、fake 六阶段状态流转、每用户单活动（含并发双提交
恰一个 409）、三类错误映射、排队/轮询/章节间取消、取消-完成竞争
唯一终态、重启中断恢复、假模式零外部网络哨兵）。

## 3. 启动双服务

终端 A（仓库根）：

```powershell
$env:WEB_FAKE_MODE="1"
.\.venv\Scripts\python.exe -m uvicorn web.app.main:app --reload --port 8000
```

终端 B：

```powershell
cd webui
npm run dev
```

浏览器开 http://localhost:5173，注册/登录（Spec 001 流程）。

## 4. 手工验收场景（假模式）

| # | 操作 | 预期 |
|---|---|---|
| 1 | 进入"密钥设置"页 | 两个服务均显示未配置；无任何报错 |
| 2 | 不配置密钥，直接"提交任务"粘贴 `https://arxiv.org/abs/1706.03762` 提交 | 假模式下**允许**提交，立即进入任务列表，状态 `queued` 很快变 `running`（页面每 3 秒自动刷新） |
| 3 | 观察列表→详情 | 最终 `succeeded`，结果摘要显示样例论文标题、7 章、字符数 >0；详情标"演示任务"。**注意：假模式全程亚秒级，手工通常只能看到终态，"六阶段依次推进、进度 0→1"以 §2 自动化（T018⑬ 轮询查库断言每次 stage 落库）为权威证据；想肉眼看阶段推进可走 §6 真实模式** |
| 4 | 检查 `runs/<你的user_id>/<task_id>/` | 存在 source.pdf、parsed/content_list.json、图片与产物目录；无法通过任何 URL 直接访问这些文件路径 |
| 5 | 用第二个浏览器注册用户 B，提交任务 | A、B 可各自同时拥有活动任务（全局上限 2 内）；B 的列表里看不到 A 的任务 |
| 6 | B 直接请求 A 的任务（浏览器控制台 `(await fetch('/api/tasks/<A的id>', {headers:{Authorization:'Bearer '+B的token}})).status`，或简单地在详情页改地址栏 id） | 404 `任务不存在` |
| 7 | 同一用户在上一个任务结束后再提交；另尝试双标签页几乎同时各提交一次 | 串行正常；并发双提交恰好一个 201、另一个提示"已有进行中的任务" |
| 8 | 提交新任务后立刻点"取消"（假模式任务通常亚秒级完成，浏览器很难抢在完成前点到；可多点几次碰运气） | 按钮受理提示出现；若抢到活动态，任务变 `cancelled`、不出现成功摘要；终态后再点取消 → 409"任务已结束"。**注意：运行中取消（排队/轮询间隙/章节间）与取消-完成竞争的权威验证是 §2 自动化（T037 用可控事件确定性触发），手工只验按钮受理与终态展示；要稳定看到"运行中取消"请走 §6 真实模式长任务** |
| 9 | 提交非法内容：链接框填 `https://example.com/abc`；上传一个扩展名为 .txt 的文件；上传 >50MB 文件（可临时设 `WEB_UPLOAD_MAX_MB=1` 用普通 PDF 验） | 分别得到：422 链接不受支持；422 仅支持 PDF；413 超上限；均不产生任务记录 |
| 10 | 回到密钥设置，任意填写并保存 MinerU 密钥（如 `test-key-1234567890ab`） | 立即显示掩码 `••••…6789`；刷新页面只见掩码；覆盖保存后掩码变化；删除后恢复"未配置"。全程 DevTools 网络面板看不到明文回显 |
| 11 | 重启终端 A 的后端（Ctrl+C 再启动） | 服务正常启动；**中断恢复的权威验证是 §2 自动化（T030 直接构造 queued/running 残留行后触发 recover_interrupted）——假模式任务亚秒级完成，手工几乎无法在重启瞬间留下 running 任务**。手工可选：用 SQLite 工具临时把某历史任务状态改回 running 再重启，启动后该任务应变 `failed`/系统问题/"服务曾重启…可重新提交"，不再卡住 |
| 12 | 全流程期间在终端 A 观察日志 | 不出现任何密钥明文；假模式无任何对外网络请求日志 |

## 5. 错误三分类的针对性验证（自动化为主，手工可选）

- **输入问题**：非法 arXiv 链接/损坏 PDF（422，任务不创建）；纯
  扫描件由 core 检测时任务 failed→input/scan_without_text。
- **服务问题**：真实模式下故意填错误密钥提交 → failed，
  category=service、code=key_rejected，文案引导检查密钥（不会误报
  为"链接错误"）；外部超时/5xx → service_unavailable。
- **系统问题**：制造未知内部异常（测试中以替身触发）→
  internal_error，前端只显示统一维护者文案，无堆栈/路径泄露。

## 6. 真实模式抽验（可选，消耗自有 API 额度）

1. 终端 A 重启为 `$env:WEB_FAKE_MODE="0"`（.env 中配齐
   WEB_MASTER_KEY 与两个真实密钥——密钥在网页设置里录入即可）；
2. 提交一篇真实 arXiv 小论文；
3. 预期：parse 阶段可看到 MinerU 轮询期间的等待；此时点取消，
   任务在下一次轮询间隙停止（取消延迟不超过一个轮询周期 + 当前
   请求），终态 cancelled；
4. 再提交一篇不取消直到 succeeded，核对结果摘要与
   `runs/<user>/<task>/` 产物。

## 7. core 变更回归（原则一特许项）

```powershell
.\.venv\Scripts\python.exe -m pytest web\tests -v -k cancel
.\.venv\Scripts\python.exe -c "import inspect; from core.pipeline import transform_paper; from core.mineru_client import MinerUClient; print('cancel_event' in inspect.signature(transform_paper).parameters); print('cancel_event' in inspect.signature(MinerUClient.__init__).parameters)"
```

- 输出两个 `True`；
- 不传 `cancel_event` 时 core 既有行为逐字节不变（既有测试与探针
  全绿即为证据）；
- `core/errors.py`、`core/prompts/` 与其余 core 文件无改动。

## 8. 完成判定

- 第 2 节全绿；
- 第 4 节 12 个场景与第 5 节三分类逐条通过；
- 第 7 节 core 精确 diff 核对无误；
- 即可在 tasks.md 勾选最后任务并运行 `/speckit-converge`。
