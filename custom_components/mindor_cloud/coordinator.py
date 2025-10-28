from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.core import HomeAssistant
from datetime import timedelta
import aiohttp
from .const import API_BASE
from .request_config import RequestConfig
from .websocket_client import MindorWebSocketClient

import logging

_LOGGER = logging.getLogger(__name__)


class MindorDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.token = entry.data["token"]
        self.devices = entry.data["devices"]
        self.user_id = entry.data["user_id"]
        self.websocket_url = "wss://lock.wangjile.cn/cable"
        self.websocket_client: MindorWebSocketClient = None
        self.real_time_enabled = True

        super().__init__(
            hass,
            _LOGGER,
            name="Mindor Cloud",
            update_interval=timedelta(minutes=5),
        )

    async def _on_ha_started(self, event):
        """Home Assistant启动时更新数据并建立WebSocket连接"""
        await self.async_request_refresh()
        if self.real_time_enabled:
            await self._setup_websocket()

    async def _setup_websocket(self):
        """设置WebSocket连接"""
        try:
            self.websocket_client = MindorWebSocketClient(
                token=self.token,
                user_id=self.user_id,
                websocket_url=self.websocket_url,
                on_message_callback=self._handle_websocket_message,
            )

            # 更新设备列表到WebSocket客户端
            self.websocket_client.update_equipment_list(self.devices)

            if await self.websocket_client.init_websocket():
                _LOGGER.info(f"WebSocket连接成功，管理{len(self.devices)}个设备")
            else:
                _LOGGER.warning("WebSocket连接失败，将使用轮询模式")

        except Exception as e:
            _LOGGER.error(f"设置WebSocket连接时出错: {e}")

    async def _handle_websocket_message(self, message: dict):
        """处理WebSocket消息"""
        try:
            msg_type = message.get("type")

            if msg_type == "welcome":
                _LOGGER.info("收到WebSocket欢迎消息")
            elif msg_type == "ping":
                _LOGGER.debug("收到WebSocket心跳")
            elif "message" in message:
                # 设备状态更新
                websocket_msg = message.get("message", {})
                if isinstance(websocket_msg, dict) and websocket_msg.get("device_id"):
                    await self._update_device_from_websocket(websocket_msg)

        except Exception as e:
            _LOGGER.error(f"处理WebSocket消息时出错: {e}")

    async def _update_device_from_websocket(self, websocket_msg: dict):
        """从WebSocket消息更新设备数据"""
        _LOGGER.warning(f"收到WebSocket消息: {websocket_msg}")
        try:
            device_id = websocket_msg.get("device_id")
            if not device_id:
                return

            # 查找并更新设备数据
            for i, device in enumerate(self.devices):
                if (
                    device.get("device_id") == device_id
                    or device.get("id") == device_id
                ):
                    # 确保设备有device_act_status数组
                    if "device_act_status" not in self.devices[i]:
                        self.devices[i]["device_act_status"] = []

                    act_status = self.devices[i]["device_act_status"]

                    # 处理act_arr中的各种动作
                    if websocket_msg.get("act_arr"):
                        for act in websocket_msg["act_arr"]:
                            act_type = act.get("act")
                            act_val = act.get("val")

                            if act_type == "source":
                                # 插座开关状态
                                is_on = act_val != "off"
                                self.devices[i]["l1_state"] = is_on
                                _LOGGER.info(
                                    f"设备 {device_id} 开关状态更新为: {is_on}"
                                )

                            elif act_type == "power":
                                # 电量款插座的实时功率
                                power_value = (
                                    float(act_val)
                                    if act_val.replace(".", "").isdigit()
                                    else 0.0
                                )
                                self._update_act_status(act_status, "power", act_val)
                                _LOGGER.info(
                                    f"插座 {device_id} 实时功率更新为: {power_value}W"
                                )

                            elif act_type == "thermoregulation":
                                # 空调温度调节 - 更新device_act_status数组
                                self._update_act_status(
                                    act_status, "thermoregulation", act_val
                                )
                                _LOGGER.info(
                                    f"空调 {device_id} 目标温度更新为: {act_val}°C"
                                )

                            elif act_type == "mode":
                                # 空调模式切换 - 更新device_act_status数组
                                self._update_act_status(act_status, "mode", act_val)
                                mode_names = {
                                    "01": "制冷",
                                    "02": "制热",
                                    "03": "除湿",
                                    "04": "送风",
                                    "05": "自动",
                                }
                                mode_name = mode_names.get(act_val, f"模式{act_val}")
                                _LOGGER.info(
                                    f"空调 {device_id} 模式更新为: {mode_name}"
                                )

                            elif act_type == "airSwing":
                                # 空调摆风控制 - 更新device_act_status数组
                                self._update_act_status(act_status, "airSwing", act_val)
                                swing_names = {
                                    "00": "关闭扫风",
                                    "01": "上下扫风",
                                    "02": "左右扫风",
                                }
                                swing_name = swing_names.get(act_val, f"摆风{act_val}")
                                _LOGGER.info(
                                    f"空调 {device_id} 摆风状态更新为: {swing_name}"
                                )

                            elif act_type == "windGear":
                                # 空调风速控制 - 更新device_act_status数组
                                self._update_act_status(act_status, "windGear", act_val)
                                wind_names = {
                                    "00": "自动",
                                    "01": "低速",
                                    "02": "中速",
                                    "03": "高速",
                                }
                                wind_name = wind_names.get(act_val, f"风速{act_val}")
                                _LOGGER.info(
                                    f"空调 {device_id} 风速更新为: {wind_name}"
                                )
                            elif act_type == "On":
                                # 空调开关状态
                                self._update_act_status(act_status, "On", act_val)
                                _LOGGER.info(
                                    f"空调 {device_id} 开关状态更新为: {act_val}"
                                )

                    # 处理在线状态
                    if websocket_msg.get("type") == "status":
                        is_online = websocket_msg.get("data") == "online"
                        self.devices[i]["online"] = is_online
                        _LOGGER.info(f"设备 {device_id} 在线状态更新为: {is_online}")

                    # 触发实体更新
                    self.async_update_listeners()
                    _LOGGER.debug(f"已更新设备 {device_id} 的实时数据")
                    break

        except Exception as e:
            _LOGGER.error(f"从WebSocket更新设备数据时出错: {e}")

    def _update_act_status(
        self, act_status: list, act_name: str, new_value: str
    ) -> None:
        """更新device_act_status数组中的指定状态值"""
        # 查找并更新现有状态项
        for item in act_status:
            if item.get("act") == act_name:
                item["val"] = new_value
                _LOGGER.debug(f"已更新设备状态: {act_name} = {new_value}")
                return

        # 如果没找到对应项，则添加新项
        act_status.append({"act": act_name, "val": new_value})
        _LOGGER.debug(f"已添加设备状态: {act_name} = {new_value}")

    async def async_shutdown(self):
        """关闭coordinator时清理WebSocket连接"""
        if self.websocket_client:
            await self.websocket_client.disconnect()
            self.websocket_client = None
        _LOGGER.info("Mindor Cloud coordinator已关闭")

    async def _async_update_data(self):
        """获取设备数据的更新方法"""
        try:
            # 使用现有的API配置获取设备数据
            request_config = RequestConfig()
            opt = request_config.get_opt()
            sign = request_config.generate_sign(opt)

            headers = {
                "Content-Type": "application/json",
                "Authorization": self.token,
                "Sign": sign,
            }

            opt_str = {str(k): str(v) for k, v in opt.items()}
            merged_headers = {
                **dict(headers),
                **opt_str,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{API_BASE}/md_openapi/home_assistant/devices",
                    headers=merged_headers,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        _LOGGER.warning(f"Devices: {data}")
                        if data.get("errcode") != 0:
                            _LOGGER.error(f"API返回错误: {data.get('msg')}")
                            raise Exception(f"API错误: {data.get('msg', '未知错误')}")

                        _LOGGER.debug(f"设备列表: {data}")
                        # 更新设备列表
                        self.devices = data["records"]
                        _LOGGER.debug(f"成功更新 {len(self.devices)} 个设备的数据")
                        return self.devices
                    else:
                        _LOGGER.error(f"HTTP请求失败: {response.status}")
                        raise Exception(f"HTTP错误: {response.status}")

        except Exception as e:
            _LOGGER.error(f"更新设备数据时出错: {e}")
            raise
