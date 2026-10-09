# Phase 0 研究：实时进度与成品阅读

**日期**：2026-09-28 | **关联**：[plan.md](./plan.md)、[spec.md](./spec.md)

研究方法：先对 002 代码与真实 fake 产物取证（core 拼装格式、
worker 写入点、现有依赖、runs/ 下真实 output.md），再在每个
决策点给出选择、理由与被否方案。全部结论离线可验。

## 事实取证（设计前提）

1. **成品格式已固定且在磁盘上**：worker 在成功后把
   `result.full_markdown` 写入 `runs/<uid>/<tid>/output.md`
   （pipeline_run.py 第 186-188 行注释已写明"供 Spec 003 消费"）。
   真实样本结构：
   - 首行 `# {论文标题}`；
   - 固定 7 个二级标题（planner.SLUG_ORDER 七项，标题形如
     `## 0 · 30 秒摘要` … `## 6 · 总评`）；
   - 章后可能有 `---\n\n## 附录（折叠区）`，其下为
     `<details><summary>…</summary>…</details>` 块，折叠内容
     含**原始 HTML 表格**与图片；
   - 图片链接为相对路径 `images/<文件名>`；
   - 数据库只存 title/section_count/total_chars，**未存分章
     JSON**，因此章节必须从 output.md 解析（这同时保证 002
     历史任务可直接阅读）。
2. **进度写入点集中**：worker 的进度只有一个出口
   `persist_progress(task_id, stage, p, msg)`；终态三个出口
   `_finish_success/_finish_failure/_finish_cancelled`（均为
   条件 UPDATE 后 commit）。这 4 处是天然的发布点。
3. **worker 在 anyio 线程内执行**；SSE 客户端连接在 asyncio
   事件循环线程——跨线程投递必须显式桥接。
4. **前端现状**：`markdown-it@14.1`、`katex@0.16.11` 已在
   package.json 且 node_modules 中；缺数学插件与消毒库；
   002 的刷新是 tasks store 内 3 秒 `setInterval` 轮询。
5. **假物料无图片**：content_list.json 13 块（含 1 个 equation
   文本块 `$$y = W x + b$$`、1 个 table、1 个 ref_text），
   parsed/images 不存在。阅读器要端到端验真图片必须补物料。
6. **鉴权现状**：JWT 仅经 `Authorization: Bearer`（localStorage
   令牌），无 cookie 会话。

---

## D1：实时通道的服务端形态

**Decision**：用 FastAPI/Starlette 自带 `StreamingResponse`
（`media_type="text/event-stream"`）输出标准事件流，零新增
依赖；不使用 WebSocket，不引 sse-starlette。

**Rationale**：
- 宪法既定"只用 SSE、不用 WebSocket"，且方向纯为服务器→客户端
  的状态广播，请求-响应式 StreamingResponse 完全够用；
- Starlette 已随 fastapi 安装，sse-starlette 提供的
  ping/重试便利在此用 20 行代码即可覆盖（心跳帧、收尾帧），不
  值当一条新依赖；
- 与 002 同步栈不冲突：端点是 `async def`，数据来自 asyncio
  队列（D3），不碰阻塞调用。

**Alternatives**：
- *WebSocket*：宪法明确排除；双向能力本特性用不到。
- *sse-starlette*：多一条第三方依赖与版本跟随，收益仅样板代码。
- *继续轮询*：违反 FR-001/FR-004（用户明确要实时取代轮询）。

## D2：事件流与图片的鉴权方式（关键安全决策）

**Decision**：浏览器端**不用原生 EventSource**，改用 `fetch`
+ ReadableStream 手动解析事件流帧，`Authorization: Bearer`
随请求头携带（与全站唯一令牌口径一致）；图片在前端以带 Bearer
的 fetch 取 blob 后转 `objectURL` 注入。令牌永不进入 URL。

**Rationale**：
- 原生 EventSource 不能设置请求头，社区惯例是把令牌放 query
  string——URL 会进访问日志、代理日志、浏览器历史，违反原则四
  的精神（密钥/令牌不落日志）且与 001 的 Bearer 双通道口径
  分裂；
- fetch 流式读取在目标平台（evergreen Chrome/Edge，002 plan
  已声明）原生可用；失败时 fetch 会像普通请求一样收到 401/404
  状态码，归属语义（404 不承认存在）在建立流之前即可判定；
