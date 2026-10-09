# 接口契约：成品阅读、受控图片与打包下载

**日期**：2026-09-29 | **对应**：[spec.md](../spec.md) FR-005～011、US2/US3
| 上游：`002-task-pipeline/contracts/api-tasks.md`

- 基址 `/api`；全部端点需要 `Authorization: Bearer`
  （未认证 `401 {"detail":"未授权或登录已过期"}`）。
- 归属规则与 002 一致：任务不存在或属于他人，统一
  `404 {"detail":"任务不存在"}`，不泄露存在性。
- 所有端点只接收任务 id 与受控资源名，**没有任何端点接收文件
  系统路径**（FR-007/FR-011）。
- 正文、图片、打包下载**仅对 `status=succeeded` 的任务开放**；
  queued/running/failed/cancelled 一律 409
  `task_not_ready`，不返回半成品字节。

## 1. 阅读器内容

`GET /api/tasks/{task_id}/reader`

**200 OK**（任务已完成且成品可读）：

```json
{
  "task_id": "9a4b…",
  "title": "Sample Paper: A Demonstration of the Pipeline",
  "fake_mode": true,
  "total_chars": 923,
  "sections": [
    { "slug": "s0", "heading": "0 · 30 秒摘要", "content": "…Markdown 原文…" },
    { "slug": "s1", "heading": "1 · 这篇论文要解决什么问题（引言精讲）", "content": "…" }
  ]
}
```

- `sections[].slug`：`s0` 起按顺序编号，稳定且仅含
  `[a-z0-9]`；阅读器深链锚点 `#s3`；含折叠区的成品其附录为
  最后一个 section；
- `content`：该章 Markdown 原文（允许含 `$...$`/`$$...$$`、
  `<details><summary>…` 与原始 HTML 表格、
  `![alt](images/name.png)` 相对图片引用）；由浏览器侧统一
  渲染管线消毒后呈现（见 §5 前端约束）；
- `sections` 正常为 7 项、有折叠区为 8 项；服务端不强制数量；
  但成品无法解析出任何章节时按错误处理（见下）。

**错误**：

| 状态码 | body | 触发 |
|---|---|---|
| 401 | `{"detail":"未授权或登录已过期"}` | 未认证 |
| 404 | `{"detail":"任务不存在"}` | 不存在/越权 |
| 409 | `{"detail":"任务尚未完成，暂无可阅读的成品","code":"task_not_ready"}` | 任务非 succeeded |
| 409 | `{"detail":"成品文件缺失或已损坏，请重新提交任务","code":"artifact_missing"}` | succeeded 但 output.md 缺失/不可读/解析出 0 章 |

错误体不包含服务器路径与异常原文。

## 2. 受控图片

`GET /api/tasks/{task_id}/artifacts/images/{name}`

- `name` 规则（任一不满足即 404，不区分"非法名"与"不存在"）：
  - 匹配 `^[A-Za-z0-9._-]+$`（无路径分隔符、无 `..`、无空白
    与控制字符）；
  - 扩展名（小写比较）∈ `png` `jpg` `jpeg` `gif` `webp`；
    **不允许 svg**；
  - 服务端把 `images 目录/name` 解析为绝对路径后，必须仍位于
    该任务 `images/` 目录之内；
- 任务级门禁同 §1（401/404/409 task_not_ready）。

**成功**：**200 OK**

- `Content-Type` 按扩展名：image/png、image/jpeg、image/gif、
  image/webp；
- `Cache-Control: private, no-store`（响应仍需 Bearer 才能取得，
  不进入共享缓存）；
- Body：图片原始字节；响应头不含服务器路径。

**失败**：

| 状态码 | 触发 | 前端处理 |
|---|---|---|
| 401 | 未认证 | 全局登录处理 |
| 404 | 任务不存在/越权；name 非法；文件不存在（死链） | 本图替换"图片暂不可用"占位，不影响整章 |
| 409 `task_not_ready` | 任务未成功 | 不渲染正文，回到任务页 |

> 浏览器使用带 Authorization 头的 fetch 取图片 Blob 后转
> objectURL 注入（`<img src>` 本身无法携带 Bearer）；不得把
> 令牌拼进 URL。

