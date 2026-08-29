#!/usr/bin/env python
# -*- coding:utf-8 -*-
# @author  : sakizuki
# @time    : 2026-06-25
# @function: LLM Direct Chat 节点——三模式合一（Direct / NewBie / Anima）
# @version : v2.0

"""
LLM Direct Chat 节点。

整合 LLM Xml Prompt Formatter 的全部功能 + OpenCode-Go 的对话模式，
提供三合一节点：Direct（纯聊天）、NewBie（XML prompt 生成）、Anima（Markdown prompt 生成），
并支持 Agent 模式的多轮 MCP 标签搜索。
"""

import json
import re
from prompt_agent.agent_core import PromptAgent
from prompt_agent import utils
import comfy.model_management
from .LLM_Node import (
    load_api_config,
    get_platform_settings,
    extract_reasoning,
    _ensure_v1_suffix,
    BColors,
)


class LLM_Direct_Chat:
    """LLM 三模式合一节点。

    mode=Direct:  纯对话 + 历史管理（等效 OpenCode-Go）
    mode=NewBie:  结构化 XML prompt 生成
    mode=Anima:   结构化 Markdown prompt 生成
    agent_effort: Close=普通模式, Low/Med/High=Agent MCP 标签搜索
    """

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
        key_rotation_default = config.get("key_rotation_enabled", True)

        # 模型下拉框
        if model_list and isinstance(model_list, list) and (
            not all("your_model" in model for model in model_list)
        ):
            model_widget = (model_list,)
        else:
            model_widget = (
                "STRING",
                {"multiline": False, "default": "读取模型列表失败，请在此填写模型名称"},
            )

        # API Key 默认值 — 检查 Key 轮换机制（不计入请求计数）
        from .LLM_Node import get_api_key_with_rotation
        rotated_key = get_api_key_with_rotation(config, count_request=False)
        if rotated_key:
            key_default = "已从配置文件中读取api key，在此填写将不生效"
        elif api_key and isinstance(api_key, str) and (api_key != default_api_key):
            key_default = "已从配置文件中读取api key，在此填写将不生效"
        else:
            key_default = "读取API失败，请在此填写api key"

        # API URL 默认值
        if api_url and isinstance(api_url, str) and (api_url != default_api_url):
            url_default = "已从配置文件中读取api url，在此填写将不生效"
        else:
            url_default = "读取API失败，请在此填写api url"

        return {
            "required": {
                "api_key": (
                    "STRING",
                    {"multiline": False, "default": key_default, "dynamicPrompts": False},
                ),
                "api_url": (
                    "STRING",
                    {"multiline": False, "default": url_default, "dynamicPrompts": False},
                ),
                "model_name": model_widget,
                "user_prompt": (
                    "STRING",
                    {"multiline": True, "default": "", "dynamicPrompts": False},
                ),
                "mode": (["NewBie", "Anima", "Direct"],),
                "agent_effort": (["Close", "Low", "Medium", "High"],),
                "thinking": ("BOOLEAN", {"default": False}),
                "temperature": (
                    "FLOAT",
                    {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.1},
                ),
                "max_tokens": (
                    "INT",
                    {"default": 8192, "min": 1, "max": 100000, "step": 1},
                ),
                "key_rotation": (
                    "BOOLEAN",
                    {"default": key_rotation_default,
                     "label_on": "启用轮询",
                     "label_off": "禁用轮询"},
                ),
            },
            "optional": {
                "system_prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "所有模式通用：此处内容将追加到系统提示词末尾（不覆盖默认提示词），用于补充额外要求，如「输出至少70个tag，标签块后写2句简洁英文描述」。留空则仅使用配置文件默认提示词。",
                    },
                ),
                "history": ("STRING", {"default": "[]"}),
                "image": ("IMAGE",),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("response", "history", "debug_log")
    OUTPUT_NODE = True
    FUNCTION = "chat"
    CATEGORY = "NewBie LLM Formatter"


    # ============================================================
    # 凭据解析（所有模式共用）
    # ============================================================

    @staticmethod
    def _resolve_credentials(config, api_key, api_url, key_rotation=None):
        """解析 API 凭据：Key 轮换/配置文件优先，UI 输入作为回退。

        key_rotation=True 启用 Key 轮询，False 禁用，None 则读取配置。
        """
        key_placeholders = [
            "sk-...", "读取API失败，请在此填写api key", "",
            "已从配置文件中读取api key，在此填写将不生效", None,
        ]
        url_placeholders = [
            "https://xxx.ai/api/v1",
            "读取API失败，请在此填写api url", "",
            "已从配置文件中读取api url，在此填写将不生效", None,
        ]

        config_url = config.get("api_url")

        # 优先使用 Key 轮换机制（UI 传入的 key_rotation 覆盖配置文件）
        from .LLM_Node import get_api_key_with_rotation
        if key_rotation is not None:
            config["key_rotation_enabled"] = key_rotation
        rotated_key = get_api_key_with_rotation(config)
        final_key = None
        if rotated_key:
            final_key = rotated_key.replace(" ", "")
            print("[LLM_Direct_Chat]: 已通过 Key 轮换机制获取 API KEY.")
        elif config.get("api_key") and config.get("api_key") not in key_placeholders:
            final_key = config["api_key"].replace(" ", "")
            print("[LLM_Direct_Chat]: 已从配置文件中读取API KEY.")
        elif api_key and api_key not in key_placeholders:
            final_key = api_key.replace(" ", "")
            print(
                f"{BColors.WARNING}[LLM_Direct_Chat]: 已从UI输入中读取API KEY.{BColors.ENDC}"
            )
        else:
            print(
                f"{BColors.FAIL}[LLM_Direct_Chat]: 配置文件和UI输入中均无有效API KEY.{BColors.ENDC}"
            )
            return None, None

        final_url = None
        if config_url and config_url not in url_placeholders:
            final_url = config_url.replace(" ", "")
            print(f"[LLM_Direct_Chat]: 已从配置文件中读取API URL: {final_url}.")
        elif api_url and api_url not in url_placeholders:
            final_url = api_url.replace(" ", "")
            print(f"[LLM_Direct_Chat]: 已从UI输入中读取API URL: {final_url}.")
        else:
            print(
                f"{BColors.FAIL}[LLM_Direct_Chat]: 配置文件和UI输入中均无有效API URL.{BColors.ENDC}"
            )
            return final_key, None

        final_url = _ensure_v1_suffix(final_url)

        return final_key, final_url

    # ============================================================
    # Direct 模式：对话历史
    # ============================================================

    @staticmethod
    def _parse_history(history_str):
        """解析历史 JSON 字符串为消息列表。解析失败时返回 None。"""
        if not history_str or history_str == "[]":
            return []
        try:
            messages = json.loads(history_str)
            if not isinstance(messages, list):
                print(
                    f"{BColors.WARNING}[LLM_Direct_Chat]: 历史格式无效，将开始新对话。{BColors.ENDC}"
                )
                return None
            for msg in messages:
                if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                    print(
                        f"{BColors.WARNING}[LLM_Direct_Chat]: 历史消息格式无效，将开始新对话。{BColors.ENDC}"
                    )
                    return None
            return messages
        except json.JSONDecodeError:
            print(
                f"{BColors.WARNING}[LLM_Direct_Chat]: 历史 JSON 解析失败，将开始新对话。{BColors.ENDC}"
            )
            return None

    @staticmethod
    def _build_direct_messages(history, system_prompt, user_prompt, image):
        """从历史/系统提示词/用户输入构建消息列表（Direct 模式）。
        Returns (messages, text_only_messages)。
        """
        default_system = "You are a helpful assistant."
        messages = list(history) if history else []

        effective_system = system_prompt if system_prompt else default_system
        if messages and messages[0].get("role") == "system":
            if system_prompt:
                messages[0]["content"] = system_prompt
        else:
            messages.insert(0, {"role": "system", "content": effective_system})

        if image is not None:
            print("[LLM_Direct_Chat]: 检测到图片输入，正在转换...")
            base64_image = utils.tensor_to_base64(image)
            user_content = [
                {"type": "text", "text": user_prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                },
            ]
            text_only_content = f"{user_prompt}\n[image]"
        else:
            user_content = user_prompt
            text_only_content = user_prompt

        text_only_messages = [{**msg} for msg in messages]
        text_only_messages.append({"role": "user", "content": text_only_content})
        messages.append({"role": "user", "content": user_content})

        return messages, text_only_messages

    # ============================================================
    # Formatter 模式：提示词生成
    # ============================================================

    @staticmethod
    def _build_formatter_config(config, final_url, model_name, mode):
        """构建 Formatter 模式的提示词配置。
        Returns (system_content, fewshot_user, fewshot_assistant, gemma_prompt, is_anima).
        """
        is_anima = (mode == "Anima")
        if is_anima:
            system_content = config.get(
                "system_prompt_anima",
                "You are a helpful assistant that generates image prompts.",
            )
            fewshot_user = config.get("fewshot_user_anima", "")
            fewshot_assistant = config.get("fewshot_assistant_anima", "")
            artists_anima = config.get("artists_anima", "")
            system_content = f"{system_content}{artists_anima}"
            print("[LLM_Direct_Chat]: 当前模式: Anima")
        else:
            system_content = config.get(
                "system_prompt",
                "You are a helpful assistant that provides prompt tags.",
            )
            fewshot_user = config.get("fewshot_user", "")
            fewshot_assistant = config.get("fewshot_assistant", "")
            print("[LLM_Direct_Chat]: 当前模式: NewBie")

        gemma_prompt = config.get(
            "gemma_prompt",
            "You are an assistant designed to generate high-quality anime images "
            "with the highest degree of image-text alignment based on xml format "
            "textual prompts. <Prompt Start>\n",
        )

        # Gemini jailbreaker
        jailbreaker = config.get("gemini_jailbreaker", "")
        if (
            (not "googleapis" in final_url)
            and ("gemini" in model_name.lower())
            and jailbreaker
        ):
            print("[LLM_Direct_Chat]: 已启用Gemini强力破甲。")
            system_content = f"{jailbreaker}{system_content}"

        return system_content, fewshot_user, fewshot_assistant, gemma_prompt, is_anima

    @staticmethod
    def _build_formatter_messages(
        system_content, fewshot_user, fewshot_assistant,
        user_prompt, image, override_system_prompt,
    ):
        """构建 Formatter 模式的完整消息列表（含图片和可选系统提示词覆盖）。

        注意：override_system_prompt（UI 输入的 system_prompt）会被追加到配置系统提示词
        末尾，而非替换。这样可以同时保留 Anima/NewBie 的格式规则和用户的额外要求。
        """
        if override_system_prompt:
            effective_system = (
                f"{system_content}\n\n"
                f"# 用户附加指令（优先级高于以上所有规则，必须严格遵守）\n"
                f"{override_system_prompt}"
            )
        else:
            effective_system = system_content

        messages_content = [{"type": "text", "text": user_prompt}]
        if image is not None:
            print("[LLM_Direct_Chat]: 检测到图片输入，正在转换...")
            base64_image = utils.tensor_to_base64(image)
            messages_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
            })

        if fewshot_assistant and fewshot_user:
            print("[LLM_Direct_Chat]: 已成功应用few-shot示例。\n")
            return [
                {"role": "system", "content": effective_system},
                {"role": "user", "content": fewshot_user},
                {"role": "assistant", "content": fewshot_assistant},
                {"role": "user", "content": messages_content},
            ]
        return [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": messages_content},
        ]

    @staticmethod
    def _parse_formatter_output(full_response, is_anima, gemma_prompt):
        """解析 Formatter 模式的 LLM 输出。
        Anima: 中英文分离。NewBie: 三级 XML 提取策略。
        """
        if is_anima:
            xml_content, text_content = utils.split_by_language(full_response)
            xml_content = utils.strip_code_fences(xml_content)
            if not xml_content:
                print(
                    f"{BColors.WARNING}[LLM_Direct_Chat]: "
                    f"Anima模式未检测到英文内容，返回完整响应。{BColors.ENDC}"
                )
                xml_content = full_response
            return xml_content, text_content

        # NewBie mode: 严格错误处理
        if "```" not in full_response and "<img>" not in full_response:
            print(
                f"{BColors.FAIL}[LLM_Direct_Chat]: "
                f"大模型的回复中未检测到<img>标签。以下是大模型的回复：\n {full_response} {BColors.ENDC}"
            )
            raise ValueError("LLM API 的回复中未检测到<img>标签。")
        if (
            "```" not in full_response
            and "<img>" in full_response
            and "</img>" not in full_response
        ):
            print(
                f"{BColors.WARNING}[LLM_Direct_Chat]: "
                f"大模型的回复可能被截断。以下是大模型的回复：\n {full_response} {BColors.ENDC}"
            )
            raise ValueError("LLM API 的回复可能被截断。")

        xml_content, text_content = utils.parse_newbie_content(full_response)
        xml_content = utils.clean_prompt(xml_content, gemma_prompt)
        return xml_content, text_content

    # ============================================================
    # API 调用核心（所有模式共用，含 429 自动轮换 Key）
    # ============================================================

    @staticmethod
    def _call_api(
        config, final_key, final_url, model_name, messages, extra_body,
        temperature, max_tokens, unique_id, node_name="LLM_Direct_Chat",
    ):
        """执行 API 调用（含重试 + 429 自动轮换 Key）。"""
        from .LLM_Node import call_llm_with_auto_rotation

        return call_llm_with_auto_rotation(
            config=config,
            api_key=final_key,
            api_url=final_url,
            model_name=model_name,
            messages=messages,
            extra_body=extra_body,
            temperature=temperature,
            max_tokens=max_tokens,
            unique_id=unique_id,
            node_name=node_name,
        )

    # ============================================================
    # 三模式处理入口
    # ============================================================

    def _handle_direct_chat(
        self, config, final_key, final_url, model_name, user_prompt,
        thinking, temperature, max_tokens, system_prompt, history,
        image, unique_id,
    ):
        """Direct 模式：纯聊天 + 对话历史管理。"""
        parsed_history = self._parse_history(history)
        if parsed_history is None:
            parsed_history = []

        try:
            messages, text_only_messages = self._build_direct_messages(
                parsed_history, system_prompt, user_prompt, image
            )
        except Exception as e:
            print(
                f"{BColors.FAIL}[LLM_Direct_Chat]: 图片转换失败: {e}{BColors.ENDC}"
            )
            return f"图片转换失败: {str(e)}", history if history else "[]", ""

        try:
            extra_body = get_platform_settings(final_url, model_name, thinking)
        except Exception as e:
            return f"创建 API 客户端失败: {str(e)}", history if history else "[]", ""

        try:
            response = self._call_api(
                config, final_key, final_url, model_name, messages, extra_body,
                temperature, max_tokens, unique_id,
            )
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as e:
            err_msg = str(e).lower()
            if any(
                kw in err_msg
                for kw in ["api key", "authentication", "401", "unauthorized"]
            ):
                return (
                    f"API 认证错误: {str(e)}",
                    json.dumps(text_only_messages, ensure_ascii=False),
                    "",
                )
            return (
                f"API 调用失败: {str(e)}",
                json.dumps(text_only_messages, ensure_ascii=False),
                "",
            )

        full_response = response.choices[0].message.content
        reasoning_present = (
            hasattr(response.choices[0].message, "reasoning")
            and response.choices[0].message.reasoning
        ) or (
            hasattr(response.choices[0].message, "reasoning_content")
            and response.choices[0].message.reasoning_content
        )
        if full_response is None:
            if not reasoning_present:
                return (
                    "API 返回了空内容。",
                    json.dumps(text_only_messages, ensure_ascii=False),
                    "",
                )
            full_response = ""

        cleaned_response, reasoning = extract_reasoning(
            response, full_response, thinking
        )

        if thinking and reasoning:
            display_response = (
                f"[思考过程]\n{reasoning}\n\n[回复]\n{cleaned_response}"
            )
        else:
            display_response = cleaned_response

        text_only_messages.append(
            {"role": "assistant", "content": cleaned_response}
        )
        new_history = json.dumps(text_only_messages, ensure_ascii=False)

        return display_response, new_history, ""

    def _handle_formatter_mode(
        self, config, final_key, final_url, model_name, user_prompt,
        thinking, temperature, max_tokens, mode, system_prompt,
        image, unique_id,
    ):
        """Formatter 模式：结构化 prompt 生成（NewBie / Anima）。"""
        (
            system_content, fewshot_user, fewshot_assistant,
            gemma_prompt, is_anima,
        ) = self._build_formatter_config(config, final_url, model_name, mode)

        try:
            messages_list = self._build_formatter_messages(
                system_content, fewshot_user, fewshot_assistant,
                user_prompt, image, system_prompt,
            )
        except Exception as e:
            return f"图片转换失败: {str(e)}", "", ""

        try:
            extra_body = get_platform_settings(final_url, model_name, thinking)
        except Exception as e:
            return f"创建 API 客户端失败: {str(e)}", "", ""

        try:
            response = self._call_api(
                config, final_key, final_url, model_name, messages_list, extra_body,
                temperature, max_tokens, unique_id,
            )
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as e:
            return f"API 调用失败: {str(e)}", "", ""

        full_response = response.choices[0].message.content
        reasoning_present = (
            hasattr(response.choices[0].message, "reasoning")
            and response.choices[0].message.reasoning
        ) or (
            hasattr(response.choices[0].message, "reasoning_content")
            and response.choices[0].message.reasoning_content
        )
        if full_response is None:
            if not reasoning_present:
                return "API 返回了空内容（NoneType）。", "", ""
            full_response = ""

        full_response, _reasoning = extract_reasoning(
            response, full_response, thinking
        )

        try:
            xml_out, text_out = self._parse_formatter_output(
                full_response, is_anima, gemma_prompt
            )
            return xml_out, text_out, ""
        except Exception as e:
            print(
                f"{BColors.FAIL}[LLM_Direct_Chat]: 输出解析失败: {e}{BColors.ENDC}"
            )
            return (
                f"输出解析失败: {str(e)}\n\n原始回复:\n{full_response}",
                "",
                "",
            )

    def _handle_agent_mode(
        self, config, final_key, final_url, model_name, user_prompt,
        thinking, mode, agent_effort, image, unique_id,
        temperature=0.7, max_tokens=8192, system_prompt="",
    ):
        """Agent 模式：MCP 标签搜索 + 结构化 prompt 生成。"""
        from prompt_agent.agent_core import (
            enable_log_capture, get_captured_log, disable_log_capture,
        )
        print(
            f"[LLM_Direct_Chat]: Agent 模式已启用 "
            f"(mode={mode}, effort={agent_effort})"
        )
        enable_log_capture()
        try:
            agent = PromptAgent(
                api_key=final_key,
                api_url=final_url,
                model_name=model_name,
                mode=mode,
                thinking=thinking,
                config=config,
                effort=agent_effort,
                unique_id=unique_id,
                custom_prompt=system_prompt,
                custom_temperature=temperature,
                custom_max_tokens=max_tokens,
            )
            result = agent.run(user_prompt, image=image)
            debug_log = get_captured_log()
            if isinstance(result, tuple) and len(result) == 2:
                return result[0], result[1], debug_log
            return result[0], result[1], debug_log
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as e:
            debug_log = get_captured_log()
            print(
                f"{BColors.FAIL}[LLM_Direct_Chat]: Agent 模式失败: {e}，"
                f"回退为普通模式{BColors.ENDC}"
            )
            try:
                    f_ret = self._handle_formatter_mode(
                        config, final_key, final_url, model_name, user_prompt,
                        thinking, temperature, max_tokens, mode,
                        system_prompt, image, unique_id,
                    )
                    return f_ret[0], f_ret[1], debug_log
            except Exception as fallback_e:
                return (
                    f"Agent + 回退均失败: {e} / {fallback_e}",
                    "", debug_log,
                )
        finally:
            disable_log_capture()

    # ============================================================
    # 主入口
    # ============================================================

    def chat(
        self,
        api_key,
        api_url,
        model_name,
        user_prompt,
        mode,
        agent_effort,
        thinking,
        temperature,
        max_tokens,
        key_rotation=True,
        system_prompt="",
        history="[]",
        image=None,
        unique_id=None,
    ):
        """三模式合一入口。

        mode=Direct:  纯聊天（history 管理）
        mode=NewBie:  XML prompt 生成
        mode=Anima:   Markdown prompt 生成
        agent_effort: Close=普通, Low/Med/High=Agent MCP 搜索

        Returns (response, history, debug_log)。
        """
        # ── 凭据解析 ─────────────────────────────────────────────
        config = load_api_config()
        final_key, final_url = self._resolve_credentials(
            config, api_key, api_url, key_rotation=key_rotation,
        )

        if final_key is None or final_url is None:
            return (
                "错误：API KEY 或 API URL 未配置。"
                "请在 LPF_config.json 中配置，或在节点输入中填写。",
                history if history else "[]",
                "",
            )

        # ── 模式路由 ─────────────────────────────────────────────
        if mode == "Direct":
            return self._handle_direct_chat(
                config, final_key, final_url, model_name, user_prompt,
                thinking, temperature, max_tokens, system_prompt,
                history, image, unique_id,
            )
        else:
            # NewBie / Anima 模式
            if agent_effort != "Close":
                return self._handle_agent_mode(
                    config, final_key, final_url, model_name, user_prompt,
                    thinking, mode, agent_effort, image, unique_id,
                    temperature, max_tokens, system_prompt,
                )
            else:
                return self._handle_formatter_mode(
                    config, final_key, final_url, model_name, user_prompt,
                    thinking, temperature, max_tokens, mode,
                    system_prompt, image, unique_id,
                )


# ── ComfyUI 节点注册 ────────────────────────────────────────────────
NODE_CLASS_MAPPINGS = {
    "LLM_Direct_Chat": LLM_Direct_Chat,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LLM_Direct_Chat": "LLM Direct Chat",
}
