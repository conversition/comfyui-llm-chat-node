# OpenCode Go — ComfyUI Plugin

AI 对话节点，通过 Anthropic Messages API 兼容格式连接 OpenCode AI 网关。

## 安装

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/your-org/ComfyUI-OpenCode-Go.git
# 或直接将本目录复制到 custom_nodes/
```

依赖：`requests`（ComfyUI 内置 Python 环境通常已安装）。

## 使用

1. 在 ComfyUI 节点菜单中找到 **ComfyUI/OpenCode Go → OpenCode Go**
2. 填写参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| **prompt** | STRING (multiline) | — | 你的问题/输入 |
| **system_prompt** | STRING (multiline) | — | 系统提示词（角色设定） |
| **model** | STRING | `deepseek-v4-pro` | 模型名称（自由输入，可用模型取决于 API Key 权限） |
| **api_url** | STRING | `https://opencode.ai/zen/go` | API 基础地址 |
| **api_key** | STRING | — | API 认证 token（直接在 UI 填写，也可写入 `config.json`） |
| **temperature** | FLOAT | `0.7` | 采样温度 0.0~2.0 |
| **max_tokens** | INT | `8192` | 最大输出 token 数（推理模型建议 ≥8192） |
| **timeout** | INT | `120` | 请求超时秒数 |
| **history** (可选) | STRING | — | JSON 格式对话历史，支持多轮对话 |
| **seed** (可选) | INT | `0` | 随机种子，0 表示不使用 |

3. 输出：
   - `response` — 助手回复文本
   - `history` — 更新后的 JSON 对话历史（可连接到下一个节点继续对话）

## Key 配置

两种方式任选其一：

1. **UI 直接输入**（推荐）——在节点的 `api_key` 字段填写
2. **config.json** ——编辑 `config.json` 中的 `opencode_api_key` 字段

优先级：UI 输入 > config.json

## 可用模型

模型可用性由 API Key 权限决定，在节点 `model` 字段自由输入。常见模型：

- `deepseek-v4-pro` — DeepSeek V4 Pro（推理模型，需较高 max_tokens）

> 提示：推理模型（如 deepseek-v4-pro）的思考过程会消耗输出 token，建议 `max_tokens` 设置 ≥ 8192 以确保正文正常输出。

## API 格式

```
POST {api_url}/v1/messages
Headers:
  x-api-key: {api_key}
  anthropic-version: 2023-06-01
  Content-Type: application/json
Body:
  {
    "model": "deepseek-v4-pro",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
    ],
    "temperature": 0.7,
    "max_tokens": 8192,
    "stream": false
  }
```

> **注意**：用户消息的 `content` 必须为数组格式 `[{"type":"text","text":"..."}]`，纯字符串会被 API 拒绝。
