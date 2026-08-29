#!/usr/bin/env python
# -*- coding:utf-8 -*-
# @author  : Claude
# @time    : 2026-06-25
# @function: OpenCode Go — ComfyUI 节点，通过 Anthropic Messages API 调用 AI 网关
# @version : v1.0

import json
import logging
import os
import time

import requests

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Config:
    """管理 config.json 中的 API Key 持久化存储。

    UI 中填写的 api_key 优先使用，config.json 作为兜底。
    """

    def __init__(self):
        self.config_path = os.path.join(
            os.path.dirname(os.path.realpath(__file__)), "config.json"
        )
        self.api_key = None
        self.last_request_time = 0
        self.min_request_interval = 1.0
        self.load_config()

    def load_config(self):
        """从 config.json 加载 opencode_api_key。"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    config = json.load(f)
                    self.api_key = config.get('opencode_api_key', '')
            except Exception as e:
                logger.error(f"Error loading config: {e}")
                self.api_key = ''
        else:
            self.save_config()

    def save_config(self):
        """保存 opencode_api_key 到 config.json。"""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        config = {
            'opencode_api_key': self.api_key or ''
        }
        try:
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving config: {e}")


config = Config()


class OpenCodeGo:
    """OpenCode Go 对话节点。

    使用 Anthropic Messages API 兼容格式，支持：
    - 自定义 API 地址和 Key（UI 直接输入）
    - 系统提示词
    - 多模型选择
    - 对话历史管理
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": (
                    "STRING",
                    {"multiline": True, "default": "Hello, who are you?"},
                ),
                "system_prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "You are a helpful assistant.",
                    },
                ),
                "model": (
                    "STRING",
                    {"default": "deepseek-v4-pro"},
                ),
                "api_url": (
                    "STRING",
                    {"default": "https://opencode.ai/zen/go"},
                ),
                "api_key": (
                    "STRING",
                    {"default": ""},
                ),
                "temperature": (
                    "FLOAT",
                    {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.1},
                ),
                "max_tokens": (
                    "INT",
                    {"default": 8192, "min": 1, "max": 100000, "step": 1},
                ),
                "timeout": (
                    "INT",
                    {"default": 120, "min": 10, "max": 600, "step": 10},
                ),
            },
            "optional": {
                "history": ("STRING", {"default": ""}),
                "seed": (
                    "INT",
                    {"default": 0, "min": 0, "max": 2147483647},
                ),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("response", "history")
    FUNCTION = "chat"
    CATEGORY = "ComfyUI/OpenCode Go"

    def chat(
        self,
        prompt,
        system_prompt,
        model,
        api_url,
        api_key,
        temperature,
        max_tokens,
        timeout,
        history,
        seed,
    ):
        """执行对话请求。

        Args:
            prompt: 用户输入文本
            system_prompt: 系统提示词
            model: 模型名称
            api_url: API 基础地址
            api_key: API 认证 token
            temperature: 采样温度
            max_tokens: 最大输出 token 数
            timeout: 请求超时（秒）
            history: JSON 格式的对话历史
            seed: 随机种子（0 表示不使用）

        Returns:
            (response, history): 助手回复和更新后的对话历史
        """
        try:
            # Resolve API key: UI input → config.json → error
            effective_api_key = api_key if api_key else config.api_key
            if not effective_api_key:
                return (
                    "Please provide an API key in the node or add it to config.json",
                    "",
                )

            # Resolve API URL
            effective_api_url = (
                api_url if api_url else "https://opencode.ai/zen/go"
            )

            # Parse history if provided
            messages = []
            if history:
                try:
                    messages = json.loads(history)
                except json.JSONDecodeError:
                    logger.warning(
                        f"Failed to parse history, starting fresh: {history}"
                    )
                    messages = []

            # Add system prompt if starting fresh
            if not messages:
                messages.append({"role": "system", "content": system_prompt})

            # Add user message (Anthropic format requires content array)
            messages.append({
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            })

            # Build request headers (Anthropic Messages API format)
            headers = {
                "x-api-key": effective_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }

            # Build request body
            data = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }

            # Add seed if provided (for reproducible responses)
            if seed > 0:
                data["random_seed"] = seed

            # Rate limiting
            current_time = time.time()
            time_since_last_request = (
                current_time - config.last_request_time
            )
            if time_since_last_request < config.min_request_interval:
                time.sleep(
                    config.min_request_interval - time_since_last_request
                )

            endpoint = f"{effective_api_url.rstrip('/')}/v1/messages"
            logger.info(
                f"Sending request to {endpoint} with model={model}, "
                f"messages_count={len(messages)}, timeout={timeout}s"
            )
            response = requests.post(
                endpoint, headers=headers, json=data, timeout=timeout
            )
            config.last_request_time = time.time()

            if response.status_code == 200:
                result = response.json()
                logger.info(f"API Response received successfully")

                if "content" not in result:
                    return (
                        f"API Error: No content in response: {result}",
                        json.dumps(messages),
                    )

                # Extract assistant response — find text type content,
                # skip thinking blocks
                response_content = ""
                for item in result["content"]:
                    if item.get("type") == "text":
                        response_content = item.get("text", "")
                        break

                if not response_content:
                    return (
                        f"API Error: No text content in response: {result}",
                        json.dumps(messages),
                    )

                # Update history with assistant response
                messages.append(
                    {"role": "assistant", "content": response_content}
                )
                new_history = json.dumps(messages, ensure_ascii=False)

                return (response_content, new_history)
            else:
                try:
                    error_response = response.json()
                    error_message = (
                        f"API Error {response.status_code}: {error_response}"
                    )
                except Exception:
                    error_message = (
                        f"API Error {response.status_code}: {response.text}"
                    )
                logger.error(error_message)
                return (
                    error_message,
                    json.dumps(messages, ensure_ascii=False),
                )

        except requests.exceptions.Timeout:
            error_message = f"API request timed out after {timeout} seconds"
            logger.error(error_message)
            return (error_message, "")
        except Exception as e:
            error_message = f"Error in chat method: {str(e)}"
            logger.exception(error_message)
            return (error_message, "")


# Register the node with ComfyUI
NODE_CLASS_MAPPINGS = {
    "OpenCodeGo": OpenCodeGo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "OpenCodeGo": "OpenCode Go",
}
