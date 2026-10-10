# 交接说明 · 纸慧 PaperWise（2026-10-09）

## 这个项目是什么

纸慧 PaperWise：把英文学术论文（PDF / arXiv 链接）转写成「先给结论、再讲过程」的
中文教程式阅读材料，供移动端/桌面端浏览器阅读。不是翻译，是换个讲法让初学者能懂。

- 产品形态：FastAPI 单端口（8000）同时托管 API 和 Vue 3 构建产物，Docker 部署
- 已上线：**https://app.paperwise.cn**（阿里云服务器，真实模式，Caddy 自动 HTTPS）
- 代码仓库：https://github.com/xiaolu7096/essay_transformer
- 与同服务器上的另一个项目 paper_wise_v1.5（paperwise.cn 主站，React+Caddy）
  共用一个 Docker 网络，互不干扰

## 技术栈与目录

```
core/          转写核心（纯 Python）：ingest→mineru→normalize→plan→rewrite→assemble
web/app/       FastAPI 后端：routers(auth/credentials/tasks/health)、runner 后台执行器、
               services(凭据加密/产物)、config 环境变量配置、models SQLite
webui/         Vue 3 + Vite 5 + Pinia + vue-router；纯 CSS 柔墨设计系统（tokens.css），
               明暗双主题；已做移动端响应式（AppLayout isMobile + 抽屉菜单）
docs/          core 层设计文档（架构/服务/数据模型）
specs/         speckit 规格 001~005（001~004 已完成，005 已上线，T026 人工目检待勾）
ui-showcase/   UI 设计稿 HTML（非运行内容）
data/ runs/    运行时数据（.gitignore，不入库；生产环境挂载在宿主机）
```

## 本地开发（Windows PowerShell）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r web\requirements.txt
Copy-Item web\.env.example web\.env   # 填入 WEB_JWT_SECRET / WEB_MASTER_KEY
uvicorn web.app.main:app --reload --port 8000   # 后端 + 托管已构建前端

cd webui; npm install; npm run dev    # 前端开发服务器（5173，代理到 8000）
npm run build                         # 产物输出 webui/dist
```

测试：`.\.venv\Scripts\python.exe -m pytest web/tests/ -q`

关键配置（web/app/config.py，全部环境变量，无默认密钥，缺失快速失败）：
`WEB_JWT_SECRET`（必填）、`WEB_MASTER_KEY`（Fernet，真实模式必填）、
`WEB_FAKE_MODE`、`WEB_MAX_CONCURRENT_TASKS=2`、`WEB_UPLOAD_MAX_MB=50`。
注意：fake 模式下未设 MASTER_KEY 会用进程内临时密钥，切换真实模式后旧凭据解密失败，
需在密钥页重新填写（见 BUGFIX_LOG Bug #2）。

## 生产部署现状（重要）

服务器目录：

```
/opt/paperwise/
├── paper_wise_v1.5/        现有主站；其 Caddy 容器是唯一占用 80/443 的入口
│   ├── docker-compose.yml  web 服务已挂 ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
│   └── deploy/Caddyfile    末尾追加了 app.paperwise.cn 反代块（与主站块平级）
└── essay_transformer/      本项目（git clone）
    ├── docker-compose.yml  单服务 web(container_name=essay-transformer)，
    │                       不占宿主机端口，接入外部网络 paper_wise_v15_default
    ├── .env                生产密钥（不入库）
    ├── data/               SQLite（挂载）
    └── runs/               PDF 与转写产物（挂载）