- 图片同样不能用裸 `<img src="/api/...">`（无法带 Bearer），
  统一走"认证 fetch → Blob → objectURL"，注销时 revoke，行为
  与流式鉴权一致，不需要一次性签名票证机制。

**Alternatives**：
- *EventSource + query token*：实现最省事，但令牌进 URL/日志，
  否决。
- *短时一次性订阅票证*（先 POST 换票、再用票建流）：安全但要
  新增票证存储/TTL/一次性消费语义，对本特性的威胁模型（可信
  内网、本机）属过度设计；若未来公网加固需要可再加，不影响
  端点路径。
- *cookie 会话*：与现有 JWT 架构不符，会出现第二套身份载体。

## D3：进程内事件中枢与跨线程桥接

**Decision**：新增 `web/app/runner/events.py` 的 `ProgressHub`
（app.state 单例）：

- 订阅侧（asyncio 线程）：`subscribe(task_id)` 返回
  `asyncio.Queue(maxsize=256)` 并登记到
  `dict[task_id, set[Queue]]`；`unsubscribe` 移除；
- 启动时 lifespan 调 `hub.bind_loop(asyncio.get_running_loop())`
  保存主循环引用；
- 发布侧（worker 线程，同步）：`publish(task_id, payload)`
  通过 `loop.call_soon_threadsafe(...)` 把入队动作调度回主循环；
  队列满时丢弃**最旧的非终态**消息（终态/失败/取消消息强制
  保留：先挤掉旧消息，仍满则直接 put_nowait 覆盖最早位——
  实施时以"终态绝不丢"为断言）；
- 每条消息带任务内单调递增 `seq`（发布侧在主循环线程内自增，
  call_soon_threadsafe 保证有序）。
- 心跳：连接每 15 秒发送一帧 SSE 注释（`: ping\n\n`），不计
  seq、不触发状态更新。

**Rationale**：
- 002 已选 anyio 线程池，发布必然跨线程；
  call_soon_threadsafe 是标准库无锁桥接，避免自造锁+条件变量；
- 单调 seq 是 D4 快照/竞态收敛的依据；
- hub 不做持久化（规格假设：事件瞬时，不回放），进程重启后由
  002 recovery + 首帧快照收口。

**Alternatives**：
- *janus/第三方跨线程队列*：新依赖，不必要。
- *直接在 worker 操作 asyncio.Queue*：非线程安全，偶发丢消息，
  否决。
- *Redis pub/sub*：宪法/假设排除外部队列。
- *合并进 cancel_registry*：一个是执行期取消信号、一个是表现层
  订阅，生命周期与方向都不同，合并会互相牵连。

## D4：连接语义——首帧快照、竞态、晚订阅、终态收尾

**Decision**（端点 `GET /api/tasks/{id}/events`）：

1. 先完成认证与归属（未认证 401；不存在/越权直接 HTTP 404，
   不升级为流）。
2. **先订阅，再快照**：登记队列拿到订阅起点；随后记录
   `snap_seq = hub.current_seq(task_id)`，再从数据库读任务行
   （顺序保证见下）。
3. 首帧发 `event: snapshot`（载荷=002 TaskOut 同形 JSON）。
4. 之后转发队列中 **seq > snap_seq** 的事件帧
   （`event: update`/`event: terminal`）；≤ snap_seq 的丢弃
   （已被快照覆盖），重复帧天然幂等。
5. 若快照已是终态：只发 snapshot + 同内容 terminal 两帧后
   **正常关闭流**（晚订阅立即知终态，FR-003）。
6. 活动任务收到 terminal 帧后发完即关闭；客户端收到终态停止
   重连。
7. 客户端断线重连：退避 1→2→4→…封顶 10 秒，每次重连重新走
   2-6，全量以新快照对齐（不做 Last-Event-ID 回放：事件不
   持久化）。
8. worker 发布纪律：**先 commit 后 publish**（progress 与三个
   终态收口都在事务提交成功后才发）。因此"snap_seq 之前的
   事件必已落库"，步骤 4 的水位过滤不错乱：
   - 事件 N 在快照水位之后 → 队列里有，转发；
   - 事件 N 在水位之前 → 其 DB 状态必已包含在快照行内。
9. 通道连续失败（浏览器不支持 fetch 流或连续 N 次重连失败）：
   前端切换为 002 既有低频快照轮询（3 秒 GET 详情/列表），并
   保留手动刷新；任一时刻只存在一种持续刷新（FR-004）。

**Rationale**：先订阅再快照 + 单调水位是教科书式无缺口订阅
模式；不引入事件表即获得"不丢不重不漏终态"的体验（重复靠
seq/快照吸收）。