## 3. 成品打包下载（zip）

`GET /api/tasks/{task_id}/artifact/download`

任务级门禁同 §1（401/404/409 task_not_ready；output.md 缺失
为 409 artifact_missing）。

**成功**：**200 OK**

- `Content-Type: application/zip`
- `Content-Disposition: attachment; filename="<ascii>.zip";
  filename*=UTF-8''<百分号编码名>.zip`
- Body：一个 zip 压缩包，单层根目录结构：

  ```text
  <base>/
  ├── <base>.md        # 成品 Markdown：output.md 原始字节，逐字一致
  └── images/
      └── …            # 成品引用且磁盘真实存在的图片，相对结构不变
  ```

- `<base>.md` 内容与阅读器成品**同源逐字一致**；Markdown 中
  的相对图片链接 `images/<name>` 与包内目录结构对应，解压后
  在常见 Markdown 工具中图文完整可看；
- 仅收录该任务 images/ 下**真实存在**、扩展名属于
  png/jpg/jpeg/gif/webp 的普通文件；死链不产生条目；无图片
  成品的 zip 只含 Markdown；
- 包内条目名全部由服务端用安全段拼装（根名与 md 名同为
  `<base>`，图片条目固定 `<base>/images/<name>`），不存在
  Zip Slip（条目名不可能含 `..`、绝对路径或盘符）；响应头与
  包元数据不含服务器绝对路径。

`<base>` 命名规则（服务端 `safe_filename`）：

1. 来源：任务结果标题，缺失时取 output.md 一级标题，再缺失用
   `paper-{task_id 前 8 位}`；
2. 删除控制字符与 `\/:*?"<>|`，去掉 Windows 保留名
   （CON、PRN、AUX、NUL、COM1-9、LPT1-9），trim 首尾点与
   空格，折叠连续空白；
3. 限长 80 字符；净化后为空则回退 `paper-{task_id 前 8 位}`；
4. 压缩包文件名追加 `.zip`；ASCII 回退名仅保留 ASCII 安全
   字符（中文等非 ASCII 字符在 filename 中以 `-` 替代），原名
   完整保留在 `filename*` 的 UTF-8 百分号编码中。

示例：标题 `Attention Is All You Need` →

```text
Content-Disposition: attachment; filename="Attention Is All You Need.zip";
  filename*=UTF-8''Attention%20Is%20All%20You%20Need.zip
```

包内：`Attention Is All You Need/Attention Is All You Need.md`
与 `Attention Is All You Need/images/…`。

前端以带 Bearer 的 fetch 取 Blob，再以 `<a download>` 触发
保存；不打开裸 URL、不把令牌放入 URL。

## 4. 与 002 任务接口的关系

- 不修改 002 的 TaskOut 字段；阅读/下载入口的可见性由前端按
  `status == succeeded` 控制，后端以 409 兜底；
- 推送（见 api-realtime.md）到达 terminal/succeeded 即阅读
  入口可出现的时刻；
- 失败原因（三分类）仍由 002 任务详情承载，阅读器不重复。

## 5. 前端渲染安全约束（契约性要求）

1. 任何视图不得绕过统一的 Markdown 渲染模块输出成品 HTML；
2. 必须先渲染再经白名单消毒：允许常规排版标签、
   `<details>/<summary>`、表格系、公式排版输出；移除脚本、
   样式外链、所有事件属性与 `javascript:` 目标；
3. 图片只允许指向本任务受控端点（前端以 data-art 占位 →
   认证 Blob → objectURL 注入，离开页面时 revoke）；渲染过程
   不得发起任何非受控外站请求；
4. 公式定界符沿用成品现状：行内 `$...$`、行间 `$$...$$`；
   单个公式渲染失败降级显示原文，不阻断整章；
5. KaTeX 样式与字体随前端打包在本地提供，不使用 CDN。

## 6. 本契约明确不提供

- source.pdf 原件下载、逐张图片的单独下载入口、PDF/EPUB 导出；
- 成品编辑、评论、批注；
- 跨任务成品检索；
- 任何接受文件系统路径参数的入口。
