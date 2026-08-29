# ComfyUI-LLM-Chat-Node

> 基于 [SuzumiyaAkizuki/ComfyUI-NewBie-LLM-Formatter](https://github.com/SuzumiyaAkizuki/ComfyUI-NewBie-LLM-Formatter) 二次开发的 **ComfyUI LLM 对话节点包**:将自然语言/图片转化为结构化提示词,并新增三合一对话节点与 OpenCode AI 网关接入。

<p align="center">
  <img src="https://img.shields.io/github/license/conversition/comfyui-llm-chat-node" alt="License"/>
  <img src="https://img.shields.io/github/languages/top/conversition/comfyui-llm-chat-node" alt="Top Language"/>
  <img src="https://img.shields.io/github/languages/count/conversition/comfyui-llm-chat-node" alt="Languages"/>
  <img src="https://img.shields.io/github/repo-size/conversition/comfyui-llm-chat-node" alt="Repo Size"/>
  <img src="https://img.shields.io/github/last-commit/conversition/comfyui-llm-chat-node" alt="Last Commit"/>
  <img src="https://img.shields.io/github/stars/conversition/comfyui-llm-chat-node" alt="Stars"/>
</p>

## ✨ 二次开发内容(本次新增/修改)

| 类型 | 内容 | 说明 |
|---|---|---|
| 🆕 新增 | **LLM Direct Chat 节点**(`LLM_Chat_Node.py`) | 三模式合一:Direct 纯聊天 / NewBie XML prompt / Anima Markdown prompt,支持 Agent 模式多轮 MCP 标签搜索 |
| 🆕 新增 | **OpenCode Go 节点**(`ComfyUI-OpenCode-Go/`) | 通过 Anthropic Messages API 兼容格式连接 OpenCode AI 网关,默认 `deepseek-v4-pro` 模型,支持自由配置 api_url/model |
| 🆕 新增 | `_header.txt` | 节点统一头部注释模板 |
| ✏️ 修改 | `LLM_Node.py` | 扩展 XML Prompt Formatter 功能,支撑三合一节点复用 |
| ✏️ 修改 | `prompt_agent/` | 精简为 6 个核心模块(agent_core / agent_prompts / tools / cache / utils),保留 MCP 标签搜索 Agent 能力 |
| ✏️ 修改 | `__init__.py` | 注册 `LLM_Direct_Chat` 节点,加载路径修正 |

**继承上游**(未修改):XML Prompt Formatter / XML Style Injector / Style Saver 节点、`json_editor.html` 前端、`requirements.txt`。

## 🛠 节点清单

| 节点 | 类型 | 用途 |
|---|---|---|
| LLM Xml Prompt Formatter | 继承 | 自然语言 → NewBie XML 结构化提示词 |
| XML Style Injector | 继承 | 样式注入 |
| Style Preset Saver | 继承 | 样式预设保存 |
| **LLM Direct Chat** | 新增 | 三模式对话 + Agent 模式 MCP 标签搜索 |
| **OpenCode Go** | 新增 | OpenCode AI 网关对话(deepseek-v4-pro 等) |

## 📦 安装

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/conversition/comfyui-llm-chat-node.git
# 或直接复制本目录到 custom_nodes/
```

依赖:`requests`、`pydantic`(见 `requirements.txt`)。

## 🚀 使用

1. 在 ComfyUI 节点菜单中找到 `ComfyUI/LLM Chat Node` 分组
2. 配置 API Key 与环境变量(参考上游 NewBie LLM Formatter 配置方式)
3. **LLM Direct Chat**:选择模式(Direct/NewBie/Anima),填写 prompt 与系统提示词即可对话;Agent 模式下可搜索 MCP 标签并组装 prompt
4. **OpenCode Go**:填写 prompt / system_prompt / model / api_url 后运行

## 🔗 上游

- 上游仓库:[SuzumiyaAkizuki/ComfyUI-NewBie-LLM-Formatter](https://github.com/SuzumiyaAkizuki/ComfyUI-NewBie-LLM-Formatter)
- 本仓库仅包含二次开发增量 + 运行必需文件,完整上游代码请参见上游仓库

## 📄 License

MIT(上游作者 SuzumiyaAkizuki 版权所有,见 [LICENSE](LICENSE))
