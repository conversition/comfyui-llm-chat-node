import os
import re
import json
import time
import threading
from openai import OpenAI
from prompt_agent.agent_core import PromptAgent
from prompt_agent import utils
import comfy.utils
import comfy.model_management


class BColors:
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'

CONFIG_FILENAME = "LPF_config.json"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME)

# ── API Key 轮换轮询（计数40次后轮换）────────────────────────────
_request_counter = 0          # 全局请求计数器
_current_key_index = 0        # 当前使用的 key 索引
_rotation_lock = threading.Lock()
REQUESTS_PER_KEY = 40         # 每个 key 最多使用 40 次后轮换

# 外部 Key 文件路径（默认空 = 禁用）
# 如需多 Key 轮换，可在 LPF_config.json 中设置 "apikey_file" 指向每行一个 key 的文本文件
APIKEY_FILE_PATH = ""


def _ensure_v1_suffix(url: str) -> str:
    """归一化 OpenAI 兼容 base_url：确保以 /v1 结尾。

    例如 https://opencode.ai/zen/go → https://opencode.ai/zen/go/v1，
    已含 /v1 的地址保持不变。空值原样返回。
    """
    if not url:
        return url
    url = str(url).strip().rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url

# Key 文件缓存（避免重复 I/O）
_cached_ext_keys = None
_cached_ext_keys_mtime = 0


def _load_keys_from_file(filepath: str) -> list[str]:
    """从外部文件读取 API Key 列表（每行一个 key），带 mtime 缓存。"""
    global _cached_ext_keys, _cached_ext_keys_mtime

    try:
        if not os.path.exists(filepath):
            print(f"{BColors.WARNING}[KeyRotation]: Key 文件不存在: {filepath}{BColors.ENDC}")
            return []

        current_mtime = os.path.getmtime(filepath)
        if _cached_ext_keys is not None and current_mtime == _cached_ext_keys_mtime:
            return _cached_ext_keys

        with open(filepath, "r", encoding="utf-8") as f:
            keys = []
            for line in f:
                key = line.strip()
                if key and not key.startswith("#"):
                    keys.append(key)
        if keys:
            print(
                f"{BColors.WARNING}[KeyRotation]: 从外部文件加载 {len(keys)} 个 API Key"
                f"（{filepath}）{BColors.ENDC}"
            )
        _cached_ext_keys = keys
        _cached_ext_keys_mtime = current_mtime
        return keys
    except Exception as e:
        print(f"{BColors.FAIL}[KeyRotation]: 读取 Key 文件失败: {e}{BColors.ENDC}")
        return []