```

Caddy 反代配置（deploy/Caddyfile 末尾）：

```caddyfile
app.paperwise.cn {
	encode zstd gzip
	reverse_proxy essay-transformer:8000
}
```

日常运维（在 /opt/paperwise/essay_transformer）：
- 更新：`git pull && docker compose up -d --build`
- 日志：`docker compose logs -f`
- 备份：`tar czf backup.tar.gz data runs`
- 安全组只放行 22/80/443；DNS：app.paperwise.cn A 记录 → 服务器 IP（阿里云云解析）

踩过的坑（排查时先看这些）：
1. compose 的 volume 挂载行缩进容易错挂到别的服务下，改完用
   `docker inspect <容器> --format '{{range .Mounts}}...'` 核实
2. Caddyfile 中 `reverse_proxy essay-transformer:8000` 的冒号两边**不能有空格**，
   站点块必须与原站点**平级**不能嵌套；改配置后 `docker compose exec web caddy reload
   --config /etc/caddy/Caddyfile` 或 force-recreate
3. curl 验证时 URL 不要加反引号

## 本轮（2026-10-09）已完成

1. 任务列表卡片显示真实论文标题（displayTitle：result.title 优先，source_ref 回退）
2. 品牌定版「纸慧 / PaperWise」：BrandLogo 重写、全站文字/favicon/FastAPI title
3. 用户菜单修复：删除「个人设置」「反馈建议」，「帮助文档」禁用标"敬请期待"
4. Docker 化并上线 app.paperwise.cn（Dockerfile、docker-compose.yml、.env.example）
5. 代码推 GitHub；specs/、.specify/ 已移出入库范围（历史提交中仍在，见下）

待办遗留：
- specs/005 T026：等用户目检 runs/t026_real_survey/output.md 后勾选并更新 spec.md 状态行
- specs/ 与 .specify/ 仍存在于 git 历史提交中；若仓库转公开需先清历史（force push）

---

# 下一轮 Spec 规划（已与用户确认拆分）

## Spec 006：内测准入与帮助中心（先做）

### US1（P1）邀请码注册
- 新增配置 `WEB_INVITE_CODES=code-a,code-b`（.env，逗号分隔，重启生效）
- UserCreate 请求体加 invite_code；register 校验：码不匹配 → 403
- **已确认：WEB_INVITE_CODES 为空 = 关闭自助注册**（不是放开）
- 注册页 RegisterView 加邀请码输入框；登录页不动
- 不做管理后台、不做一次性码（MVP 一个码可重复使用）
- 后端测试四分支：无配置/无码/错码/对码
- 涉及：web/app/config.py、schemas.py、routers/auth.py、webui RegisterView、.env.example

### US2（P1）登录速率限制
- 用 slowapi 按 IP 限制 /api/auth/login（建议 5 次/分钟），超限 429
- 注意：Caddy 反代后后端拿到的是 Docker 内网 IP，需确认转发头
  （Caddy 默认带 X-Forwarded-For，slowapi 配置取该头；部署后用错误密码实测）

### US3（P2）帮助文档独立 HTML
- 新建 webui/public/help/index.html（Vite 原样拷贝，SPA 回退天然支持 /help/ 直链，
  无需改后端路由）
- 内容最小版：产品简介 → 3 步上手（配密钥/提交/阅读）→ 支持来源与限制 → FAQ
  （任务失败、密钥存储位置、处理时长）
- 样式复用柔墨 token，移动端可读
- AppLayout「帮助文档」从禁用态恢复为跳转 /help/（桌面菜单 + 移动抽屉两处）

### US4（P2）首个用户初始化
- 注册关闭后管理员建号途径：提供 `docker compose exec web python -m web.scripts.create_user
  <用户名> <密码>` 脚本（或文档化的一次性开启邀请码流程），spec 中明确选型

## Spec 007：移动端 PWA 化（006 上线后再做）

- webui 加 manifest.json（纸慧名、BrandLogo 全套尺寸图标、主题色、standalone）
- vite-plugin-pwa 接入 Service Worker：外壳离线 + 静态资源缓存
- 真机 UX 审查重点：任务提交（手机上传 PDF/粘贴 arXiv）、阅读页（长文/折叠区/图片）、
  **SSE 实时进度在锁屏/切后台/弱网后的断线重连**（本 spec 唯一技术风险点，单列验收）
- 明确不做：应用商店上架、推送通知、Capacitor 原生壳（确有需求再开 008）

## 给新对话的第一句话建议

> 读 HANDOFF.md，按 speckit 流程创建 Spec 006（内测准入与帮助中心）：
> 邀请码注册（WEB_INVITE_CODES 为空即关闭注册）、slowapi 登录限流、
> webui/public/help/index.html 帮助页、管理员建号脚本。不要动 core/ 转写逻辑。

## 风格约束（沿用前几轮）

- 配置走环境变量，密钥不硬编码；错误返回中文文案 + 机器码；不泄露账号是否存在
- 前端用柔墨 CSS 变量，不用组件库硬编码色值；任何新页面必须明暗主题 + 移动端可用
- 不过度工程：不提前做后台/一次性码/推送/原生壳
- 提交前跑 pytest web/tests/，前端改完 npm run build；部署走 git pull + compose 重建
