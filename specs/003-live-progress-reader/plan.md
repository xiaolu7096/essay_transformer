# 技术方案：实时进度与成品阅读

**分支**：`003-live-progress-reader` | **日期**：2026-09-28 | **规格**：[spec.md](./spec.md)

**输入**：来自 `/specs/003-live-progress-reader/spec.md` 的特性规格

## 摘要

在 Spec 002 的 FastAPI + Vue3 任务流水线之上，补齐"等待与阅读"
两块体验：① 以服务器→浏览器单向推送取代 3 秒轮询，任务状态
变化（排队/六阶段/终态）约 1 秒内自动到达，断线自动重连并以
快照收敛；② 成功任务新增网页阅读器（章节导航、数学公式、受控
图片、折叠区，正文做 XSS 消毒）与成品打包下载（zip：成品
Markdown + images/ 同结构，解压即图文完整）。推送在 Web 层
挂接 002 已有的进度回调与终态写入点，经进程内事件中枢跨线程
广播；阅读器不新增产物格式，直接解析 002 已落盘的
output.md，因此 002 历史任务可直接阅读。core 逐字节不动，
后端零新增第三方依赖（zip 用标准库 zipfile 流式打包），前端
新增 markdown-it-texmath 与 dompurify（markdown-it、katex
已在依赖中）。全程假模式离线可验收。前端交付标准为**功能框架
完整**（入口可达、信息正确、交互闭环），视觉精修归 Spec 004。

## 技术上下文

**语言/版本**：Python 3.11+（本机 3.13.7，沿用仓库 `.venv`）；前端
JavaScript ES2022 + Vue 3 SFC（不引入 TypeScript）；Node 18+。

**后端依赖**：**零新增第三方依赖**。
- 推送响应直接用 FastAPI 自带 Starlette 的
  `StreamingResponse`（`media_type="text/event-stream"`），
  不引 sse-starlette/websocket；
- 打包下载用标准库 `zipfile.ZipFile`（写内存
  `io.BytesIO`/临时管道均可，成品规模为单篇论文量级），不引
  第三方压缩库；
- 其余全部复用：fastapi/starlette、sqlalchemy 2.x、anyio、
  pydantic；core 白名单（根 requirements.txt 5 行）一行不改。

**前端新增依赖**（只进 `webui/package.json`）：
- `dompurify`：成品 HTML 消毒（去脚本/事件属性/非受控外链）；
- `markdown-it-texmath`：让既有 `markdown-it` 识别
  `$...$`/`$$...$$` 并用既有 `katex` 排版（版本在实施时取与
  markdown-it@14 兼容的稳定版）；
- `markdown-it@14`、`katex@0.16` 已在 package.json 且
  node_modules 就绪，无需新引。

**存储**：
- 不新增表、不新增列。任务最新状态仍只存在 SQLite `tasks` 行
  （002 快照模型）；推送事件是瞬时内存消息，不持久化、不回放；
- 成品仍只在 `runs/<user_id>/<task_id>/output.md` 与
  `images/`（002 落盘）；阅读器与下载只读取、不写入；
- 假物料在 `web/fixtures/fake_run/` 内扩充 1 张样例图
  （Web 层物料，非 core 资产）。

**测试**：后端 pytest 全程离线零网络（沿用 002 哨兵纪律）：
推送事件流用 TestClient 流式上下文 + 假模式真实任务驱动；
归属/越权、晚订阅终态、断线重连快照、章节解析、图片路径逃逸、
下载文件名校验均以断言固化。前端无自动化框架，阅读器观感
（公式/折叠/导航/占位）以 quickstart §4 手工验收为准。

**目标平台**：本地开发机（Windows/PowerShell，命令兼容
Linux/macOS），可信内网；evergreen Chrome/Edge（fetch 流式
读取在目标浏览器原生可用）。

**项目类型**：前后端分离 Web 应用（在 001/002 两工程上增量）。

**性能目标**：
- 一次状态变化到界面反映 ≤1 秒（SC-001）；
- 推送为低频次事件（单任务全程约十几次），单客户端队列上限
  256，慢消费者丢弃最旧的非终态消息（终态消息不丢）；
- 心跳注释 15 秒一帧，用于探活；
- 阅读器正文一次性返回（单篇论文量级，数万字/数十图），章节
  跳转在浏览器内本地完成。

