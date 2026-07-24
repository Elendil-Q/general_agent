# DeerFlow 配置指南

本指南汇总 DeerFlow 运行所需的全部重要配置，帮助你理解 `.env`、`config.yaml`、`extensions_config.json` 与前端环境变量之间的关系，并安全、快速地完成部署。

> 如果你只想快速跑起来，可以先参考 [README_zh.md](../README_zh.md) 的“配置”小节。本文档是面向生产与深度定制的完整参考。
> 后端模型、工具、沙箱等细节还可参阅 [backend/docs/CONFIGURATION.md](../backend/docs/CONFIGURATION.md)。

---

## 目录

- [配置体系概览](#配置体系概览)
- [根目录 `.env` 完全参考](#根目录-env-完全参考)
- [`config.yaml` 分段参考](#configyaml-分段参考)
- [`extensions_config.json` 参考](#extensions_configjson-参考)
- [前端环境变量参考](#前端环境变量参考)
- [Docker / 部署环境变量参考](#docker--部署环境变量参考)
- [安全配置注意事项](#安全配置注意事项)
- [常用配置示例](#常用配置示例)
- [故障排查](#故障排查)
- [参考与延伸阅读](#参考与延伸阅读)

---

## 配置体系概览

DeerFlow 的配置由四层组成：

| 配置层 | 文件 | 用途 | 生效方式 |
|--------|------|------|----------|
| 环境变量 | 项目根目录 `.env` | 密钥、API key、端口、Docker 构建参数、代理等 | 进程启动时读取 |
| 主配置 | 项目根目录 `config.yaml` | 模型、工具、沙箱、内存、数据库、IM 渠道等 | 热重载 + 部分字段需重启 |
| 扩展配置 | 项目根目录 `extensions_config.json` | MCP Server、skills 开关状态 | 运行时可通过 API 修改 |
| 前端环境变量 | `frontend/.env` | Next.js 路由重写、SSR Gateway 地址、认证开关等 | 构建 / SSR 时读取 |

### 文件位置与读取优先级

- **`config.yaml`**：默认读取项目根目录。可通过 `DEER_FLOW_CONFIG_PATH` 指向任意路径，或通过 `DEER_FLOW_PROJECT_ROOT` 指定项目根目录。
- **`extensions_config.json`**：与 `config.yaml` 同样的优先级规则，由 `DEER_FLOW_EXTENSIONS_CONFIG_PATH` 覆盖。
- **`.env`**：由 `make dev` / `make up` / Docker Compose 自动加载，也可在 shell 中手动 `export`。
- **`frontend/.env`**：前端独立加载，Docker 模式下由 `docker-compose.yaml` 通过 `env_file` 注入 frontend 服务。

`config.yaml` 具体查找顺序：

1. 代码中显式传入的 `config_path`
2. `DEER_FLOW_CONFIG_PATH` 环境变量
3. `DEER_FLOW_PROJECT_ROOT` 下的 `config.yaml`，或当前工作目录下的 `config.yaml`
4. 后端兼容路径

### 环境变量替换

`config.yaml` 与 `extensions_config.json` 中所有字段值都支持 `$VAR_NAME` 形式的环境变量引用。例如：

```yaml
models:
  - name: gpt-4
    api_key: $OPENAI_API_KEY
```

> 建议：所有密钥与凭据都通过 `.env` 注入，避免在配置文件中写死。

### 配置版本与升级

`config.example.yaml` 顶部包含 `config_version: 16`。启动时，DeerFlow 会对比你的 `config.yaml` 版本与示例版本；如果示例版本更高，会输出如下警告：

```
WARNING - Your config.yaml (version 0) is outdated — the latest version is 16.
Run `make config-upgrade` to merge new fields into your config.
```

运行 `make config-upgrade` 可自动合并新增字段，保留你的现有值，并生成 `.bak` 备份。

### 热重载 vs 必须重启

Gateway 在每次请求时都会重新读取 `config.yaml`，因此以下字段修改后在**下一条消息**即可生效：

- `models[*].max_tokens` 等模型参数
- `summarization.*`、`title.*`、`memory.*`
- `subagents.*`、`tools[*]`、system prompt

以下基础设施字段属于 **`STARTUP_ONLY_FIELDS`**，修改后必须**重启 Gateway** 才能生效：

| 字段 | 原因 |
|------|------|
| `database` | SQLAlchemy engine 在启动时初始化并持有连接池 |
| `checkpointer` | 持久化 checkpointer 在启动时绑定 |
| `run_events` | 事件存储后端在启动时选定 |
| `stream_bridge` | 流桥单例在启动时构造 |
| `sandbox` | 沙箱 Provider 单例在启动时缓存 |
| `log_level` | 日志级别只在启动时应用 |
| `channels` | IM 渠道客户端在启动时创建 |
| `channel_connections` | 渠道连接仓库在启动时装配 |

完整注册表见 `backend/packages/harness/deerflow/config/reload_boundary.py::STARTUP_ONLY_FIELDS`。

---

## 根目录 `.env` 完全参考

`.env.example` 中的变量按用途分组如下。

### 必填搜索 / 抓取 API Key

DeerFlow 默认启用 `web_search`（DuckDuckGo，无需 API key）与 `web_fetch`（Jina AI）。但如果你使用以下 provider，则必须配置对应 key：

| 变量 | 用途 |
|------|------|
| `SERPER_API_KEY` | Serper（Google 搜索 / 图片） |
| `TAVILY_API_KEY` | Tavily 搜索 |
| `JINA_API_KEY` | Jina AI Reader（默认 `web_fetch` provider） |
| `INFOQUEST_API_KEY` | InfoQuest 搜索 / 抓取 |

### 可选 LLM API Key

在 `config.yaml` 的 `models` 中按需引用：

| 变量 | 对应 provider |
|------|---------------|
| `OPENAI_API_KEY` | OpenAI / OpenAI 兼容网关 |
| `ANTHROPIC_API_KEY` | Anthropic Claude |
| `GEMINI_API_KEY` | Google Gemini |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `VOLCENGINE_API_KEY` | 火山引擎 / 豆包 |
| `NOVITA_API_KEY` | Novita AI（OpenAI 兼容） |
| `MINIMAX_API_KEY` | MiniMax（OpenAI 兼容） |
| `STEPFUN_API_KEY` | 阶跃星辰（OpenAI 兼容） |
| `VLLM_API_KEY` | vLLM 推理服务 |
| `MIMO_API_KEY` | 小米 MiMo |
| `MOONSHOT_API_KEY` | Moonshot |
| `ATLASCLOUD_API_KEY` | Atlas Cloud |

### 可选搜索 / 抓取 provider

| 变量 | 用途 |
|------|------|
| `FIRECRAWL_API_KEY` | Firecrawl 抓取 |
| `BRAVE_SEARCH_API_KEY` | Brave Search |
| `EXA_API_KEY` | Exa 搜索 / 抓取 |
| `GROUNDROUTE_API_KEY` | GroundRoute 元搜索 |
| `CRW_API_KEY` | fastCRW 云版 |

### Gateway 与 CORS

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GATEWAY_CORS_ORIGINS` | 空 | 逗号分隔的精确来源，用于跨域或端口转发场景；通过 nginx 统一入口时无需设置 |
| `GATEWAY_ENABLE_DOCS` | 空（即启用） | 设为 `"false"` 关闭 Swagger UI、ReDoc、OpenAPI schema |

### 内部认证

| 变量 | 说明 |
|------|------|
| `DEER_FLOW_INTERNAL_AUTH_TOKEN` | 多 worker / IM 渠道内部通信用的共享 token；`make up` 会自动生成并持久化 |

### 前端 SSR → Gateway 连接

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` | `http://localhost:8001` | Next.js SSR 访问 Gateway 的地址 |
| `DEER_FLOW_TRUSTED_ORIGINS` | `http://localhost:3000,http://localhost:2026` | SSR 认证信任来源 |

### CLI 认证（Claude Code / Codex 作为模型 provider）

| 变量 | 说明 |
|------|------|
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude Code OAuth token（推荐，无需挂载目录） |
| `ANTHROPIC_AUTH_TOKEN` | Anthropic auth token |
| `CLAUDE_CODE_CREDENTIALS_PATH` | 指向单个 `.credentials.json` 文件 |
| `CODEX_AUTH_PATH` | 指向单个 Codex `auth.json` 文件 |

> 优先使用 env token；只有 ACP adapter 需要完整 CLI 配置目录时，才使用 `docker-compose.cli-auth.yaml` overlay。

### 数据库

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | Postgres 连接串，仅当 `database.backend: postgres` 时需要，例如 `postgresql://deerflow:password@localhost:5432/deerflow` |

### IM 渠道 Bot 凭据

| 变量 | 用途 |
|------|------|
| `FEISHU_APP_ID`、`FEISHU_APP_SECRET` | 飞书 / Lark |
| `SLACK_BOT_TOKEN`、`SLACK_APP_TOKEN` | Slack（Socket Mode） |
| `TELEGRAM_BOT_TOKEN` | Telegram |
| `DISCORD_BOT_TOKEN` | Discord |
| `WECOM_BOT_ID`、`WECOM_BOT_SECRET` | 企业微信智能机器人 |
| `DINGTALK_CLIENT_ID`、`DINGTALK_CLIENT_SECRET` | 钉钉 |
| `TELEGRAM_BOT_USERNAME`、`WECHAT_BOT_TOKEN`、`WECHAT_ILINK_BOT_ID` | 其他渠道（参见 `config.example.yaml`） |

### 可观测性（Tracing）

| 变量 | 说明 |
|------|------|
| `LANGSMITH_TRACING` | 设为 `"true"` 启用 LangSmith |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` |
| `LANGSMITH_API_KEY` | LangSmith API key |
| `LANGSMITH_PROJECT` | 项目名 |
| `LANGFUSE_TRACING` | 设为 `"true"` 启用 Langfuse |
| `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY` | Langfuse 凭据 |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` |
| `DEER_FLOW_ENV` / `ENVIRONMENT` | 追踪标签 `env:<value>` |

### GitHub

| 变量 | 用途 |
|------|------|
| `GITHUB_TOKEN` | GitHub Deep Research skill、MCP GitHub server 等 |

### 服务端口

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `2026` | nginx 对外端口（Docker 模式下映射到宿主机；本地模式下 nginx 直接监听） |
| `GATEWAY_PORT` | `8001` | Gateway API 端口 |
| `FRONTEND_PORT` | `3000` | Next.js 端口 |
| `PROVISIONER_PORT` | `8002` | 仅 provisioner / K8s 模式使用 |

### Docker 构建参数

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `UV_EXTRAS` | 空 | 后端 extras，例如 `postgres`、`ollama`；Docker build 时只支持单个 token |
| `UV_IMAGE` | `ghcr.io/astral-sh/uv:latest` | uv 基础镜像 |
| `UV_INDEX_URL` | `https://pypi.org/simple` | PyPI 索引 |
| `APT_MIRROR` | 空 | apt 镜像 |
| `NPM_REGISTRY` | 空 | npm/pnpm registry 镜像 |
| `PNPM_STORE_PATH` | `/root/.local/share/pnpm/store` | 容器内 pnpm store |
| `PIP_INDEX_URL` | 空 | provisioner 镜像 pip 索引 |

### 代理

| 变量 | 说明 |
|------|------|
| `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` | 全局代理 |
| `NO_PROXY`、`no_proxy` | Compose 会自动追加内部服务主机名 |

---

## `config.yaml` 分段参考

以下按 `config.yaml` 顶级段组织。标注 ⛔ 的段属于 `STARTUP_ONLY_FIELDS`，修改后必须重启 Gateway。

### `config_version` / `log_level`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `config_version` | `16` | 配置版本号，用于检测过时配置 |
| `log_level` | `info` | deerflow/app 日志级别：`debug`/`info`/`warning`/`error`；⛔ 重启生效 |

### `token_usage`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 是否记录每次模型调用的 input/output/total token，并在 UI 展示 |

### `token_budget`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 是否启用单轮 token 预算 |
| `max_tokens` | `200000` | 单轮总 token 上限（input + output） |
| `max_input_tokens` | `null` | 单独 input 上限 |
| `max_output_tokens` | `null` | 单独 output 上限 |
| `warn_threshold` | `0.8` | 达到该比例时向 agent 发出上下文警告 |
| `hard_stop_threshold` | `1.0` | 达到该比例时剥离 tool_calls 并强制给出最终回答 |

### `models`

定义可用的 LLM。通用字段：

| 字段 | 说明 |
|------|------|
| `name` | 内部标识，供 UI 与运行时选择 |
| `display_name` | 前端展示名 |
| `use` | Provider 类路径，例如 `langchain_openai:ChatOpenAI`、`deerflow.models.patched_mimo:PatchedChatMiMo` |
| `model` | Provider 侧的模型 ID |
| `api_key` / `gemini_api_key` | API key，建议 `$ENV_VAR` |
| `base_url` / `api_base` | Provider 基础 URL |
| `max_tokens` | 最大输出 token |
| `context_window` | 最大上下文窗口 |
| `temperature` | 采样温度 |
| `supports_thinking` | 是否支持扩展思考 |
| `supports_reasoning_effort` | 是否支持 reasoning effort |
| `supports_vision` | 是否支持视觉输入（启用后会加入 `view_image` 工具） |
| `when_thinking_enabled` / `when_thinking_disabled` | thinking 开关时的额外请求体 |
| `use_responses_api` / `output_version` | OpenAI Responses API |

常用 provider：OpenAI、Anthropic、DeepSeek、Gemini、Ollama、MiMo、MiniMax、StepFun、Novita、vLLM、MindIE、Claude Code OAuth、Codex CLI 等。

> 详细 provider 示例与特殊说明（如 Gemini thought_signature、MiMo reasoning_content）见 [backend/docs/CONFIGURATION.md#models](../backend/docs/CONFIGURATION.md#models)。

### `tool_groups` / `tools`

默认 tool groups：`web`、`file:read`、`file:write`、`bash`。

内置工具：

| 工具 | 默认 provider | 说明 |
|------|---------------|------|
| `web_search` | DuckDuckGo | 可切换 Serper、Brave、Tavily、InfoQuest、Exa、Firecrawl、GroundRoute、fastCRW、SearXNG |
| `web_fetch` | Jina AI | 可切换 Browserless、Exa、InfoQuest、Firecrawl、GroundRoute、fastCRW |
| `image_search` | DuckDuckGo | 可切换 InfoQuest、Serper |
| `ls`、`read_file`、`glob`、`grep` | 本地沙箱 | 文件读取类 |
| `write_file`、`str_replace` | 本地沙箱 | 文件写入类 |
| `bash` | 本地沙箱 | 仅在隔离沙箱或 `sandbox.allow_host_bash: true` 时可用 |

> 工具 provider 详细配置见 [backend/docs/CONFIGURATION.md#tools](../backend/docs/CONFIGURATION.md#tools)。

### `tool_search`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 启用延迟加载：MCP 工具只在系统提示中列出名称，运行时通过 `tool_search` 发现 |

### `tool_output`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 工具输出预算保护开关 |
| `externalize_min_chars` | `12000` | 超过该字符数的结果落盘，模型看到 preview + 文件引用 |
| `preview_head_chars` / `preview_tail_chars` | `2000` / `1000` | preview 头尾字符数 |
| `fallback_max_chars` / `fallback_head_chars` / `fallback_tail_chars` | `30000` / `8000` / `3000` | 落盘失败时的截断策略 |
| `storage_subdir` | `.tool-results` | 落盘子目录 |
| `exempt_tools` | `["read_file", "read_file_tool"]` | 豁免工具输出预算，防止 read→persist 死循环 |
| `tool_overrides` | `{}` | 按工具自定义 `externalize_min_chars` |

### `suggestions`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 是否在每次回复末尾自动生成 follow-up 问题建议 |

### `loop_detection`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 循环检测总开关 |
| `warn_threshold` | `3` | 相同 tool-call 集合重复多少次后警告 |
| `hard_limit` | `5` | 相同 tool-call 集合重复多少次后强制停止 |
| `window_size` | `20` | 每个线程追踪的最近 tool-call 集合数 |
| `max_tracked_threads` | `100` | 同时追踪循环历史的最大线程数 |
| `tool_freq_warn` | `30` | 同一工具调用多少次后频率警告 |
| `tool_freq_hard_limit` | `50` | 同一工具调用多少次后强制停止 |
| `tool_freq_overrides` | `{}` | 按工具覆盖 `{warn, hard_limit}` |

### `safety_finish_reason`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 拦截因安全原因终止的 AIMessage，避免执行被截断的 tool_calls |
| `detectors` | `null` | 自定义检测器列表；留空使用内置集合（OpenAI content_filter、Anthropic refusal、Gemini SAFETY 等） |

### `uploads`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `max_files` | `10` | 单次上传文件数上限 |
| `max_file_size` | `52428800`（50 MiB） | 单个文件大小上限 |
| `max_total_size` | `104857600`（100 MiB） | 单次上传总大小上限 |
| `auto_convert_documents` | `false` | 是否在网关主机自动转换 Office/PDF；仅在完全可信来源时开启 |
| `pdf_converter` | `auto` | PDF 转换器：`auto` / `pymupdf4llm` / `markitdown` |

### `sandbox` ⛔

DeerFlow 支持三种沙箱模式：

| 模式 | `config.yaml` 写法 | 是否挂载 Docker socket | 隔离级别 |
|------|--------------------|------------------------|----------|
| Local（默认） | `use: deerflow.sandbox.local:LocalSandboxProvider` | 否 | 在 Gateway 容器/宿主机文件系统直接执行，非强隔离 |
| AIO / DooD | `use: deerflow.community.aio_sandbox:AioSandboxProvider`（无 `provisioner_url`） | **是**（opt-in overlay） | 通过宿主机 Docker 启动独立容器 |
| Provisioner / K8s | `use: deerflow.community.aio_sandbox:AioSandboxProvider` + `provisioner_url` | 否 | 通过 provisioner 在 K8s Pod 中运行，隔离最强 |

Local 模式关键字段：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `allow_host_bash` | `false` | 是否允许宿主机 bash；默认关闭，仅在可信单用户本地环境开启 |
| `mounts` | `[]` | 额外挂载（Docker 模式下需同时在 `docker-compose.yaml` 中 bind-mount） |
| `bash_output_max_chars` | `20000` | bash 输出截断字符数（head+tail） |
| `read_file_output_max_chars` | `50000` | read_file 输出截断字符数 |
| `ls_output_max_chars` | `20000` | ls 输出截断字符数 |
| `bash_command_timeout` | `600` | 单个 bash 命令最大运行秒数 |

AIO 模式关键字段：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `image` | `enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest` | 沙箱容器镜像 |
| `port` | `8080` | 沙箱容器起始端口 |
| `replicas` | `3` | 最大并发沙箱数，LRU 淘汰 |
| `container_prefix` | `deer-flow-sandbox` | 容器名前缀 |
| `mounts` | `[]` | 额外挂载（skills 目录会自动挂载） |
| `environment` | `{}` | 注入沙箱容器的环境变量，支持 `$VAR` |

Provisioner 模式：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  provisioner_url: http://provisioner:8002
```

Pod 使用 provisioner 的 `SANDBOX_IMAGE` 环境变量，而不是 `sandbox.image`。

> 沙箱详细说明、自定义镜像、安全权衡见 [backend/docs/CONFIGURATION.md#sandbox](../backend/docs/CONFIGURATION.md#sandbox)。

### `efforts`

Effort 是前端概念：用户在输入框选择 effort 后，前端派生出运行时标志（`thinking_enabled`、`is_plan_mode`、`subagent_enabled`、`reasoning_effort`）发给后端。后端只接收派生标志，不接收 effort 名称。

未配置 `efforts` 时，内置四档：

| effort | `thinking_enabled` | `is_plan_mode` | `subagent_enabled` | `reasoning_effort` |
|--------|--------------------|----------------|--------------------|--------------------|
| `flash` | `false` | `false` | `false` | `minimal` |
| `thinking` | `true` | `false` | `false` | `low` |
| `pro` | `true` | `true` | `false` | `medium` |
| `ultra` | `true` | `true` | `true` | `high` |

可在 `config.yaml` 中覆盖任意 effort 的标志。

### `subagents`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `timeout_seconds` | `1800`（30 分钟） | 内置 subagent 默认超时 |
| `max_turns` | `null` | 全局最大轮数覆盖；内置默认值 general-purpose=150，bash=60 |
| `agents` | `{}` | 按 agent 覆盖 timeout、max_turns、model、skills |
| `custom_agents` | `{}` | 自定义 subagent 类型，可配置 system_prompt、tools、skills、model、workflow 等 |

### `acp_agents`

配置外部 ACP-compatible agent，供内置 `invoke_acp_agent` 工具调用。每个条目包含 `command`、`args`、`env`、`description`、`model`、`auto_approve_permissions`。

示例：

```yaml
acp_agents:
  claude_code:
    command: npx
    args: ["-y", "@zed-industries/claude-agent-acp"]
    description: Claude Code for implementation and debugging
    env:
      ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY
```

### `skills`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `path` | 项目根目录下 `skills` | skills 目录；可用 `DEER_FLOW_SKILLS_PATH` 覆盖 |
| `container_path` | `/mnt/skills` | 沙箱容器内的挂载路径 |

自定义 agent 可通过 `agents/<name>/config.yaml` 中的 `skills` 字段做白名单控制：

- `null` / 省略：加载所有全局启用 skills
- `[]`：禁用所有 skills
- `["skill-name"]`：仅加载指定 skills

### `chains`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `path` | 项目根目录下 `chains` | chain pipeline YAML 目录；可用 `DEER_FLOW_CHAINS_PATH` 覆盖 |

通过 `/chain:<name>` slash 命令触发，文件位于 `chains/public/` 或 `chains/custom/`。

### `title`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 自动生成会话标题 |
| `max_words` | `6` | 最大词数 |
| `max_chars` | `60` | 最大字符数 |
| `model_name` | `null` | 使用默认模型 |

### `summarization`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 自动摘要开关 |
| `model_name` | `null` | 摘要模型，建议使用轻量模型 |
| `trigger` | `tokens: 32000` | 触发条件（`tokens` / `messages` / `fraction`，OR 逻辑） |
| `keep` | `messages: 10` | 摘要后保留的最近历史 |
| `trim_tokens_to_summarize` | `15564` | 准备摘要时的最大 token 数 |
| `summary_prompt` | `null` | 自定义摘要 prompt |
| `preserve_recent_skill_count` | `5` | 最近加载的 skill 文件不进入摘要 |
| `preserve_recent_skill_tokens` | `25000` | 保留 skill 文件的总 token 预算 |
| `preserve_recent_skill_tokens_per_skill` | `5000` | 单个 skill 文件的 token 上限 |
| `skill_file_read_tool_names` | `["read_file", "read", "view", "cat"]` | 视为 skill 读取的工具名 |

### `memory`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 记忆总开关 |
| `storage_path` | `memory.json` | 存储路径；绝对路径会退出按用户隔离 |
| `debounce_seconds` | `30` | 处理队列更新的等待时间 |
| `model_name` | `null` | 更新记忆的模型 |
| `max_facts` | `100` | 最大保存事实数 |
| `fact_confidence_threshold` | `0.7` | 存储事实的最小置信度 |
| `injection_enabled` | `true` | 是否将记忆注入系统提示 |
| `max_injection_tokens` | `2000` | 记忆注入的 token 预算 |
| `token_counting` | `tiktoken` | `tiktoken`（准确，可能联网下载 BPE）或 `char`（离线、CJK 感知） |
| `guaranteed_categories` | `["correction"]` | 保证注入的事实类别 |
| `guaranteed_token_budget` | `500` | 保证类别的事实 token 上限 |

### `agents_api`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 是否暴露自定义 agent 的 SOUL/USER.md 管理 HTTP API |

### `skill_evolution`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 是否允许 agent 自动在 `skills/custom/` 创建/改进 skill |
| `moderation_model_name` | `null` | 安全扫描模型 |

### `database` ⛔

统一存储后端，同时驱动 LangGraph checkpointer 与 DeerFlow 应用数据（runs、threads、feedback 等）。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `backend` | `sqlite` | `memory` / `sqlite` / `postgres` |
| `sqlite_dir` | `.deer-flow/data` | SQLite 文件目录 |
| `postgres_url` | `""` | Postgres URL，通常 `$DATABASE_URL` |
| `echo_sql` | `false` | 是否打印所有 SQL |
| `pool_size` | `5` | Postgres 连接池大小 |

使用 Postgres 时需在 `.env` 中设置 `DATABASE_URL`，并安装 extras：

```bash
# 本地
UV_EXTRAS=postgres

# Docker build
UV_EXTRAS=postgres docker compose build
```

### `checkpointer`（已废弃）

独立 checkpointer 配置，仅用于向后兼容。建议改用 `database`。若两者同时存在，`checkpointer` 对 LangGraph 状态优先生效。

### `run_events` ⛔

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `backend` | `memory` | `memory` / `db` / `jsonl` |
| `max_trace_content` | `10240` | db 后端下 trace 内容截断字节数 |
| `track_token_usage` | `true` | 是否累计 token 使用量到 RunRow |

### `channel_connections` ⛔

让用户在前端绑定自己的 IM 账号，复用下方 `channels` 的运行时配置。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 总开关 |
| `require_bound_identity` | `true` | 是否要求绑定身份；关闭后未绑定的外部用户也可创建 run |
| `telegram` / `slack` / `discord` / `feishu` / `dingtalk` / `wechat` / `wecom` | `enabled: false` | 各渠道开关 |

### `channels` ⛔

连接外部 IM 平台。所有渠道都使用出站连接（WebSocket 或轮询），无需公网 IP。

公共字段：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `langgraph_url` | `http://localhost:8001/api` | Gateway LangGraph-compatible API 地址 |
| `gateway_url` | `http://localhost:8001` | Gateway REST API 地址 |
| `session.assistant_id` | `lead_agent` | 默认 agent；可填自定义 agent 名 |
| `session.config.recursion_limit` | `100` | 递归限制 |
| `session.context.thinking_enabled` | `true` | 是否启用 thinking |
| `session.context.is_plan_mode` | `false` | 是否启用 plan mode |
| `session.context.subagent_enabled` | `false` | 是否启用 subagent |

各渠道：

| 渠道 | 关键字段 |
|------|----------|
| `feishu` | `app_id`、`app_secret`、`domain` |
| `slack` | `bot_token`（`xoxb-...`）、`app_token`（`xapp-...`）、`allowed_users` |
| `telegram` | `bot_token`、`allowed_users` |
| `wechat` | `bot_token`、`ilink_bot_id`、`qrcode_login_enabled`、`polling_timeout`、文件大小限制等 |
| `wecom` | `bot_id`、`bot_secret` |
| `dingtalk` | `client_id`、`client_secret`、`card_template_id` |
| `discord` | `bot_token`、`allowed_guilds`、`mention_only`、`allowed_channels`、`thread_mode` |

> 各 IM 渠道详细接入步骤见 [backend/docs/IM_CHANNEL_CONNECTIONS.md](../backend/docs/IM_CHANNEL_CONNECTIONS.md)。

### `guardrails`

工具调用前授权检查。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 总开关 |
| `fail_closed` | `true` | provider 出错时是否阻断工具调用 |
| `passport` | `null` | OAP passport 路径或托管 agent ID |
| `provider` | `null` | provider 配置：`use` + `config` |

内置 provider：`deerflow.guardrails.builtin:AllowlistProvider`（零依赖）。

> 详细用法见 [backend/docs/GUARDRAILS.md](../backend/docs/GUARDRAILS.md)。

### `circuit_breaker`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `failure_threshold` | `5` | 连续失败多少次后熔断 |
| `recovery_timeout_sec` | `60` | 熔断后多少秒尝试恢复 |

### `auth`

OIDC / SSO 认证。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `oidc.enabled` | `false` | 总开关 |
| `oidc.frontend_base_url` | `null` | 生产环境应设为公开前端 URL |
| `oidc.providers` | `{}` | 各 provider 配置 |

每个 provider：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `display_name` | — | 展示名 |
| `issuer` | — | OIDC issuer URL |
| `client_id` | — | OAuth client ID |
| `client_secret` | — | OAuth client secret，支持 `$VAR` |
| `redirect_uri` | — | 回调地址 |
| `scopes` | `["openid", "email", "profile"]` | 请求 scope |
| `token_endpoint_auth_method` | `client_secret_post` | token endpoint 认证方式 |
| `auto_create_users` | `true` | 首次登录自动创建用户 |
| `require_verified_email` | `true` | 要求邮箱已验证 |
| `allowed_email_domains` | `[]` | 允许邮箱域名白名单 |
| `admin_emails` | `[]` | 自动授予管理员角色的邮箱 |
| `pkce_enabled` | `true` | PKCE S256 |
| `nonce_enabled` | `true` | ID token nonce 校验 |

> OIDC 详细配置见 [backend/docs/SSO.md](../backend/docs/SSO.md)。

---

## `extensions_config.json` 参考

`extensions_config.json` 由 `extensions_config.example.json` 复制而来，运行时可通过 Gateway API 修改。

```json
{
  "mcpInterceptors": ["my_package.mcp.auth:build_auth_interceptor"],
  "mcpServers": {
    "github": {
      "enabled": false,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "$GITHUB_TOKEN" },
      "description": "GitHub MCP server"
    }
  },
  "skills": {}
}
```

### `mcpServers` 字段

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 是否启用该 server |
| `type` | `stdio` | 传输类型：`stdio` / `sse` / `http` |
| `command` | `null` | stdio 模式下启动命令 |
| `args` | `[]` | stdio 模式下命令参数 |
| `env` | `{}` | 注入子进程的环境变量，支持 `$VAR` |
| `url` | `null` | sse/http 模式下 server URL |
| `headers` | `{}` | sse/http 模式下请求头 |
| `oauth` | `null` | sse/http 模式下 OAuth 配置 |
| `description` | `""` | 人类可读描述 |

### `oauth` 字段

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | OAuth 注入开关 |
| `token_url` | — | token 端点 |
| `grant_type` | `client_credentials` | `client_credentials` / `refresh_token` |
| `client_id` / `client_secret` | — | 客户端凭据 |
| `refresh_token` | — | refresh_token grant 用 |
| `scope` / `audience` | — | scope / audience |
| `token_field` | `access_token` | 响应中 token 字段名 |
| `token_type_field` | `token_type` | token 类型字段名 |
| `expires_in_field` | `expires_in` | 过期秒数字段名 |
| `default_token_type` | `Bearer` | 默认 token 类型 |
| `refresh_skew_seconds` | `60` | 过期前多少秒刷新 |
| `extra_token_params` | `{}` | 额外 form 参数 |

### `skills`

记录各 skill 的启用状态：

```json
{
  "skills": {
    "my-skill": { "enabled": true }
  }
}
```

### 运行时更新

- `GET /api/mcp/config` / `PUT /api/mcp/config`：读取/保存 MCP 配置
- `PUT /api/skills/{name}`：更新单个 skill 启用状态
- 修改会写回 `extensions_config.json`，运行时通过 mtime 检测变化

> 更多 MCP 细节见 [backend/docs/MCP_SERVER.md](../backend/docs/MCP_SERVER.md)。

---

## 前端环境变量参考

前端环境变量定义在 `frontend/.env`（由 `frontend/.env.example` 复制）。

| 变量 | 作用域 | 默认值 | 说明 |
|------|--------|--------|------|
| `NEXT_PUBLIC_BACKEND_BASE_URL` | 客户端 | 空（走 nginx） | 浏览器直接访问 Gateway 的地址 |
| `NEXT_PUBLIC_LANGGRAPH_BASE_URL` | 客户端 | 空（走 nginx） | LangGraph SDK 地址 |
| `NEXT_PUBLIC_STATIC_WEBSITE_ONLY` | 客户端 | 空 | 设为 `"true"` 开启静态演示模式（无后端） |
| `DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` | 服务端 | `http://127.0.0.1:8001` | SSR 与 `/api/*` rewrite 用 Gateway 地址 |
| `DEER_FLOW_TRUSTED_ORIGINS` | 服务端 | `http://localhost:3000` | SSR 认证信任来源，逗号分隔 |
| `DEER_FLOW_AUTH_DISABLED` | 服务端 | 空 | 设为 `"1"` 关闭认证（生产环境 `prod`/`production` 会忽略） |
| `DEER_FLOW_ENV` / `ENVIRONMENT` | 服务端 | 空 | 设为 `prod`/`production` 会强制启用认证 |
| `BETTER_AUTH_SECRET` | 服务端 | 空 | 生产环境会话签名密钥 |
| `SKIP_ENV_VALIDATION` | 构建 | 空 | 设为 `"1"` 跳过 Zod 校验（Docker 构建用） |
| `NEXT_CONFIG_BUILD_OUTPUT` | 构建 | 空 | 设为 `standalone` 启用独立构建 |
| `GITHUB_OAUTH_TOKEN` | 服务端 | 空 | landing 页 StarCounter 获取 GitHub star 数 |
| `NODE_ENV` | 服务端 | `development` | Node 运行模式 |

> 新增前端环境变量时，需同步更新 `frontend/src/env.js` 中的 Zod schema。

---

## Docker / 部署环境变量参考

以下变量主要由 `docker-compose.yaml`、`docker-compose-dev.yaml`、`scripts/deploy.sh`、`scripts/serve.sh` 使用。

### 运行时路径

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEER_FLOW_PROJECT_ROOT` | 当前工作目录 | 项目根目录，用于相对路径 |
| `DEER_FLOW_HOME` | `<project_root>/.deer-flow` | 可写运行时数据目录 |
| `DEER_FLOW_CONFIG_PATH` | `<project_root>/config.yaml` | `config.yaml` 路径 |
| `DEER_FLOW_EXTENSIONS_CONFIG_PATH` | `<project_root>/extensions_config.json` | `extensions_config.json` 路径 |
| `DEER_FLOW_SKILLS_PATH` | `<project_root>/skills` | skills 目录 |
| `DEER_FLOW_CHAINS_PATH` | `<project_root>/chains` | chains 目录 |
| `DEER_FLOW_DOCKER_SOCKET` | `/var/run/docker.sock` | Docker socket 路径（仅 DooD 模式） |
| `DEER_FLOW_REPO_ROOT` | — | 宿主机 repo 根目录，DooD 下用于 skills host path |

### IM 渠道内部地址

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEER_FLOW_CHANNELS_LANGGRAPH_URL` | `http://gateway:8001/api` | 渠道访问 Gateway LangGraph API |
| `DEER_FLOW_CHANNELS_GATEWAY_URL` | `http://gateway:8001` | 渠道访问 Gateway REST API |

### DooD 路径转换

| 变量 | 说明 |
|------|------|
| `DEER_FLOW_HOST_BASE_DIR` | 宿主机上 `.deer-flow` 的绝对路径 |
| `DEER_FLOW_HOST_SKILLS_PATH` | 宿主机上 skills 目录绝对路径 |
| `DEER_FLOW_SANDBOX_HOST` | 沙箱容器访问 Gateway 的主机名，例如 `host.docker.internal` |

### Gateway worker

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GATEWAY_WORKERS` | `1` | Gateway uvicorn worker 数；当前多 worker 需要 nginx sticky sessions + 共享 stream bridge，默认保持 1 |

### 生产部署必需

| 变量 | 说明 |
|------|------|
| `BETTER_AUTH_SECRET` | 生产环境下 frontend 会话签名，必须设置 |

### Docker overlay

| overlay | 文件 | 触发条件 | 用途 |
|---------|------|----------|------|
| DooD | `docker/docker-compose.dood.yaml` | `sandbox.use=AioSandboxProvider` 且无 `provisioner_url` | 挂载宿主机 Docker socket |
| CLI auth | `docker/docker-compose.cli-auth.yaml` | 需要完整 `~/.claude` / `~/.codex` 目录时 | 挂载 CLI 配置目录 |

`scripts/deploy.sh` 与 `scripts/docker.sh` 会自动检测 sandbox 模式并追加 DooD overlay。

---

## 安全配置注意事项

### 1. 沙箱模式选择

| 模式 | 推荐场景 | 安全要点 |
|------|----------|----------|
| Local | 本地开发、单用户、可信输入 | 非强隔离；`allow_host_bash` 默认关闭 |
| AIO / DooD | 需要容器隔离的本地/测试环境 | 必须挂载 Docker socket，相当于把宿主机 root 暴露给 Gateway |
| Provisioner / K8s | 生产、多租户、互联网暴露 | 最强隔离，不挂载 Docker socket，推荐 |

> 永远不要向不可信用户暴露开启 `allow_host_bash: true` 或 DooD overlay 的 DeerFlow 实例。

### 2. CLI 凭据最小暴露

| 需求 | 推荐方式 | 暴露范围 |
|------|----------|----------|
| Claude model provider | `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_AUTH_TOKEN` | 无目录暴露 |
| Codex model provider | `CODEX_AUTH_PATH` 指向单个文件 | 单个文件 |
| ACP adapter | adapter 自己的 env API key | 无目录暴露 |
| 必须读取完整 CLI 目录 | `docker-compose.cli-auth.yaml` overlay | 整个 `~/.claude` / `~/.codex` |

### 3. 危险开关默认值

DeerFlow 默认保持以下安全设置：

- `sandbox.allow_host_bash: false`
- `uploads.auto_convert_documents: false`
- `agents_api.enabled: false`
- `skill_evolution.enabled: false`
- `channel_connections.require_bound_identity: true`
- `auth.oidc.providers.*.pkce_enabled: true`
- `auth.oidc.providers.*.nonce_enabled: true`

### 4. 认证

- 本地开发可设置 `DEER_FLOW_AUTH_DISABLED=1` 关闭认证（生产环境 `prod`/`production` 会忽略此变量）。
- 生产环境必须设置 `BETTER_AUTH_SECRET`，并配置 HTTPS 与正确的 `frontend_base_url`。

---

## 常用配置示例

### 示例 1：本地开发最小配置

`.env`：

```bash
OPENAI_API_KEY=sk-...
```

`config.yaml`：

```yaml
models:
  - name: gpt-4
    display_name: GPT-4
    use: langchain_openai:ChatOpenAI
    model: gpt-4
    api_key: $OPENAI_API_KEY
    max_tokens: 4096
    temperature: 0.7
```

### 示例 2：全离线 / Ollama 配置

`.env`：

```bash
# 无需 API key
```

`config.yaml`：

```yaml
models:
  - name: qwen3-local
    display_name: Qwen3 32B
    use: langchain_ollama:ChatOllama
    model: qwen3:32b
    base_url: http://localhost:11434
    num_predict: 8192
    reasoning: true
    supports_thinking: true
```

### 示例 3：AIO Docker sandbox

`config.yaml`：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  image: enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
  replicas: 3
```

`scripts/deploy.sh` 会自动追加 `docker-compose.dood.yaml`。

### 示例 4：Provisioner / K8s sandbox

`config.yaml`：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  provisioner_url: http://provisioner:8002
```

`make docker-start` / `make up` 会自动启动 `provisioner` 服务。

### 示例 5：关闭生产环境文档端点

`.env`：

```bash
GATEWAY_ENABLE_DOCS=false
```

### 示例 6：启用 LangSmith 追踪

`.env`：

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=ls-...
LANGSMITH_PROJECT=deer-flow
```

### 示例 7：启用 Telegram 渠道

`.env`：

```bash
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
```

`config.yaml`：

```yaml
channels:
  langgraph_url: http://gateway:8001/api
  gateway_url: http://gateway:8001
  telegram:
    enabled: true
    bot_token: $TELEGRAM_BOT_TOKEN
    allowed_users: []
```

---

## 故障排查

### 配置不生效

1. 检查 `config_version` 是否落后于 `config.example.yaml`，必要时运行 `make config-upgrade`。
2. 确认修改的字段是否属于 `STARTUP_ONLY_FIELDS`（见上文），如果是，需要重启 Gateway。
3. 确认修改的是项目根目录的 `config.yaml`，而不是 backend 目录下的副本。

### API key 无效

1. 确认 `.env` 已加载（`make dev` / `make up` 会自动加载）。
2. 检查 `config.yaml` 中 `$VAR_NAME` 拼写是否与 `.env` 一致。
3. 如果通过 OpenAI 兼容网关（如 OpenRouter）使用自定义变量名，确保 `api_key` 指向该变量，例如 `api_key: $OPENROUTER_API_KEY`。

### Sandbox 启动失败

1. 确认 Docker 正在运行。
2. 检查端口 `8080`（或自定义 `sandbox.port`）是否被占用。
3. 确认沙箱镜像可拉取。
4. AIO 模式下确认 `docker-compose.dood.yaml` overlay 已加载。
5. Provisioner 模式下确认 K8s 集群与 kubeconfig 可用。

### tiktoken 离线卡住

在气隙/离线环境中，将 `memory.token_counting` 改为 `char`：

```yaml
memory:
  token_counting: char
```

### 前端无法连接后端

1. 默认走 nginx 统一入口，确认 `NEXT_PUBLIC_BACKEND_BASE_URL` 与 `NEXT_PUBLIC_LANGGRAPH_BASE_URL` 保持注释。
2. 如果直接访问 Gateway，确认 `GATEWAY_CORS_ORIGINS` 包含前端来源。
3. SSR 场景下检查 `DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` 是否指向正确地址。

---

## 参考与延伸阅读

- [backend/docs/CONFIGURATION.md](../backend/docs/CONFIGURATION.md) — 后端模型、工具、沙箱深度配置
- [backend/docs/MCP_SERVER.md](../backend/docs/MCP_SERVER.md) — MCP Server 配置
- [backend/docs/IM_CHANNEL_CONNECTIONS.md](../backend/docs/IM_CHANNEL_CONNECTIONS.md) — IM 渠道接入
- [backend/docs/GUARDRAILS.md](../backend/docs/GUARDRAILS.md) — 工具调用 Guardrails
- [backend/docs/SSO.md](../backend/docs/SSO.md) — OIDC / SSO 认证
- [offline-docker-setup.md](./offline-docker-setup.md) — 离线/气隙环境部署
- [SECURITY.md](../SECURITY.md) — 安全策略
- [README_zh.md](../README_zh.md) — 快速开始与高级配置
