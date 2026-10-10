# Bug 修复日志

记录项目开发过程中发现和修复的 Bug，按时间倒序排列。

---

## 2026-10-09

### Bug #5：用户菜单按钮失效/名实不符

**严重程度**：中

**现象**：个人控制页（AppLayout 用户菜单）6 个按钮中，「帮助文档」「反馈建议」点击后只关闭菜单、无任何跳转；「个人设置」实际跳转到密钥管理页，与「密钥管理」入口重复。桌面下拉菜单与移动抽屉两处表现一致。

**根因**：
- `goHelp`/`goFeedback` 是空壳函数（注释"未来跳转"），路由表中也不存在 help/feedback 对应路由
- `goSettings` 为临时方案，直接 `router.push('/credentials')`，无独立设置页

**修复方式**（按用户确认的最小方案）：
- 删除「个人设置」「反馈建议」两个菜单项（桌面 + 移动抽屉同步）
- 「帮助文档」改为禁用态并标注"敬请期待"（待 Spec 006 帮助页上线后恢复跳转）
- 删除无用函数 goSettings/goHelp/goFeedback，菜单收敛为：我的任务、密钥管理、帮助文档、退出登录

**修改文件**：
- webui/src/layouts/AppLayout.vue（菜单模板、脚本、样式）

---

### 优化 #4：任务卡片展示真实论文标题

**现象**：任务列表卡片标题显示 arXiv ID（如 1910.07467）而非论文标题。

**修复方式**：
- TaskListView.vue 新增 displayTitle(task)：优先取 task.result?.title，回退 source_ref
- 取消/删除确认弹窗同步使用 displayTitle
- 边界：运行中/失败任务无标题只能回退 arXiv ID；fake 模式任务显示假标题

**修改文件**：webui/src/views/TaskListView.vue

---

### 变更：品牌定版「纸慧 / PaperWise」

- 重写 BrandLogo.vue（绿色圆角方块 + 白色书页嫩芽）
- 全站品牌文字（Landing/Login/Register/AppLayout）、index.html title、favicon（data URI）、FastAPI title 统一为纸慧 PaperWise

**修改文件**：webui/src/components/BrandLogo.vue、webui/index.html、4 个视图/布局、web/app/main.py

---

## 2026-10-07

### Bug #3：密钥设置页暗色主题下不可读

**严重程度**：中

**现象**：切换到暗色主题时，密钥设置页（CredentialsView）文字和背景对比度极低，几乎看不清。

**根因**：
- CredentialsView.vue 是 Spec 001 时代的老页面，使用 Naive UI 组件（n-card、n-form、n-input 等）
- 页面样式硬编码了 `#fff` 背景、`#6b7280` 文字等亮色主题色值
- Naive UI 的暗色主题覆盖不完整，与柔墨设计 token 不一致
- 该页面未纳入 Spec 004 UI 重写范围，成为遗漏项

**修复方式**：
- 完全重写 `webui/src/views/CredentialsView.vue`，移除 Naive UI 组件依赖，改用纯 CSS 自定义组件
- 全部使用柔墨设计系统的 CSS 变量（`--bg-card`、`--text-primary`、`--border` 等），明暗主题自动适配
- 新增功能：密码显隐切换、自定义确认弹框、骨架屏加载态、状态徽章
- 补充 `webui/src/styles/tokens.css` 中的组件语义别名 token（`--bg-card`、`--bg-input`、`--text-placeholder`、`--accent-color`、`--success-color`、`--warning-color`、`--error-color`）

**修改文件**：
- `webui/src/views/CredentialsView.vue`（重写）
- `webui/src/styles/tokens.css`（新增语义化 token）

---

### Bug #2：切换真实模式后任务在 ingest 阶段立即失败（InvalidToken）

**严重程度**：高

**现象**：从 fake 模式切换到真实模式（配置 WEB_MASTER_KEY 并设置 WEB_FAKE_MODE=0）后，提交新任务立即在 ingest 阶段失败，错误码 `system/internal_error`，日志显示 `InvalidToken()`。

**根因**：
- 用户此前在 fake_mode 下保存了 MinerU 和 DeepSeek 的 API Key
- fake_mode 下如果 `WEB_MASTER_KEY` 为空，系统会自动生成一个**进程内临时 Fernet 密钥**（见 `app/config.py` 第 108 行）
- 密钥是用这个临时密钥加密后存入数据库的
- 切换到真实模式后，主密钥变为 `WEB_MASTER_KEY` 配置的正式密钥
- 用新的主密钥解密旧密文 → Fernet 解密失败 → `InvalidToken`
- 任务启动时第一步就要解密密钥，所以在 ingest 阶段立即失败

**修复方式**：
- 非代码 bug，属环境配置/数据迁移问题
- 解决方案：进入密钥设置页，重新填写并保存 MinerU 和 DeepSeek 的 API Key，系统会用新的主密钥重新加密

**涉及文件**：
- `web/app/config.py`（临时密钥生成逻辑，正常行为）
- `web/app/services/credentials.py`（密钥解密逻辑，正常行为）
- `web/app/runner/pipeline_run.py`（启动时解密密钥）

---

### Bug #1：产品 Logo 与设计不符

**严重程度**：低

**现象**：全局使用的 Logo 是一个空心圆加两条曲线的抽象图形，辨识度低，与"林中一叶"的品牌定位不符。

**根因**：
- 早期开发时随手画的 SVG 占位 Logo，未按 `ui-showcase/phase4-demo.html` 中的设计稿实现
- 设计稿提供了 6 套 Logo 备选方案，其中「方案 2 · 林中一叶」为推荐方案

**修复方式**：
- 创建 `webui/src/components/BrandLogo.vue` 组件，实现"林中一叶"Logo（圆形深色底 + 倾斜叶片 + 叶脉）
- 全局 4 处 Logo 统一替换为 BrandLogo 组件
- 移除各处的内联 SVG Logo 代码

**修改文件**：
- `webui/src/components/BrandLogo.vue`（新增）
- `webui/src/layouts/AppLayout.vue`（替换 app-logo）
- `webui/src/views/LoginView.vue`（替换 logo-icon）
- `webui/src/views/RegisterView.vue`（替换 logo-icon）
- `webui/src/views/LandingView.vue`（替换 nav-logo + footer-logo）

---

## 说明

- 本日志仅记录需要代码修改或需要注意的 Bug/问题
- 配置类/环境类问题也记录，便于后续排查
- 修复验证后标记"已修复"，已知未修复的问题请注明状态