**约束**：
- core 零改动（含不新增参数）；002 的 cancel_event 特许增量
  不扩大；
- 唯一实时通道为单向推送，不引入 WebSocket/消息队列；
- 所有新连接/端点复用 001 单一 JWT 身份令牌与 404 归属语义；
- 推送与资源 URL 中不得出现令牌（不用 query token，见 D2）；
- 假模式零外部调用：连图片与公式字体都不触发外站请求（KaTeX
  样式/字体本地打包；图片只走受控端点）。

**规模/范围**：后端新增 3 个模块（事件中枢、SSE 路由流、成品
读取服务）+ tasks 路由追加 3 个端点 + worker 4 个发布点；前端
新增 1 个视图、1 个推送封装、1 个 Markdown 渲染模块、2 个 API
封装，tasks store 以订阅替换轮询；fake fixture 增补 1 图。

## 宪法检查

*门控：Phase 0 研究前必须通过；Phase 1 设计后复核。*

| 宪法原则 | 本特性符合性 | 说明 |
|---|---|---|
| 一、core 冻结 | ✅ 通过 | 本特性对 `core/` **零改动、零新参数**。阅读器直接解析 002 落盘的 output.md（标题行 `# `、章节 `## `、文末 `<details>` 折叠区、`images/<name>` 相对链接，均已对真实产物取证），属 Web 层对既有产物格式的适配，不回改 core。若未来 core 标题格式变化，影响面仅 Web 层解析器一个模块（D5）。 |
| 二、继承架构边界 | ✅ 通过 | 推送中枢、流式响应、成品解析、消毒全部在 web/webui 层；core 既不感知订阅关系也不感知阅读器；Web 仍只调 core 公共入口（本特性连调用都不新增）。 |
| 三、多租户隔离 | ✅ 通过 | 推送订阅、正文、图片、下载四类访问全部走"认证 + get_owned_task 归属"，越权/不存在统一 404；图片端点只接受资源名（安全段+扩展名白名单+目录解析校验），不接受路径；不新增任何裸文件系统入口。 |
| 四、凭据与密钥安全 | ✅ 通过 | 推送载荷口径等同 002 TaskOut 快照，无路径/密文；令牌只走 Authorization 头（fetch 流式与图片 blob 均如此），不出现在 URL/日志；事件中枢不记录载荷正文。 |
| 五、长任务后台化 | ✅ 通过 | worker 与线程池模型不变；推送是在既有 DB 写入点之后的进程内广播（先提交后发布），不增加请求路径阻塞；流式端点为纯异步读队列，不占用 worker 线程；执行器替换为队列实现时，发布点仍在任务状态写入侧，订阅口径不变。 |
| 六、契约先行 | ✅ 通过 | 推送帧结构与三个新端点写入 contracts/（事件帧 JSON 字段、错误码、Content-Disposition 规则）；前端只用统一 axios/fetch 通道与既有令牌；阅读器是 requiresAuth + AppLayout 内的普通路由。 |
| 七、离线可验收 | ✅ 通过 | 假模式下推送六阶段、阅读器（公式/图片/折叠）、下载全部零外部调用可演（KaTeX 资源本地打包、图片走本任务受控端点）；002 历史成功任务直接可读；fixture 增补样例图仍在 web 层。 |

**门控结论**：无违例，无需复杂度豁免。实施中若发现必须改 core
（例如 output.md 无法解析出章节），立即停工回到本门控修订。

## 项目结构

### 本特性文档

```text
specs/003-live-progress-reader/
├── plan.md              # 本文件
├── research.md          # Phase 0：技术决策 D1–D11
├── data-model.md        # Phase 1：瞬时事件模型 + output.md 解析模型
├── quickstart.md        # Phase 1：本地运行与离线验收手册
└── contracts/
    ├── api-realtime.md  # 任务事件推送通道契约
    └── api-reader.md     # 阅读器正文/图片/下载契约
```

### 源码结构（仓库根，仅列新增/改动）