**Alternatives**：
- *先快照后订阅*：两步之间的事件会永久丢失，否决。
- *持久化事件表 + Last-Event-ID*：规格已决定不做时间线
  （002 data-model 亦如此预留），复杂度不值。
- *终态不关流*：无谓占用连接，且客户端无法干净停止重连。

## D5：阅读器正文来源与章节解析

**Decision**：新增 `services/artifacts.py`：
- `read_output(run_dir) -> str`：UTF-8 读取 output.md，文件
  缺失按"成品尚不可用"处理（succeeded 但文件被外部删除→500
  统一文案？见下，定为 409 `artifact_missing`，不泄露路径）；
- `parse_reader(md) -> {title, sections[]}`：
  - 首个 `# ` 行去井号为 title（缺失则回退任务 result_title）；
  - 以**行首 `## `** 为章节切点，章节 slug 用稳定序号
    `s0…sN`（不依赖中文标题做锚点，避免 slugify 中文问题），
    heading 取去掉 `## ` 的原文；章正文为到下一 `## ` 前的
    全部内容（Markdown 原文，含 `<details>`/表格 HTML）；
    `#` 标题与首章之间的前导文字并入 s0 之前的"前言"或丢弃
    （实测为空）；
  - 附录自然成为最后一个 section（heading 即"附录（折叠区）"，
    slug=sN），不为它造特殊类型，前端对 `<details>` 统一渲染；
- 只解析、不改写文件。

**Rationale**：
- 002 没有存 sections JSON，且 SC-007 要求 002 历史任务可读，
  解析 output.md 是唯一同时满足两者的方案；
- 切点规则简单且已用真实产物验证（7 个 `## ` + 可能的附录
  1 个）；序号 slug 让深链 `/tasks/:id/reader#s3` 稳定可测。

**Alternatives**：
- *成功时把 PaperResult.sections 落 reader.json*：002 历史任务
  没有此文件，仍要写解析器；双份内容有不一致风险，否决。
- *用 core 的 planner 常量做语义 slug*：web 层反向依赖 core
  内部章节概念，违反原则二边界精神；序号已够用。

## D6：浏览器渲染管线与消毒策略

**Decision**：`webui/src/lib/markdown.js` 导出唯一
`renderMarkdown(md)`：

1. markdown-it 实例（html:true 以保留折叠区/表格原文，
   linkify:false）挂载 markdown-it-texmath（delimiters 配置
   同时识别 `$...$` 与 `$$...$$`，KaTeX `throwOnError:false`
   ——单个公式异常不毁整页，降级显示原始定界文本）；
2. 覆盖 image rule：不输出真实 src，输出
   `<img alt data-art="{资源名}">` 占位（资源名做与服务端一致
   的安全字符合法性检查，非法直接渲染破损占位）；
3. 渲染为 HTML 后过 **DOMPurify**：
   - 允许：常规 Markdown 标签、`<details><summary>`、表格系、
     KaTeX 输出（span/annotation/MathML）、`img[data-art]`；
   - 禁止：`<script>`、`<style>`（外链）、所有 on* 事件属性、
     `javascript:` 链接、任何 src/href 指向外站或非受控形态；
   - 锚点统一加 `target="_blank" rel="noopener noreferrer"`；
4. 组件把消毒后的 HTML 注入容器，再遍历 `img[data-art]`，
   经 D7 取 objectURL 换 src；组件卸载 revoke 全部 URL；
5. KaTeX 的 CSS 与字体从本地 node_modules 打包引入（import
   'katex/dist/katex.min.css'），**不引 CDN**（零外联，FR-014）。

**Rationale**：成品内容来自第三方论文经 LLM 转写，本质是
不可信 HTML（折叠表格是 core 原样保留的外部 HTML）；必须
"先 html:true 保功能、再过白名单消毒"，不能简单 html:false
（那会让折叠区与表格退化成源码文本，US2 场景 6 不达标）。

**Alternatives**：
- *html:false 全文转义*：折叠区/原文表格不可用，违反 US2。
- *rehype-raw + unified 生态*：要引入整条 unified 插件链替代
  markdown-it，迁移面大；markdown-it + DOMPurify 是最短路径。
- *自己写正则消毒*：安全上不可接受。

## D7：受控图片加载与死链占位

