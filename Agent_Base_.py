import json
import os
import platform
import requests
import re
import sys
import uuid
import time
from abc import ABC, abstractmethod
from bs4 import BeautifulSoup
import threading

try:
    from .logging_config import logger  # 配置日志
    from .agent_tool import tool_registry
except:
    raise ImportError("不可单独执行")


class Agent():
    """
    所有具体 dumplingsAI 必须实现四个属性：
        api_key
        api_provider
        model_name
        prompt
    """
    prompt = None
    api_provider = None
    model_name = None
    api_key = None
    fc_model = True # 现在xml工具调用改为下位支持，如有bug修复优先级降低
    stream = True
    description = None
    # ---------------- 通用构造 ----------------
    def __init__(self,new_load=True):
        self.uuid=self.__class__.uuid
        self.name=self.__class__.name
        self.stream_run=False
        self.current_task_id = None  # 当前任务 ID
        self.tool_call_hooks = []     # 工具调用钩子列表
        agent_name = getattr(self.__class__, 'name', None) or getattr(self.__class__, '__name__', None)
        if agent_name and self.uuid:
            tool_registry.register_agent_uuid(self.uuid, agent_name)

        # 获取该 agent 有权限的所有工具信息
        tools_info = tool_registry.get_all_tools_info(self.uuid)
        tools_prompt = ""
        tools_list = []

        # 添加注册的工具
        if tools_info:
            tools_list.extend(tools_info.keys())

        # 添加内置工具方法（如果存在且可调用）
        builtin_tools = {
            'ask_for_help': '请求其他Agent帮助，参数: agent_id(目标Agent的UUID或名称), message(请求内容)',
            'list_agents': '列出所有可用的Agent及其UUID和名称',
            'attempt_completion': '标记任务完成并退出，参数: report_content(汇报内容，可选)',
            'reload': '重新加载你自己实现重新获取你可以使用的工具，你的新提示词'
        }

        for tool_name, tool_desc in builtin_tools.items():
            if hasattr(self, tool_name) and callable(getattr(self, tool_name)):
                tools_list.append(tool_name)

        # 生成工具提示
        if tools_list:
            tools_prompt = "\n\n你可以使用以下工具：\n"
            # 注册的工具
            for tool_name, tool_info in tools_info.items():
                tools_prompt += f"- {tool_name}: {tool_info['description']}\n"
            # 内置工具
            for tool_name, tool_desc in builtin_tools.items():
                if tool_name in tools_list and tool_name not in tools_info:
                    tools_prompt += f"- {tool_name}: {tool_desc}\n"
            if not self.fc_model:
                tools_prompt += "在使用xml格式的工具时应采用（无参数调用）<工具名></工具名>（含参数调用）<工具名><参数1>放入你想传入的内容</参数1>...</工具名>"

        # 注入 Skills 信息
        try:
            from .skill import skill_registry
            tools_prompt += skill_registry.get_skills_prompt_text(self.uuid)
        except ImportError:
            pass


        prompt = self.prompt + tools_prompt + ", 你的uuid " + str(self.uuid)
        logger.debug(f"Agent {self.name} 初始化，系统提示词长度：{len(prompt)}")
        # print(prompt)
        if new_load:
            self.history = [{"role": "system", "content": prompt}]
        else:
            self.history[0] = {"role": "system", "content": prompt}
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.os_name = platform.system()
        self.conversations_folder = os.getcwd()
        if self.os_name == "Windows":
            self.os_main_folder = os.getenv("USERPROFILE")
        elif self.os_name == "Linux":
            self.os_main_folder = os.path.expanduser("~")
        elif self.os_name == "Darwin":
            self.os_main_folder = os.getenv("HOME")

        threading.Thread(target=self.Connectivity, daemon=True).start()

    # ---------------- 辅助方法 ----------------
    def _generate_task_id(self):
        """生成唯一任务 ID"""
        return str(uuid.uuid4())

    def _get_timestamp(self):
        """获取当前时间戳（毫秒级）"""
        return int(time.time() * 1000)

    def register_tool_hook(self, hook_func):
        """
        注册工具调用钩子
        hook_func 签名：hook_func(event_type, tool_name, tool_args, tool_result, task_id)
        event_type: 'before' | 'after' | 'error'
        """
        self.tool_call_hooks.append(hook_func)

    def _execute_hooks(self, event_type, tool_name, tool_args, tool_result=None):
        """执行所有注册的钩子"""
        for hook in self.tool_call_hooks:
            try:
                hook(
                    event_type=event_type,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=tool_result,
                    task_id=self.current_task_id
                )
            except Exception as e:
                logger.error(f"钩子执行失败：{e}")

    # ---------------- 连通性测试 ----------------
    def Connectivity(self):
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": "你好"}],
            "stream": self.stream,
            "stream_options": {"include_usage": True},
            "max_tokens": 1
        }
        rsp = requests.post(self.api_provider,
                            headers=self.headers,
                            json=payload)
        if rsp.status_code == 200:
            logger.info(f"{self.name} 连接正常")
        else:
            logger.error(f"{self.name} 连接测试未通过，可能存在配置错误")
        return rsp.status_code == 200

    # ---------------- 主对话函数 ----------------
    def conversation_with_tool(self, messages=None, tool=False, images=None):
        """
        进行对话，支持多模态输入（文本 + 图片）

        Args:
            messages: 文本消息
            tool: 是否是工具调用后的继续对话
            images: 图片列表，可以是 base64 字符串或图片 URL
        """
        work_history = self.history

        if messages:
            # 如果有图片，构建多模态内容
            if images:
                content_list = [{"type": "text", "text": messages}]
                for img in images:
                    if img.startswith("http"):
                        content_list.append({
                            "type": "image_url",
                            "image_url": {"url": img}
                        })
                    else:
                        # 假设是 base64
                        content_list.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img}"}
                        })
                work_history.append({"role": "user", "content": content_list})
            else:
                work_history.append({"role": "user", "content": messages})

        if self.fc_model:
            # Function Calling 模式
            tools_schema = tool_registry.get_all_tools_schema(self.uuid)

            # 添加内置工具到 schema
            builtin_tools_schema = [
                {
                    "type": "function",
                    "function": {
                        "name": "ask_for_help",
                        "description": "请求其他Agent帮助",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "agent_id": {"type": "string", "description": "目标Agent的UUID或名称"},
                                "message": {"type": "string", "description": "请求内容"}
                            },
                            "required": ["agent_id", "message"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_agents",
                        "description": "列出所有可用的Agent及其UUID和名称",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "attempt_completion",
                        "description": "标记任务完成并退出",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "report_content": {"type": "string", "description": "汇报内容（可选）"}
                            },
                            "required": []
                        }
                    }
                }
            ]

            # 合并内置工具和注册工具的schema
            for builtin_tool in builtin_tools_schema:
                if hasattr(self, builtin_tool['function']['name']) and callable(getattr(self, builtin_tool['function']['name'])):
                    tools_schema.append(builtin_tool)

            # 添加 Skills 到 Function Calling schema
            try:
                from .skill import skill_registry
                tools_schema.extend(skill_registry.get_all_tool_schemas())
            except ImportError:
                pass

            payload = {
                "model": self.model_name,
                "messages": work_history,
                "stream": self.stream,
                "tools": tools_schema,
                "tool_choice": "auto"
            }
            if self.stream:
                payload["stream_options"] = {"include_usage": True},
        else:
            # XML 模式（原有逻辑）
            payload = {
                "model": self.model_name,
                "messages": work_history,
                "stream": self.stream,
                "stream_options": {"include_usage": True}
            }
            if self.stream:
                payload["stream_options"] = {"include_usage": True},

        rsp = requests.post(
            self.api_provider,
            headers={**self.headers,
                     "Accept-Charset": "utf-8",
                     "Accept": "text/event-stream"},
            json=payload,
            stream=self.stream
        )
        rsp.encoding = 'utf-8'

        full_content = ""
        self.stream_run = True
        tool_calls_list = []  # 存储所有 tool_calls

        if self.stream:
            # 流式响应处理
            for line in rsp.iter_lines(decode_unicode=True):
                logger.trace(line)
                if not line or not line.startswith('data: '):
                    continue
                data = line[6:]
                if data == '[DONE]':
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                delta = (chunk.get('choices') or [{}])[0].get('delta') or {}

                # Function Calling: 处理 tool_calls
                if self.fc_model:
                    tool_calls = delta.get('tool_calls')
                    if tool_calls:
                        for call in tool_calls:
                            # 初始化或更新 tool_calls_list
                            if call.get('index') is not None:
                                idx = call['index']
                                if idx >= len(tool_calls_list):
                                    tool_calls_list.append({
                                        'id': call.get('id'),
                                        'function': {
                                            'name': call['function']['name'],
                                            'arguments': call['function'].get('arguments') or ''
                                        }
                                    })
                                else:
                                    if 'arguments' in call['function'] and call['function']['arguments'] is not None:
                                        tool_calls_list[idx]['function']['arguments'] += call['function']['arguments']
                            continue

                # 普通 content
                content = delta.get('content', '')
                if content:
                    full_content += content
                    self.pack(content, finish_task=False)

                usage = chunk.get('usage')
                if usage:
                    self.stream_run = False
                    self.pack(finish_task=True)
                    self.pack(f"\n本次请求用量：提示 {usage['prompt_tokens']} tokens，"
                          f"生成 {usage['completion_tokens']} tokens，"
                          f"总计 {usage['total_tokens']} tokens。", other=True)
        else:
            # 非流式响应处理
            self.stream_run = False
            try:
                response_json = rsp.json()
                logger.trace(response_json)
                message = response_json['choices'][0]['message']
                full_content = message.get('content', '')

                # Function Calling: 处理 tool_calls
                if self.fc_model:
                    tool_calls_list = message.get('tool_calls', [])

                if full_content:
                    self.pack(full_content, finish_task=False)
                usage = response_json.get('usage', {})
                if usage:
                    self.pack(finish_task=True)
                    self.pack(f"\n本次请求用量：提示 {usage['prompt_tokens']} tokens，"
                          f"生成 {usage['completion_tokens']} tokens，"
                          f"总计 {usage['total_tokens']} tokens。", other=True)
            except Exception as e:
                logger.error(f"非流式响应处理错误: {e}")
                full_content = rsp.text

        logger.trace(f"AI 回复内容长度：{len(full_content)}")

        # Function Calling: 执行工具调用
        if self.fc_model and tool_calls_list:
            logger.debug(f"发现 Function Calling 工具调用: {tool_calls_list}")

            # 添加 assistant message with tool_calls
            work_history.append({
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls_list
            })

            tool_results = []
            for tool_call in tool_calls_list:
                tool_name = tool_call['function']['name']
                tool_id = tool_call['id']

                # 查找工具
                tool_func = None
                if tool_registry.check_permission(self.uuid, tool_name):
                    tool_info = tool_registry.get_tool_info(tool_name)
                    if tool_info is not None:
                        tool_func = tool_info['function']

                if tool_func is None and hasattr(self, tool_name):
                    method = getattr(self, tool_name)
                    if callable(method):
                        tool_func = method

                if tool_func is None:
                    error_msg = f"找不到工具 '{tool_name}'"
                    logger.warning(error_msg)
                    tool_results.append({
                        'tool_call_id': tool_id,
                        'name': tool_name,
                        'content': error_msg
                    })
                    continue

                # 调用工具（解析 arguments 为 dict）
                try:
                    args = json.loads(tool_call['function']['arguments'])
                    # 生成任务 ID
                    self.current_task_id = self._generate_task_id()
                    # 执行 before 钩子
                    self._execute_hooks('before', tool_name, args)
                    logger.debug(f"调用工具 {tool_name}，参数: {args}")
                    self.pack(tool_name=tool_name, tool_parameter=args)
                    result = tool_func(**args)

                    # 执行 after 钩子
                    self._execute_hooks('after', tool_name, args, result)
                    # 打包工具返回值
                    self.pack(tool_result=result, tool_name=tool_name)
                    tool_results.append({
                        'tool_call_id': tool_id,
                        'name': tool_name,
                        'content': result
                    })
                except Exception as e:
                    error_msg = f"执行工具 {tool_name} 时出错: {str(e)}"
                    logger.error(error_msg)

                    # 执行 error 钩子（如果 args 已定义）
                    if 'args' in locals():
                        self._execute_hooks('error', tool_name, args, error_msg)
                    tool_results.append({
                        'tool_call_id': tool_id,
                        'name': tool_name,
                        'content': error_msg
                    })

            # 添加 tool responses 到历史
            for result in tool_results:
                work_history.append({
                    "role": "tool",
                    "tool_call_id": result['tool_call_id'],
                    "name": result['name'],
                    "content": result['content']
                })

            # 继续对话
            logger.debug("工具执行完成，继续对话")
            return self.conversation_with_tool(tool=True)

        # XML 模式：提取并执行工具
        xml_pattern = re.compile(r'<(\w+)>.*?</\1>', flags=re.S)
        clean_pattern = re.compile(r'</?(out_text|thinking)>', flags=re.S)
        clean_content = clean_pattern.sub('', full_content)
        xml_blocks = [m.group(0) for m in xml_pattern.finditer(clean_content)]

        tool_results = []
        tool_names = []
        for block in xml_blocks:
            logger.debug(f"发现工具块：{block[:50]}...")
            soup = BeautifulSoup(block, "xml")
            root = soup.find()
            if root is None:
                raise ValueError("空 XML")
            tool_name = root.name

            # 优先级查找工具：1. 工具注册器 2. 类方法 3. 返回无工具
            tool_func = None
            tool_source = None

            # 优先级1: 在工具注册器中查找
            if tool_registry.check_permission(self.uuid, tool_name):
                tool_info = tool_registry.get_tool_info(tool_name)
                if tool_info is not None:
                    tool_func = tool_info['function']
                    tool_source = "工具注册器"

            # 优先级2: 在类方法中查找
            if tool_func is None and hasattr(self, tool_name):
                method = getattr(self, tool_name)
                if callable(method):
                    tool_func = method
                    tool_source = "类方法"

            # 优先级3: 都没有找到
            if tool_func is None:
                available_tools = self.get_all_available_tools()
                tool_error = f"工具错误：找不到工具 '{tool_name}'。"
                if available_tools:
                    tool_error += f" 你可以使用以下工具：{', '.join(available_tools)}"

                work_history.append({"role": "system", "content": tool_error})
                tool_results.append({"error": tool_error})
                logger.warning(f"工具 {tool_name} 未找到，可用工具: {available_tools}")
                continue

            logger.debug(f"从 {tool_source} 找到工具 {tool_name}")
            print(block)

            # 解析 XML 参数，与 Function Calling 模式统一
            params = {}
            for child in root.children:
                if hasattr(child, 'name') and child.name:
                    params[child.name] = child.text

            # 生成任务 ID
            self.current_task_id = self._generate_task_id()

            # 执行 before 钩子
            self._execute_hooks('before', tool_name, params)

            self.pack(tool_name=tool_name, tool_parameter=params)

            # 执行工具：根据函数签名决定传递 dict 还是解包参数
            import inspect
            sig = inspect.signature(tool_func)
            param_count = len([p for p in sig.parameters.values()
                             if p.default == inspect.Parameter.empty and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)])

            # 如果函数接受 **kwargs 或单个参数，传递整个 dict
            # 否则解包参数
            has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            if has_kwargs or param_count == 0:
                result = tool_func(**params) if params else tool_func()
            elif param_count == 1 and len(params) == 1:
                # 单个参数函数，传递单个值
                result = tool_func(list(params.values())[0])
            else:
                # 多参数函数，尝试解包
                try:
                    result = tool_func(**params)
                except TypeError:
                    # 如果解包失败，回退到传递整个 XML 块（向后兼容）
                    result = tool_func(block)

            # 执行 after 钩子（传入结果）
            self._execute_hooks('after', tool_name, params, result)

            # 打包工具返回值
            self.pack(tool_result=result, tool_name=tool_name)

            if not result:
                result = f"no return for the tool{tool_name}"

            tool_results.append(result)
            tool_names.append(tool_name)

        #如配置错误强制跳出避免堵塞
        if "<attempt_completion>" in full_content:
            self.pack("\n[系统] AI 已标记任务完成，程序退出。", tool_name="attempt_completion")
            # sys.exit(0)

        # 3. 若工具产生结果，继续对话
        if tool_results:
            logger.debug(f"工具执行成功，结果数：{len(tool_results)}")
            n = 0
            for i in tool_results:
                try:
                    work_history.append({"role": "system", "content": f"{tool_names[n]} results: {i}"})
                    n+=1
                except:
                    break
            logger.debug(f"对话历史长度：{len(self.history)}")
            return self.conversation_with_tool(tool=True)
        if tool:
            logger.debug(f"返回对话历史最后一条，长度：{len(work_history[-1].get('content')) if work_history[-1].get('content') else 0}")
            return work_history[-1].get("content")
        return full_content

    def pack(self, message=None, tool_model=False, tool_name=None, tool_parameter=None,
             finish_task=False, other=False, tool_result=None):
        """
        打包输出内容，包含 AI UUID、任务戳和时间戳

        参数:
            message: 普通消息内容
            tool_model: 是否是工具模型调用
            tool_name: 工具名称
            tool_parameter: 工具参数
            finish_task: 是否任务完成
            other: 其他标记
            tool_result: 工具执行结果
        """
        content = {}
        task_id = self.current_task_id or self._generate_task_id()
        timestamp = self._get_timestamp()

        if finish_task:
            content = {
                "task": True,
                "task_id": task_id,
                "timestamp": timestamp,
                "ai_uuid": self.uuid,
                "ai_name": self.name
            }
        elif tool_model:
            content = {
                "tool_name": tool_name,
                "tool_parameter": tool_parameter,
                "ai_uuid": self.uuid,
                "ai_name": self.name,
                "task_id": task_id,
                "timestamp": timestamp,
                "task": False
            }
        elif tool_result is not None:
            # 工具返回值
            content = {
                "tool_result": tool_result,
                "tool_name": tool_name,
                "ai_uuid": self.uuid,
                "ai_name": self.name,
                "task_id": task_id,
                "timestamp": timestamp,
                "task": False
            }
        else:
            content = {
                "message": message,
                "ai_uuid": self.uuid,
                "ai_name": self.name,
                "other": other,
                "task_id": task_id,
                "timestamp": timestamp,
                "task": False
            }
        self.out(content)

    def out(self, content):
        if content.get("tool_name"):
            print("调用工具:", content.get("tool_name"), "参数", content.get("tool_parameter"))
            return
        if not content.get("task"):
            message = content.get("message")
            if message is not None:
                print(message, end="")
        else:
            print()


    # ---------------- 内置工具 ----------------

    def reload(self):
        self.__init__(new_load=False)
        return "successfully reloaded"

    def get_all_available_tools(self) -> list:
        tools = []
        tools_info = tool_registry.get_all_tools_info(self.uuid)
        if tools_info:
            tools.extend(tools_info.keys())
        builtin_tools = ['ask_for_help', 'list_agents', 'attempt_completion']
        for tool_name in builtin_tools:
            if hasattr(self, tool_name) and callable(getattr(self, tool_name)):
                tools.append(tool_name)
        return tools

    def ask_for_help(self, **kwargs):
        """
        工具方法：请求其他 Agent 帮助
        参数可以通过 XML 或 Function Calling 传递
        """
        # 支持两种调用方式
        agent_id = None
        message = None

        # Function Calling 方式（dict 参数）
        if 'agent_id' in kwargs and 'message' in kwargs:
            agent_id = kwargs['agent_id']
            message = kwargs['message']
        # XML 方式（字符串参数）
        elif len(kwargs) == 1:
            xml_block = list(kwargs.values())[0]
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(xml_block, "xml")
            agent_id_tag = soup.find("agent_id")
            message_tag = soup.find("message")
            if agent_id_tag:
                agent_id = agent_id_tag.text.strip()
            if message_tag:
                message = message_tag.text.strip()

        if agent_id is None:
            return "<ask_for_help> 缺少 agent_id 字段"
        if message is None:
            return "<ask_for_help> 缺少 message 字段"

        try:
            from dumplingsAI import agent_list
            target_cls = agent_list[agent_id]
        except KeyError as e:
            return f"未找到 uuid/别名 {e}"

        target_ins = target_cls
        reply = target_ins.conversation_with_tool(message)
        return reply

    def list_agents(self, **kwargs):
        """
        工具方法：返回所有可用的Agent列表，包括它们的UUID和名称
        """
        from dumplingsAI import agent_list

        # 获取所有唯一的Agent（避免UUID和名称重复）
        unique_agents = {}
        for key, agent in agent_list.items():
            agent_uuid = getattr(agent, 'uuid', None)
            agent_name = getattr(agent, 'name', None)
            agent_description = getattr(agent, 'description', None)
            if agent_uuid and agent_name:
                if agent_uuid not in unique_agents:
                    unique_agents[agent_uuid] = {
                        'uuid': agent_uuid,
                        'name': agent_name,
                        "description": agent_description
                    }

        # 格式化为易读的字符串
        agents_info = []
        for agent_info in unique_agents.values():
            agents_info.append(f"- {agent_info['name']} (UUID: {agent_info['uuid']}) description: {agent_info['description']}")

        result = "可用的Agent列表：\n" + "\n".join(agents_info)
        return result

    def attempt_completion(self, **kwargs):
        """
        工具方法：标记任务完成并退出
        """
        # 支持两种调用方式
        report_content = ""

        # Function Calling 方式（dict 参数）
        if 'report_content' in kwargs:
            report_content = kwargs['report_content']
        # XML 方式（字符串参数）
        elif len(kwargs) == 1:
            xml_block = list(kwargs.values())[0]
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(xml_block, "xml")
            report_content_tag = soup.find("report_content")
            if report_content_tag:
                report_content = report_content_tag.text


        # 返回完成报告，作为 tool 响应内容
        return report_content if report_content else "任务已完成"