```text
web/
├── fixtures/fake_run/
│   └── parsed/
│       ├── content_list.json   # [改] 增补 1 个 figure 块（Web 物料）
│       └── images/
│           └── sample.png      # 新增：最小合法 PNG 样例图（随仓库提交）
└── app/
    ├── main.py                 # [改] lifespan 绑定事件中枢主循环
    ├── schemas.py              # [改] +ReaderOut/ReaderSection/事件帧模式
    ├── deps.py                 # [改] +get_owned_succeeded_task（成功态归属）
    ├── runner/
    │   ├── events.py           # 新增：ProgressHub（进程内发布/订阅，跨线程）
    │   └── pipeline_run.py     # [改] 4 个发布点（进度×1、终态×3），先提交后发布
    ├── services/
    │   └── artifacts.py        # 新增：output.md 读取/章节解析/图片安全定位/
    │                           #       文件名消毒/zip 流式打包
    └── routers/
        ├── tasks.py            # [改] +GET …/events（SSE）+reader+images+download(zip)
        └── （无新路由文件，全部归入 tasks 保持归属口径单一）

webui/src/
├── api/
│   ├── events.js               # 新增：fetch 流式 SSE 客户端（Bearer 头、自动重连）
│   ├── artifacts.js            # 新增：getReader/downloadArtifactZip/fetchImageBlob
│   └── tasks.js                # [改] 无（事件相关不放进 tasks.js）
├── lib/
│   └── markdown.js             # 新增：markdown-it+texmath 渲染 + DOMPurify 消毒 +
│                               #       图片占位规则（data-art），单例
├── stores/tasks.js             # [改] 以事件订阅替换 3s 轮询；失败降级低频快照
├── views/
│   ├── ReaderView.vue          # 新增：章节导航/公式/图片 blob 注入/折叠/深链
│   ├── TaskListView.vue        # [改] succeeded 行 +阅读/下载
│   └── TaskDetailView.vue      # [改] succeeded 态 +阅读/下载；接入实时状态
└── router/index.js             # [改] +/tasks/:id/reader（requiresAuth）
```

**结构决策**：

1. **不引入新路由文件**：events/reader/images/download 全部挂在
   tasks router，复用其任务归属查询与错误体口径，避免出现第二套
   "按 id 取任务"的鉴权路径（研究 D1/D5）。
2. **事件中枢独立于取消注册表**：cancel_registry 是
   task_id→Event 的执行期设施（随任务消亡）；ProgressHub 是
   task_id→订阅者集合的推送设施（随连接存亡），职责不同，不合
   并（D3）。
3. **成品读取放 services 层**（`services/artifacts.py`）：解析
   output.md、安全定位图片、文件名消毒、zip 打包是纯文件领域
   逻辑，路由只做鉴权与响应装配；未来 Spec 再开放其他产物时
   复用同一份路径安全口径（D5/D7/D8）。
4. **前端 Markdown 渲染集中到 `lib/markdown.js` 单例**：消毒
   策略只有一份，任何视图不得绕过它渲染成品 HTML（D6）。
5. **假物料就地增补而非另建阅读器夹具**：保证"提交→推送→阅读
   真实图片→下载解压图片齐全"是一条端到端链路；受影响的 002
   断言（块计数 13→14、字符数下界）在本特性任务内同步更新（D9）。
6. **前端只搭功能框架**：新页面沿用 AppLayout 与 Naive UI
   默认组件把功能闭环做对即可（状态实时变化、可阅读、可下载、
   可取消、归属提示正确）；不引入设计系统、不做像素级样式与
   动效/移动端适配——视觉精修是 Spec 004 的独立范围，本特性
   任务清单不排视觉打磨项，也不为视觉返工后端（spec FR-016）。

## 复杂度跟踪

无宪法违例，无需豁免。

---

## Phase 1 后宪法复核

- **原则一**：设计全程零 core 触点；章节解析依赖的 output.md
  格式（`# / ## / <details> / images/`）已对真实产物与 core 拼装
  代码双向取证；通过。
- **原则三/四**：四个新访问点的归属、安全段、扩展名白名单、
  404 语义、令牌不落 URL 均写入契约；通过。
- **原则五**：发布点全部位于既有 DB 提交之后，流式端点零阻塞；
  通过。
- **原则六**：事件帧与端点形态契约化；前端复用既有 axios 单例
  与 JWT；通过。
- **原则七**：KaTeX 本地字体 + 受控图片端点保证阅读器零外联；
  fake fixture 增补图后端到端离线可演；通过。

**复核结论：通过，无新增违例，无复杂度豁免。**