**Decision**：
- 服务端端点 `GET /api/tasks/{id}/artifacts/images/{name}`：
  name 必须匹配 `^[A-Za-z0-9._-]+$`（无分隔符、无 `..`）+
  扩展名白名单 png/jpg/jpeg/gif/webp（svg 不放行——SVG 可嵌
  脚本，与消毒目标冲突）；`(images_dir/name).resolve()` 必须
  仍在 images_dir 之下；文件存在返回字节（按扩展名给
  content-type，下载型头不附服务器路径），否则 404；
- 前端 `fetchImageBlob(name)` 带 Bearer 请求，成功→
  `URL.createObjectURL(blob)`，按任务缓存（同图不重复拉）；
  失败（404 死链/非法名/越权）→ 替换为本地内联占位样式
  （"图片暂不可用"），不裂图标、不打断阅读；
- 终态非 succeeded 的任务，图片端点同样 409（不暴露 bytes）。

**Rationale**：复用 D2 的 Bearer-fetch 模式；白名单 + resolve
  双保险与 002 `paths._safe_segment` 同口径；死链占位是
  边界情况显式要求。

**Alternatives**：
- *放行 svg*：svg 可携带脚本，除非再做 XML 消毒，超范围，否决。
- *objectURL 不回收*：长会话泄漏内存，必须 revoke。

## D8：成品打包下载（zip）与文件名/Zip Slip 安全

**Decision**：`GET /api/tasks/{id}/artifact/download`
（仅 succeeded）返回一个 zip：

- 包内结构（单层根目录，解压不污染当前目录）：

  ```text
  <安全标题>/
  ├── <安全标题>.md      # 内容即 output.md 原始字节，逐字一致
  └── images/
      └── <真实存在的图片>  # 仅收录任务 images/ 下的实际文件
  ```

- 用标准库 `zipfile.ZipFile` 写入 `io.BytesIO`，以
  `Response(content=bytes, media_type="application/zip")`
  返回（单篇论文量级，数万字+数十图，内存可接受；不落临时
  文件）；
- **收录集合**：遍历该任务 `images/` 目录下真实存在的普通
  文件，扩展名复用 D7 白名单（png/jpg/jpeg/gif/webp；svg
  不收）；output.md 中引用但缺失的死链不伪造条目；
- **Zip Slip 防护**：所有条目名由服务端用已知安全段拼装
  （`<base>/<base>.md`、`<base>/images/<name>`），`name`
  经与 D7 相同的 `^[A-Za-z0-9._-]+$` 校验，绝不把磁盘路径
  或用户输入直接写入 arcname；写入前断言解压路径不会逃逸
  根目录（`..`/绝对路径/盘符不可能出现）；
- 名称安全：`safe_filename()` 作用于包根名与 .md 名（同源，
  均取 result_title → output.md 一级标题 →
  `paper-{task_id前8位}` 回退；剥离路径分隔符、控制字符、
  Windows 保留名，trim，限长 80）；根目录名与 md 名同名；
- 响应头：`Content-Disposition: attachment;
  filename="<ascii>.zip"; filename*=UTF-8''<编码名>.zip`，
  不含服务器绝对路径；
- 前端仍以带 Bearer 的 fetch 取 blob 后 `<a download>`
  触发保存（不能裸开窗、令牌不进 URL）。

**Rationale**：
- 用户明确要求图片随下载可用；只有"同结构 zip"能保证
  output.md 里的相对链接 `images/x.png` 在 Typora、VS Code、
  GitHub 等任意常见工具中开箱即用（base64 单文件会膨胀约 33%
  且部分查看器不渲染 data URI——已作为备选否决）；
- 单层根目录是 zip 分发惯例，避免解压散落；
- ZipFile 在写入侧构造 arcname，安全边界清晰，比读取解压侧
  过滤更简单可靠。

**Alternatives**：
- *单 .md + base64 内嵌*：自包含但膨胀、兼容性参差，否决；
- *只给 .md 不给图片*：用户已否决（本地打开图片失效）；
- *流式 zipfile（StreamingResponse + 队列）*：省内存但实现
  复杂（需要后台线程写管道），单论文量级不值当；若未来成品
  体积上兆级图片再改；
- *PDF/EPUB 导出、source.pdf 原件下载*：规格假设明确排除。

## D9：假物料增补（端到端真图片）

**Decision**：在 `web/fixtures/fake_run/parsed/` 增补：
- `images/sample.png`：一次性生成的最小合法 PNG（约 100–300
  字节，随仓库提交）；
