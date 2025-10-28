import asyncio
import json
import logging
from typing import Callable, Optional, Dict, Any, List
import aiohttp
from aiohttp import WSMsgType
from .const import API_BASE
from .request_config import RequestConfig

_LOGGER = logging.getLogger(__name__)


class MindorWebSocketClient:
    """Mindor云服务WebSocket客户端 - 完全基于JavaScript实现"""

    def __init__(
        self,
        token: str,
        user_id: str,
        websocket_url: str,
        on_message_callback: Optional[Callable] = None,
    ):
        self.token = token
        self.user_id = user_id
        self.websocket_url = websocket_url
        self.on_message_callback = on_message_callback
        self.session: Optional[aiohttp.ClientSession] = None
        self.websocket: Optional[aiohttp.ClientWebSocketResponse] = None

        # 连接状态管理
        self.is_connected = False
        self.is_close = False
        self.is_connected_websocket = False
        self.is_reconnect = False
        self.connect_count = 0
        self.max_connect_attempts = 30

        # 定时器
        self.reconnect_timer = None
        self.connect_timer = None

        # 监听任务
        self._listen_task: Optional[asyncio.Task] = None

        # 设备数据缓存
        self.equipment_list = []
        self.handle_info = {}

    async def init_websocket(self) -> bool:
        """初始化WebSocket连接 - 基于JavaScript的initWebsocket方法"""
        try:
            if self.session is None:
                self.session = aiohttp.ClientSession()

            # 构建请求头
            headers = {
                "content-type": "application/json",
                "Authorization": self.token,
            }

            _LOGGER.info(f"正在连接WebSocket: {self.websocket_url}")

            self.websocket = await self.session.ws_connect(
                self.websocket_url,
                headers=headers,
                heartbeat=30,
                timeout=aiohttp.ClientTimeout(total=10),
            )

            # 连接成功处理
            await self._on_websocket_open()

            # 启动消息监听
            self._listen_task = asyncio.create_task(self._listen_messages())

            return True

        except Exception as e:
            _LOGGER.error(f"WebSocket连接失败: {e}")
            await self._on_websocket_error(e)
            return False

    async def _on_websocket_open(self):
        """WebSocket连接打开事件处理"""
        _LOGGER.info("WebSocket已经打开")
        self.is_close = True
        self.is_connected_websocket = True
        self.is_connected = True
        self.connect_count = 0

        # 延迟1秒处理重连状态
        if self.reconnect_timer:
            self.reconnect_timer.cancel()

        self.reconnect_timer = asyncio.create_task(self._delayed_reconnect_reset())

        # 订阅整个设备列表
        await self._subscribe_device_list()

    async def _delayed_reconnect_reset(self):
        """延迟重置重连状态"""
        await asyncio.sleep(1)
        _LOGGER.debug("延迟1秒再处理isReconnect状态")
        self.is_reconnect = False

    async def _subscribe_device_list(self):
        """订阅设备列表频道"""
        try:
            subscribe_info = {
                "channel": "V5MdDeviceListChannel",
                "wx_user_id": self.user_id,
            }

            subscribe_message = {
                "command": "subscribe",
                "identifier": json.dumps(subscribe_info),
            }

            _LOGGER.info(f"订阅整个列表信息: {subscribe_message}")

            await self.websocket.send_str(json.dumps(subscribe_message))
            _LOGGER.info("订阅整个列表结果: 已发送")

        except Exception as e:
            _LOGGER.error(f"订阅设备列表失败: {e}")

    async def _on_websocket_error(self, error):
        """WebSocket错误事件处理"""
        _LOGGER.error(f"WebSocket错误: {error}")

        if self.connect_count > self.max_connect_attempts:
            _LOGGER.error(f"重新连接{self.max_connect_attempts}次，退出")
            return

        self.connect_count += 1

        # 3秒后重新连接
        if self.connect_timer:
            self.connect_timer.cancel()

        self.connect_timer = asyncio.create_task(self._delayed_reconnect())

    async def _delayed_reconnect(self):
        """延迟重连"""
        await asyncio.sleep(3)
        _LOGGER.info("3秒后重新连接websocket")
        await self.connect_websocket()

    async def _on_websocket_close(self, close_info=None):
        """WebSocket关闭事件处理"""
        _LOGGER.info(f"WebSocket连接已关闭: {close_info}")
        self.is_connected = False

        if self.is_reconnect:
            _LOGGER.info("主动断开不需要重新连接")
            return

        if self.is_close:
            if self.connect_timer:
                self.connect_timer.cancel()

            _LOGGER.info("准备重新连接websocket")
            await self.connect_websocket()

    async def _on_websocket_message(self, message_data):
        """WebSocket消息事件处理"""
        try:
            result = json.loads(message_data)
            websocket_msg = result.get("message")

            # 忽略ping消息，处理其他消息
            if result.get("type") != "ping" and isinstance(websocket_msg, dict):
                await self._process_device_message(websocket_msg)

            # 调用外部回调
            if self.on_message_callback:
                await self.on_message_callback(result)

        except json.JSONDecodeError as e:
            _LOGGER.error(f"解析WebSocket消息失败: {e}")
        except Exception as e:
            _LOGGER.error(f"处理WebSocket消息异常: {e}")

    async def _process_device_message(self, websocket_msg: dict):
        """处理设备消息"""
        try:
            device_id = websocket_msg.get("device_id")
            if not device_id:
                return

            # 查找设备在列表中的索引
            find_idx = -1
            for i, item in enumerate(self.equipment_list):
                if item.get("device_id") == device_id:
                    find_idx = i
                    break

            if find_idx == -1:
                return

            # 处理动作数组
            if websocket_msg.get("act_arr"):
                await self._process_device_actions(websocket_msg, find_idx)

            # 处理设备状态（在线/离线）
            if websocket_msg.get("type") == "status":
                await self._process_device_status(websocket_msg, find_idx)

        except Exception as e:
            _LOGGER.error(f"处理设备消息异常: {e}")

    async def _process_device_actions(self, websocket_msg: dict, find_idx: int):
        """处理设备动作"""
        try:
            device_id = websocket_msg.get("device_id")
            act_arr = websocket_msg.get("act_arr", [])

            for item in act_arr:
                if item.get("act") == "source":
                    # 清除设备操作状态
                    if find_idx < len(self.equipment_list):
                        self.equipment_list[find_idx]["isOperation"] = False

                    # 处理定时回调
                    await self._handle_timer_callback(
                        device_id, item, find_idx, callback_type="switch"
                    )

        except Exception as e:
            _LOGGER.error(f"处理设备动作异常: {e}")

    async def _process_device_status(self, websocket_msg: dict, find_idx: int):
        """处理设备状态"""
        try:
            device_id = websocket_msg.get("device_id")
            device_type_id = f"{device_id}_status"

            # 初始化处理信息
            if device_type_id not in self.handle_info:
                self.handle_info[device_type_id] = []

            self.handle_info[device_type_id].append(websocket_msg)

            # 清除设备操作状态
            if find_idx < len(self.equipment_list):
                self.equipment_list[find_idx]["isOperation"] = False

            # 处理状态回调
            await self._handle_timer_callback(
                device_type_id,
                self.handle_info[device_type_id],
                find_idx,
                callback_type="status",
                delay=1,
            )

        except Exception as e:
            _LOGGER.error(f"处理设备状态异常: {e}")

    async def _handle_timer_callback(
        self, key: str, item: Any, find_idx: int, callback_type: str, delay: int = 0
    ):
        """处理定时回调"""
        try:
            if delay > 0:
                await asyncio.sleep(delay)

            if callback_type == "switch":
                # 处理开关状态
                switch_val = item.get("val", "off")
                is_on = switch_val != "off"

                if find_idx < len(self.equipment_list):
                    self.equipment_list[find_idx]["isOn"] = is_on

                # 清除处理信息
                device_id = key
                if device_id in self.handle_info:
                    self.handle_info[device_id] = []

                _LOGGER.info(f"设备 {device_id} 开关状态更新为: {is_on}")

            elif callback_type == "status":
                # 处理在线状态
                status_list = item if isinstance(item, list) else [item]
                is_online = any(
                    status_item.get("data") == "online" for status_item in status_list
                )

                if find_idx < len(self.equipment_list):
                    self.equipment_list[find_idx]["online"] = is_online

                # 清除处理信息
                if key in self.handle_info:
                    self.handle_info[key] = []

                _LOGGER.info(f"设备状态更新 - 在线状态: {is_online}")

        except Exception as e:
            _LOGGER.error(f"处理定时回调异常: {e}")

    async def _listen_messages(self):
        """监听WebSocket消息"""
        try:
            async for msg in self.websocket:
                if msg.type == WSMsgType.TEXT:
                    await self._on_websocket_message(msg.data)
                elif msg.type == WSMsgType.ERROR:
                    await self._on_websocket_error(self.websocket.exception())
                    break
                elif msg.type == WSMsgType.CLOSE:
                    await self._on_websocket_close(msg.data)
                    break
        except Exception as e:
            _LOGGER.error(f"WebSocket消息监听异常: {e}")
        finally:
            self.is_connected = False

    async def connect_websocket(self):
        """连接WebSocket（重连入口）"""
        return await self.init_websocket()

    async def disconnect(self):
        """断开WebSocket连接"""
        self.is_reconnect = True
        self.is_connected = False

        # 取消定时器
        if self.reconnect_timer:
            self.reconnect_timer.cancel()
        if self.connect_timer:
            self.connect_timer.cancel()

        # 取消监听任务
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        # 关闭连接
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        if self.session:
            await self.session.close()
            self.session = None

        _LOGGER.info("WebSocket连接已断开")

    def update_equipment_list(self, equipment_list: List[Dict]):
        """更新设备列表"""
        self.equipment_list = equipment_list
        # 初始化处理信息
        for device in equipment_list:
            device_id = device.get("device_id")
            if device_id:
                if device_id not in self.handle_info:
                    self.handle_info[device_id] = []
                status_key = f"{device_id}_status"
                if status_key not in self.handle_info:
                    self.handle_info[status_key] = []

    def get_equipment_list(self) -> List[Dict]:
        """获取当前设备列表"""
        return self.equipment_list