def get_api_key_with_rotation(config: dict, count_request: bool = True) -> str:
    """获取 API Key，支持计数40次后轮换轮询。

    Key 来源优先级：
    1. 外部文件 APIKEY_FILE_PATH（每行一个 key）— 主源
    2. config['api_keys']（列表）— 回退源
    3. config['api_key']（单字符串）— 向后兼容

    每 REQUESTS_PER_KEY（40）次请求后自动切换到下一个 key，
    全部用完后回到第一个（循环）。

    可通过配置文件关闭轮询（`"key_rotation_enabled": false`），
    关闭后直接返回 config['api_key'] 的单 Key。

    count_request=False 时只读取当前 Key 不计入请求计数
    （用于模型列表预获取等非 API 调用场景）。

    返回 key 字符串，无可用 key 时返回空字符串。
    """
    global _request_counter, _current_key_index

    # ── 开关：关闭轮询时直接返回单 Key ──────────────────────────
    rotation_enabled = config.get("key_rotation_enabled", True)
    if not rotation_enabled:
        single_key = config.get("api_key", "").strip()
        if not single_key:
            print(f"{BColors.FAIL}[KeyRotation]: Key 轮询已关闭且未配置 api_key{BColors.ENDC}")
            return ""
        print(f"{BColors.WARNING}[KeyRotation]: Key 轮询已关闭，使用单 Key 模式{BColors.ENDC}")
        return single_key

    # ── 轮询模式 ─────────────────────────────────────────────────
    # 读取外部文件（需在配置中显式设置 apikey_file 才启用）
    apikey_file = str(config.get("apikey_file") or APIKEY_FILE_PATH or "").strip()
    ext_keys = _load_keys_from_file(apikey_file) if apikey_file else []

    # 合并 key 来源
    valid_keys = ext_keys[:]
    if not valid_keys:
        cfg_keys = config.get("api_keys")
        if cfg_keys and isinstance(cfg_keys, list):
            valid_keys = [k.strip() for k in cfg_keys if k and k.strip()]
    if not valid_keys:
        single_key = config.get("api_key", "").strip()
        if single_key:
            valid_keys = [single_key]

    if not valid_keys:
        print(f"{BColors.FAIL}[KeyRotation]: 未找到任何可用的 API Key{BColors.ENDC}")
        return ""

    with _rotation_lock:
        # 根据请求计数决定用哪个 key（每 REQUESTS_PER_KEY 次轮换）
        key_index = (_request_counter // REQUESTS_PER_KEY) % len(valid_keys)
        key = valid_keys[key_index]
        if count_request:
            current_req = _request_counter + 1
            _request_counter += 1
            use_in_round = (current_req - 1) % REQUESTS_PER_KEY + 1
            print(
                f"{BColors.WARNING}[KeyRotation]: 使用第 {key_index + 1}/{len(valid_keys)} 个 Key"
                f"（本轮第 {use_in_round}/{REQUESTS_PER_KEY} 次请求，总请求 #{current_req}）"
                f"{BColors.ENDC}"
            )
        else:
            print(
                f"{BColors.WARNING}[KeyRotation]: 使用第 {key_index + 1}/{len(valid_keys)} 个 Key"
                f"（不计入请求计数）{BColors.ENDC}"
            )
    return key


def force_rotate_key(config: dict) -> str:
    """强制轮换到下一个 Key（忽略 40 次计数），用于 429 限流恢复。

    将请求计数器推进到下一个 40-request 块的起点，
    然后返回该块对应的 Key。

    返回新 Key，无可用 Key 时返回空字符串。
    """
    global _request_counter

    with _rotation_lock:
        # 推进到下一个 40-request 块的起点
        _request_counter = ((_request_counter // REQUESTS_PER_KEY) + 1) * REQUESTS_PER_KEY

        # 重新读取有效 keys（需在配置中显式设置 apikey_file 才启用）
        apikey_file = str(config.get("apikey_file") or APIKEY_FILE_PATH or "").strip()
        ext_keys = _load_keys_from_file(apikey_file) if apikey_file else []
        valid_keys = ext_keys[:]
        if not valid_keys:
            cfg_keys = config.get("api_keys")
            if cfg_keys and isinstance(cfg_keys, list):
                valid_keys = [k.strip() for k in cfg_keys if k and k.strip()]
        if not valid_keys:
            single_key = config.get("api_key", "").strip()
            if single_key:
                valid_keys = [single_key]

        if not valid_keys:
            return ""

        key_index = (_request_counter // REQUESTS_PER_KEY) % len(valid_keys)
        key = valid_keys[key_index]
        print(
            f"{BColors.WARNING}[KeyRotation]: 429 触发强制轮换 → "
            f"第 {key_index + 1}/{len(valid_keys)} 个 Key{BColors.ENDC}"
        )
        return key


def call_llm_with_auto_rotation(
    config: dict,
    api_key: str,
    api_url: str,
    model_name: str,
    messages: list,
    extra_body: dict,
    temperature: float,
    max_tokens: int,
    unique_id: str = None,
    node_name: str = "LLM",
):
    """调用 OpenAI Chat API，自动处理 429 限流并轮换 Key。

    收到 429 Too Many Requests 时的行为：
    1. 调用 force_rotate_key() 强制轮换到下一个 Key
    2. 用新 Key 重建 OpenAI 客户端并重试
    3. 最多尝试 min(可用 Key 数, 5) 次

    遇到认证错误时立即抛出，不重试。
    Returns response object。所有 Key 都 429 时抛出异常。
    """
    import time as _time

    # 确定最大尝试次数
    max_attempts = 5
    pbar = comfy.utils.ProgressBar(max_attempts, node_id=unique_id) if unique_id else None

    current_key = api_key
    current_url = _ensure_v1_suffix(api_url)
    last_error = None
    key_rotated = False

    for attempt in range(max_attempts):
        comfy.model_management.throw_exception_if_processing_interrupted()
        if pbar:
            pbar.update_absolute(attempt + 1)

        try:
            client = OpenAI(api_key=current_key, base_url=current_url)
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
            )
            usage = response.usage
            if usage:
                print(
                    f"[{node_name}]: Tokens: {usage.prompt_tokens} input + "
                    f"{usage.completion_tokens} output = {usage.total_tokens} used."
                )
            return response

        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as e:
            last_error = e
            err_str = str(e)
            err_lower = err_str.lower()

            # 认证错误直接抛出
            if any(kw in err_lower for kw in ["api key", "authentication", "401", "unauthorized"]):
                raise

            # 429 Too Many Requests → 强制轮换 Key
            if any(kw in err_str for kw in ["429", "too many requests", "rate limit"]):
                new_key = force_rotate_key(config)
                if new_key and new_key != current_key:
                    current_key = new_key
                    key_rotated = True
                    print(
                        f"{BColors.WARNING}[{node_name}]: 429 触发自动轮换 Key，"
                        f"使用新 Key 重试 (第 {attempt + 1}/{max_attempts} 次)...{BColors.ENDC}"
                    )
                    continue
                else:
                    # 只有一个 Key 的情况，等待抖动后重试
                    wait = 2 ** attempt  # 指数退避
                    print(
                        f"{BColors.WARNING}[{node_name}]: 429 限流（仅 1 个可用 Key），"
                        f"等待 {wait}s 后重试...{BColors.ENDC}"
                    )
                    _time.sleep(wait)
                    continue

            # 其他可重试错误
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                print(
                    f"{BColors.WARNING}[{node_name}]: 网络错误 ({e})，"
                    f"等待 {wait}s 后第 {attempt + 1} 次重试...{BColors.ENDC}"
                )
                _time.sleep(wait)
                continue
            else:
                raise

    raise last_error if last_error else RuntimeError(f"[{node_name}]: API 调用失败: 未知错误")


# ── 模型列表动态获取与缓存 ────────────────────────────────────────
_model_list_cache = None
_model_list_cache_time = 0
MODEL_LIST_CACHE_TTL = 300  # 5 分钟缓存

def fetch_model_list_from_api(api_url: str, api_key: str) -> list | None:
    """从 OpenAI 兼容 API 的 /models 端点获取可用模型列表。

    返回模型 ID 列表（仅包含 chat.completions 模型），失败时返回 None。
    结果按模型 ID 字母排序。
    """
    try:
        client = OpenAI(api_key=api_key, base_url=api_url)
        response = client.models.list()
        models = []
        for model in response.data:
            m_id = model.id
            # 只保留 chat completion 模型（过滤 embedding 等）
            owned_by = getattr(model, "owned_by", "") or ""
            if any(skip in m_id.lower() for skip in ["embedding", "whisper", "tts", "davinci", "babbage"]):
                continue
            models.append(m_id)
        if not models:
            return None
        models.sort()
        print(
            f"{BColors.WARNING}[ModelFetch]: 从 {api_url} 获取到 {len(models)} 个模型{BColors.ENDC}"
        )
        return models
    except Exception as e:
        print(
            f"{BColors.WARNING}[ModelFetch]: 获取模型列表失败: {e}"
            f"（不影响使用，将使用配置文件预设列表）{BColors.ENDC}"
        )
        return None


def refresh_model_list(config: dict) -> list:
    """刷新模型列表：先尝试从 API 获取，失败则回退到配置预设列表。

    结果缓存在模块级变量中，TTL 由 MODEL_LIST_CACHE_TTL 控制。
    """
    global _model_list_cache, _model_list_cache_time

    now = time.time()
    if _model_list_cache is not None and (now - _model_list_cache_time) < MODEL_LIST_CACHE_TTL:
        return _model_list_cache

    api_url = _ensure_v1_suffix(config.get("api_url", "").strip())
    # 模型列表获取不计入请求计数
    api_key = get_api_key_with_rotation(config, count_request=False)

    fetched = None
    if api_url and api_key:
        fetched = fetch_model_list_from_api(api_url, api_key)

    if fetched:
        _model_list_cache = fetched
        _model_list_cache_time = now
        return fetched

    # 回退：使用配置预设列表
    preset = config.get("model_list", [])
    if preset and isinstance(preset, list):
        _model_list_cache = preset
        _model_list_cache_time = now
        return preset

    _model_list_cache = []
    _model_list_cache_time = now
    return []


def get_platform_settings(api_url: str, model_name: str, thinking: bool) -> dict:
    """
    根据 API 平台和思考模式设置，返回 extra_body 参数。
    从 LLM_Prompt_Formatter.get_platform_settings 提取为模块级函数，
    供 Agent 模式和普通模式共用。
    """
    extra_body = {}

    def _is_claude_46_plus(name):
        n = name.lower()
        return ('claude-sonnet-4-6' in n or 'claude-opus-4-6' in n
                or 'sonnet-4.6' in n or 'opus-4.6' in n)

    if 'nvidia' in api_url:
        # NVIDIA NIM / integrate.api.nvidia.com 不支持 extra_body 思考参数
        if thinking:
            print(f"{BColors.WARNING}[LLM_Prompt_Formatter]: NVIDIA 平台暂不支持思考模式参数，已忽略。{BColors.ENDC}")
        # NVIDIA 使用标准 OpenAI 格式，无需 extra_body

    elif 'openrouter' in api_url:
        if thinking:
            extra_body = {"reasoning": {"enabled": True, "exclude": False}}
        else:
            extra_body = {"reasoning": {"enabled": False, "effort": "minimal"}}

    elif 'googleapis' in api_url:
        if not thinking:
            if '3' in model_name or '2.5-pro' in model_name:
                print(f"{BColors.WARNING}[LLM_Prompt_Formatter]: googleapis平台的{model_name}模型无法彻底关闭思考功能。已将思考模式设置为low。{BColors.ENDC}")
                extra_body = {"reasoning_effort": "low"}
            else:
                extra_body = {"reasoning_effort": "none"}

    elif 'xiaomimimo' in api_url or 'moonshot' in api_url or 'deepseek' in api_url:
        if thinking:
            extra_body = {"thinking": {"type": "enabled"}}
        else:
            extra_body = {"thinking": {"type": "disabled"}}

    elif 'anthropic.com' in api_url:
        if thinking:
            if _is_claude_46_plus(model_name):
                extra_body = {"thinking": {"type": "adaptive"}}
            else:
                extra_body = {"thinking": {"type": "enabled", "budget_tokens": 8000}}

    elif 'vercel' in api_url:
        if thinking:
            extra_body = {"reasoning": {"enabled": True, "max_tokens": 8000}}
        else:
            extra_body = {"reasoning": {"enabled": False}}

    elif 'opencode' in api_url:
        # OpenCode 推理模型（deepseek-v4-pro / glm-5.2 等）自带思考过程，
        # 无需 extra_body；思考开关仅作提示。
        if thinking:
            print(f"{BColors.WARNING}[LLM_Prompt_Formatter]: OpenCode 推理模型自带思考过程，无需额外思考参数。{BColors.ENDC}")

    else:
        print(f"{BColors.WARNING}[LLM_Prompt_Formatter]: 思考模式开关暂不支持您使用的API平台。{BColors.ENDC}")

    return extra_body


def load_api_config():
    """加载配置并刷新模型列表（含 API Key 轮换轮询）。"""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # 尝试从 API 动态获取最新模型列表（缓存控制）
            try:
                fetched_models = refresh_model_list(config)
                if fetched_models:
                    config["model_list"] = fetched_models
            except Exception:
                pass  # 不影响主流程

            return config
        except Exception as e:
            print(f"{BColors.FAIL}[LLM_Prompt_Formatter]: Error loading {CONFIG_FILENAME}: {e} {BColors.ENDC}")
    return {}


# split_by_language, clean_prompt, repair_xml_custom \u5df2\u8fc1\u79fb\u81f3 prompt_agent.utils


def extract_reasoning(response, full_response: str, thinking: bool) -> tuple:
    """\u4ece LLM \u54cd\u5e94\u4e2d\u63d0\u53d6\u601d\u8003/\u63a8\u7406\u5185\u5bb9\uff08\u6a21\u5757\u7ea7\u51fd\u6570\uff0c\u4f9b\u591a\u4e2a\u8282\u70b9\u5171\u7528\uff09\u3002

    Returns (full_response, reasoning).
    \u6ce8\uff1a\u4f1a\u4ece full_response \u4e2d\u79fb\u9664 <think> \u6807\u7b7e\u5185\u5bb9\u3002
    """
    reasoning = ""
    found_thinking = False

    # \u65b9\u5f0f 1\uff1areasoning \u5c5e\u6027\uff08\u90e8\u5206\u5e73\u53f0\uff09
    if hasattr(response.choices[0].message, 'reasoning') and response.choices[0].message.reasoning:
        reasoning = response.choices[0].message.reasoning
        found_thinking = True
        print(f"{BColors.WARNING}[LLM_Prompt_Formatter]:\u5927\u6a21\u578b\u5df2\u8fdb\u884c\u6df1\u5ea6\u601d\u8003\uff0c\u4ee5\u4e0b\u662f\u601d\u8003\u5185\u5bb9\uff1a\n {reasoning} {BColors.ENDC}")
    if hasattr(response.choices[0].message, 'reasoning_content') and response.choices[0].message.reasoning_content:
        reasoning = response.choices[0].message.reasoning_content
        found_thinking = True
        print(f"{BColors.WARNING}[LLM_Prompt_Formatter]:\u5927\u6a21\u578b\u5df2\u8fdb\u884c\u6df1\u5ea6\u601d\u8003\uff0c\u4ee5\u4e0b\u662f\u601d\u8003\u5185\u5bb9\uff1a\n {reasoning} {BColors.ENDC}")

    # \u65b9\u5f0f 2\uff1a<think> \u6807\u7b7e\uff08DeepSeek R1 \u7b49\uff09
    match = re.search(r'<think>(.*?)</think>', full_response, re.DOTALL)
    if match:
        found_thinking = True
        reasoning = match.group(1)
        print(f"{BColors.WARNING}[LLM_Prompt_Formatter]:\u5927\u6a21\u578b\u5df2\u8fdb\u884c\u6df1\u5ea6\u601d\u8003\uff0c\u4ee5\u4e0b\u662f\u601d\u8003\u5185\u5bb9\uff1a\n {reasoning} {BColors.ENDC}")
        full_response = re.sub(r'<think>(.*?)</think>', "", full_response, flags=re.DOTALL).strip()

    if thinking and not found_thinking:
        print(f"{BColors.WARNING}[LLM_Prompt_Formatter]:\u867d\u7136\u60a8\u5f00\u542f\u4e86\u601d\u8003\u5f00\u5173\uff0c\u4f46\u662f\u672a\u89e3\u6790\u5230\u601d\u8003\u5185\u5bb9\u3002{BColors.ENDC}")
    if (not full_response) and reasoning:
        print(f"{BColors.WARNING}[LLM_Prompt_Formatter]:\u6a21\u578b\u672a\u8fd4\u56de\u7ed3\u679c\u4f46\u68c0\u6d4b\u5230\u601d\u8003\u5185\u5bb9\uff0c\u4ee5\u601d\u8003\u5185\u5bb9\u4f5c\u4e3a\u7ed3\u679c\u3002{BColors.ENDC}")
        full_response = reasoning

    return full_response, reasoning



class LLM_Prompt_Formatter:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        config = load_api_config()
        model_list = config.get("model_list", [])
        api_key = config.get("api_key")
        api_url = config.get("api_url")
        default_api_key = "sk-..."
        default_api_url = "https://integrate.api.nvidia.com/v1"
        default_user_text = "1girl, holding a sword"
        key_rotation_default = config.get("key_rotation_enabled", True)

        AllReadSuccess = True
        if model_list and isinstance(model_list, list) and (not all("your_model" in model for model in model_list)):
            model_widget = (model_list,)
        else:
            model_widget = ("STRING", {"multiline": False, "default": "读取模型列表失败，请在此填写模型名称"})
            AllReadSuccess = False

        # API Key 检查：配置文件中的 api_keys / api_key / 外部文件均可（不计入请求计数）
        rotated_key = get_api_key_with_rotation(config, count_request=False)
        if rotated_key:
            key_default = "已从配置文件中读取api key，在此填写将不生效"
        elif api_key and isinstance(api_key, str) and (not api_key == default_api_key):
            key_default = "已从配置文件中读取api key，在此填写将不生效"
        else:
            key_default = "读取API失败，请在此填写api key"
            AllReadSuccess = False

        if api_url and isinstance(api_url, str) and (not api_url == default_api_url):
            url_default = "已从配置文件中读取api url，在此填写将不生效"
        else:
            url_default = "读取API失败，请在此填写api url"
            AllReadSuccess = False

        if not AllReadSuccess:
            default_user_text = "1girl, holding a sword\n[警告]：读取API失败，请检查配置文件。你可以在节点输入相关信息。请注意，你的API会在原图中保存，分享原图可能会导致API泄露。强烈建议使用配置文件，完成配置后按F5刷新页面并重新创建此节点。"
            print(
                f"{BColors.WARNING}[LLM_Prompt_Formatter]: 读取API失败，请检查配置文件。你可以在节点输入相关信息。请注意，你的API会在原图中保存，分享原图可能会导致API泄露。强烈建议使用配置文件，完成配置后按F5刷新页面并重新创建此节点。{BColors.ENDC}")

        return {
            "required": {
                "api_key": ("STRING", {"multiline": False, "default": key_default, "dynamicPrompts": False}),
                "api_url": ("STRING", {"multiline": False, "default": url_default, "dynamicPrompts": False}),
                "model_name": model_widget,
                "user_text": ("STRING",
                              {"multiline": True, "default": default_user_text, "dynamicPrompts": False}),
                "thinking": ("BOOLEAN", {"default": False}),
                "mode": (["NewBie", "Anima"],),
                "agent_effort": (["Close", "Low", "Medium", "High"],),
                "key_rotation": (
                    "BOOLEAN",
                    {"default": key_rotation_default,
                     "label_on": "启用轮询",
                     "label_off": "禁用轮询"},
                ),
            },
            "optional": {
                "image": ("IMAGE",),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("xml_out", "text_out")
    OUTPUT_NODE = True
    FUNCTION = "process_text"
    CATEGORY = "NewBie LLM Formatter"

    def get_platform_settings(self, api_url, model_name, thinking):
        return get_platform_settings(api_url, model_name, thinking)

    # ── 辅助方法（从 process_text 拆分）─────────────────────────────────

    @staticmethod
    def _resolve_credentials(config, api_key, api_url, key_rotation=None):
        """解析 API 凭据：配置文件/轮询 Key 优先，UI 输入作为回退。
        Returns (final_key, final_url). 缺失时抛出 RuntimeError。

        key_rotation=True 启用 Key 轮询，False 禁用，None 则读取配置。
        """
        key_placeholders = ["sk-...", "读取API失败，请在此填写api key", "", "已从配置文件中读取api key，在此填写将不生效", None]
        url_placeholders = [
            "https://xxx.ai/api/v1",
            "读取API失败，请在此填写api url", "",
            "已从配置文件中读取api url，在此填写将不生效", None,
        ]

        # 优先使用 Key 轮换机制（UI 传入的 key_rotation 覆盖配置文件）
        if key_rotation is not None:
            config["key_rotation_enabled"] = key_rotation
        rotated_key = get_api_key_with_rotation(config)
        if rotated_key:
            final_key = rotated_key.replace(" ", "")
            print(f"[LLM_Prompt_Formatter]: 已通过 Key 轮换机制获取 API KEY.")
        elif api_key and api_key not in key_placeholders:
            final_key = api_key.replace(" ", "")
            print(f"{BColors.WARNING}[LLM_Prompt_Formatter]: 已从UI输入中读取API KEY.{BColors.ENDC}")
        else:
            print(f"{BColors.FAIL}[LLM_Prompt_Formatter]: 配置文件和UI输入中均无有效API KEY.{BColors.ENDC}")
            raise RuntimeError(f"LLM_Prompt_Formatter failed: API KEY 缺失！请在 LPF_config.json 中配置")

        config_url = config.get("api_url")
        if config_url and config_url not in url_placeholders:
            final_url = config_url.replace(" ", "")
            print(f"[LLM_Prompt_Formatter]: 已从配置文件中读取API URL: {final_url}.")
        elif api_url and api_url not in url_placeholders:
            final_url = api_url.replace(" ", "")
            print(f"[LLM_Prompt_Formatter]: 已从UI输入中读取API URL: {final_url}.")
        else:
            print(f"{BColors.FAIL}[LLM_Prompt_Formatter]: 配置文件和UI输入中均无有效API URL.{BColors.ENDC}")
            raise RuntimeError(f"LLM_Prompt_Formatter failed: API URL 缺失！请在 LPF_config.json 中配置")

        final_url = _ensure_v1_suffix(final_url)

        return final_key, final_url

    @staticmethod
    def _build_normal_config(mode, config, api_url, model_name):
        """构建普通模式（非 Agent）的提示词配置。
        Returns (system_content, fewshot_user, fewshot_assistant, gemma_prompt, is_anima).
        """
        is_anima = (mode == "Anima")
        if is_anima:
            system_content = config.get("system_prompt_anima", "You are a helpful assistant that generates image prompts.")
            fewshot_user = config.get("fewshot_user_anima", "")
            fewshot_assistant = config.get("fewshot_assistant_anima", "")
            artists_anima = config.get("artists_anima", "")
            system_content = f"{system_content}{artists_anima}"
            print(f"[LLM_Prompt_Formatter]: 当前模式: Anima")
        else:
            system_content = config.get("system_prompt", "You are a helpful assistant that provides prompt tags.")
            fewshot_user = config.get("fewshot_user", "")
            fewshot_assistant = config.get("fewshot_assistant", "")
            print(f"[LLM_Prompt_Formatter]: 当前模式: NewBie")

        gemma_prompt = config.get("gemma_prompt", "You are an assistant designed to generate high-quality anime images with the highest degree of image-text alignment based on xml format textual prompts. <Prompt Start>\n")

        # Gemini 强力破甲
        jailbreaker = config.get("gemini_jailbreaker", "")
        if (not 'googleapis' in api_url) and ('gemini' in model_name.lower()) and jailbreaker:
            print(f"[LLM_Prompt_Formatter]: 已启用Gemini强力破甲。")
            system_content = f"{jailbreaker}{system_content}"

        return system_content, fewshot_user, fewshot_assistant, gemma_prompt, is_anima

    @staticmethod
    def _build_normal_messages(system_content, fewshot_user, fewshot_assistant, user_text, image):
        """构建普通模式的完整消息列表（含图片）。"""
        messages_content = [{"type": "text", "text": user_text}]
        if image is not None:
            print(f"[LLM_Prompt_Formatter]: 检测到图片输入，正在转换...")
            base64_image = utils.tensor_to_base64(image)
            messages_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            })
        if fewshot_assistant and fewshot_user:
            print("[LLM_Prompt_Formatter]: 已成功应用用户few-shot设置。\n")
            return [
                {"role": "system", "content": system_content},
                {"role": "user", "content": fewshot_user},
                {"role": "assistant", "content": fewshot_assistant},
                {"role": "user", "content": messages_content},
            ]
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": messages_content},
        ]

    @staticmethod
    def _extract_reasoning(response, full_response, thinking):
        """从 LLM 响应中提取思考/推理内容（委托模块级函数）。
        Returns (full_response, reasoning).
        """
        return extract_reasoning(response, full_response, thinking)

    @staticmethod
    def _parse_normal_output(full_response, is_anima, gemma_prompt):
        """解析普通模式的 LLM 输出。
        Anima: 中英文分离。NewBie: 三级 XML 提取策略。
        """
        if is_anima:
            xml_content, text_content = utils.split_by_language(full_response)
            xml_content = utils.strip_code_fences(xml_content)
            if not xml_content:
                print(f"{BColors.WARNING}[LLM_Prompt_Formatter]: Anima模式未检测到英文内容，返回完整响应。{BColors.ENDC}")
                xml_content = full_response
            return xml_content, text_content

        # NewBie mode: 严格错误处理
        if "```" not in full_response and "<img>" not in full_response:
            print(f"{BColors.FAIL}[LLM_Prompt_Formatter]: 大模型的回复中未检测到<img>标签。以下是大模型的回复：\n {full_response} {BColors.ENDC}")
            raise ValueError("LLM API 的回复中未检测到<img>标签。")
        if "```" not in full_response and "<img>" in full_response and "</img>" not in full_response:
            print(f"{BColors.WARNING}[LLM_Prompt_Formatter]: 大模型的回复可能被截断。以下是大模型的回复：\n {full_response} {BColors.ENDC}")
            raise ValueError("LLM API 的回复可能被截断。")

        xml_content, text_content = utils.parse_newbie_content(full_response)
        xml_content = utils.clean_prompt(xml_content, gemma_prompt)
        return xml_content, text_content

    # ── 主方法 ─────────────────────────────────────────────────────────

    def process_text(self, api_key, api_url, model_name, mode, user_text, thinking, agent_effort, key_rotation=True, image=None, unique_id=None):
        config = load_api_config()
        final_key, final_url = self._resolve_credentials(config, api_key, api_url, key_rotation=key_rotation)

        # ── Agent 模式分支 ───────────────────────────────────────────
        if agent_effort != "Close":
            print(f"[LLM_Prompt_Formatter]: Agent 模式已启用 (effort={agent_effort})")
            try:
                agent = PromptAgent(
                    api_key=final_key, api_url=final_url, model_name=model_name,
                    mode=mode, thinking=thinking, config=config, effort=agent_effort,
                    unique_id=unique_id,
                )
                return agent.run(user_text, image=image)
            except comfy.model_management.InterruptProcessingException:
                raise
            except Exception as e:
                print(f"{BColors.FAIL}[LLM_Prompt_Formatter]: Agent 模式失败: {e}，回退为普通模式{BColors.ENDC}")

        # ── 普通模式 ─────────────────────────────────────────────────
        system_content, fewshot_user, fewshot_assistant, gemma_prompt, is_anima = \
            self._build_normal_config(mode, config, final_url, model_name)

        try:
            if not final_key or final_key == "sk-...":
                print(f"{BColors.FAIL}[LLM_Prompt_Formatter]: API KEY 缺失！请在 LPF_config.json 中配置。{BColors.ENDC}")
                raise RuntimeError(f"LLM_Prompt_Formatter failed: API KEY 缺失！请在 LPF_config.json 中配置")

            messages_list = self._build_normal_messages(
                system_content, fewshot_user, fewshot_assistant, user_text, image
            )

            extra_body = self.get_platform_settings(final_url, model_name, thinking)

            # 使用共享的 429 自动轮换 API 调用函数
            response = call_llm_with_auto_rotation(
                config=config,
                api_key=final_key,
                api_url=final_url,
                model_name=model_name,
                messages=messages_list,
                extra_body=extra_body,
                temperature=0.7,
                max_tokens=16384,
                unique_id=unique_id,
                node_name="LLM_Prompt_Formatter",
            )
            full_response = response.choices[0].message.content
            reasoning_present = (
                hasattr(response.choices[0].message, 'reasoning') and response.choices[0].message.reasoning
            ) or (
                hasattr(response.choices[0].message, 'reasoning_content') and response.choices[0].message.reasoning_content
            )
            if full_response is None:
                if not reasoning_present:
                    raise ValueError("LLM API 返回了 NoneType (返回内容为空)。")
                full_response = ""

            # 提取思考/推理内容
            full_response, _reasoning = self._extract_reasoning(response, full_response, thinking)

            # 解析输出
            return self._parse_normal_output(full_response, is_anima, gemma_prompt)

        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as e:
            print(f"{BColors.FAIL}[LLM_Prompt_Formatter]: {str(e)}, 请确认 API 配置是否正确。{BColors.ENDC}")
            raise RuntimeError(f"LLM_Prompt_Formatter failed: {str(e)}") from e


# 以下函数已迁移至 prompt_agent.utils:
#   - split_by_language
#   - clean_prompt
#   - repair_xml_custom