- content_list.json 增 1 个 figure 块（id 顺延 blk-0011，
  img_path 指向 images/sample.png，带英文 caption），保持
  UTF-8 无 BOM；
- 预期链路：normalize→planner 把 figure 标 fold（repro_data
  组）→assemble `copy_images` 把图拷进任务 images/、折叠区出
  现 `![](images/sample.png)`、`check_image_links` 死链为空。
- 同步更新受影响的 002 断言：T008 的"13 块"描述与块计数断言
  改 14；涉及 total_chars 的断言均为 `>0` 下界不受影响；
  test_fake_mode 的"content_list 与 fixture 字节一致"仍成立
  （比的是复制前后）；core 测试一律不动。

**Rationale**：FR-014/SC-007 要求假模式端到端覆盖图片；只加
fixture 即可让真实 core 管线产出真图，无需在 web 层造假。

**Alternatives**：
- *另建阅读器静态夹具*：端到端链路（copy_images/死链修复/
  相对链接）就测不到，退化成单元自测。
- *用 equation 代替图片验收*：公式与图片是两类渲染要素，不能
  互相替代。

## D10：前端实时客户端、降级与多标签

**Decision**：`api/events.js` 导出 `openTaskStream(taskId,
{onSnapshot,onUpdate,onTerminal,onError,signal})`：
- `fetch('/api/tasks/{id}/events', {headers:{Authorization},
  signal})` 读 `response.body.getReader()`，按 SSE 分帧
  （`event:`/`data:` 行，空行分隔）解析 JSON；
- 401→交 001 的全局 401 处理（清会话跳登录）；404→任务不存在
  处理；网络错误/流中断→指数退避重连（上限 10 秒），页面
  可见期间持续；组件卸载→AbortController 中止；
- tasks store 用订阅替换 002 的 startPolling：列表页订阅什么？
  ——不做每任务 N 条流（列表可能很多历史任务）：列表打开时
  先 GET 一次列表，再仅对其中活动任务各开一条流；活动归零时
  不留流。详情页开单流；
- 连续 ≥5 次重连失败（约 30 秒）→降级为 002 的 3 秒快照轮询，
  并在获得一次成功流后可恢复实时；同一任务永不双跑。
- 多标签：各标签独立 fetch 连接，hub 支持同任务多订阅者
  （set[Queue]），无相互影响。

**Rationale**：活动任务数量天然受 002"每用户 1 个活动任务"
约束，故详情页 1 条流；列表页活动流数量 = 页面上活动任务数，
最坏每用户 1 条，无线性放大。

**Alternatives**：
- *一条用户级多路复用流*：要自定义事件路由协议，本特性任务数
  极低，不值得。
- *EventSource 库（eventsource polyfill）*：仍是 query token
  问题，且为新依赖。

## D11：002 兼容面与回归边界

**Decision**：
- DB schema、任务 API 既有字段与状态机零改动；TaskOut 不扩
  字段（阅读/下载由独立端点承载）；
- tasks router 追加 4 条路由（events/reader/图片/download），
  挂载顺序注意 `GET /{task_id}` 与新静态段不冲突（新路径均为
  `/{task_id}/<子段>`，FastAPI 按段精确匹配，不抢占）；
- worker 仅在 4 个函数内加"提交后 hub.publish"，条件 UPDATE
  语义与取消竞争代码一行不改；发布失败只 log 不影响任务落库
  （推送是表现层，绝不能让任务失败）；
- 002 的 131 项测试必须继续全绿（除 D9 明示的块计数同步）；
  新测试独立成文件：`test_realtime_events.py`、
  `test_artifacts.py`。

**Alternatives**：无——兼容性是硬约束而非可选项。

---

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| core 未来改 output.md 标题格式 | 阅读器解析失败 | 解析规则集中在 artifacts.py 单一模块；解析失败给可懂错误而非 500；本特性 core 零改动 |
| KaTeX 对恶意公式串的处理 | 单章渲染异常 | throwOnError:false 降级原文；DOMPurify 在其输出之后再过一遍 |
| fetch 流被企业代理缓冲 | 实时性失效 | 15 秒注释帧探活 + 失败计数降级轮询；quickstart 含代理场景说明（本机直连无此问题） |
| 折叠区原始 HTML 表格藏恶意属性 | XSS | DOMPurify 白名单（D6），表格标签保留但属性净化 |
| 慢消费者队列堆积 | 内存 | 256 上限 + 丢最旧非终态；终态不丢；单任务事件总量约十几次 |
