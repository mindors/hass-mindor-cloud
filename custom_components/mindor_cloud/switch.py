import logging
from typing import Any, Dict, List, Optional
import aiohttp
import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, SOCKET_LIST, SOCKET_POWER_LIST, API_BASE
from .coordinator import MindorDataUpdateCoordinator
from .utils import debounce_command, get_global_debouncer
from .request_config import RequestConfig

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置开关实体"""
    coordinator: MindorDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    # 获取设备列表
    devices = coordinator.data if isinstance(coordinator.data, list) else []
    _LOGGER.debug(f"获取到 {len(devices)} 个设备")

    entities = []

    for device in devices:
        device_type = device.get("typ_spu", "")
        _LOGGER.debug(
            f"处理设备: {device.get('name', 'Unknown')} (类型: {device_type})"
        )

        if device_type in SOCKET_LIST:
            _LOGGER.debug(f"创建普通插座实体: {device.get('name')}")
            entities.append(MindorSocketEntity(coordinator, device))
        elif device_type in SOCKET_POWER_LIST:
            _LOGGER.debug(f"创建电量插座实体: {device.get('name')}")
            entities.append(MindorSocketPowerEntity(coordinator, device))

    if entities:
        async_add_entities(entities, True)
        _LOGGER.info(f"成功添加 {len(entities)} 个开关实体")
    else:
        _LOGGER.warning("未找到支持的插座设备")


class MindorSocketEntity(CoordinatorEntity, SwitchEntity):
    """Mindor 插座实体"""

    def __init__(
        self, coordinator: MindorDataUpdateCoordinator, device: Dict[str, Any]
    ):
        super().__init__(coordinator)
        self._device = device
        self._attr_name = device.get("name", "Unknown Socket")
        self._attr_unique_id = f"mindor_socket_{device.get('id', 'unknown')}"

        # 获取全局防抖器
        self._debouncer = get_global_debouncer()

    @property
    def device_info(self) -> DeviceInfo:
        """返回设备信息"""
        return DeviceInfo(
            identifiers={(DOMAIN, f"socket_{self._device.get('id', 'unknown')}")},
            name=self._device.get("name", "Unknown Device"),
            manufacturer="Mindor",
            model=self._device.get("typ_spu", "Unknown Model"),
            sw_version=self._device.get("firmware_ver", "1.0"),
        )

    @property
    def is_on(self) -> bool:
        """返回开关状态"""
        # 如果刚执行过命令（30秒内），优先使用本地状态
        if hasattr(self, '_last_command_time'):
            if time.time() - self._last_command_time < 30:
                return self._device.get("l1_state", False)
        
        # 否则从coordinator获取最新设备状态
        devices = (
            self.coordinator.data if isinstance(self.coordinator.data, list) else []
        )
        for device in devices:
            if device.get("id") == self._device.get("id"):
                return device.get("l1_state")
        return False

    @property
    def available(self) -> bool:
        """返回设备可用性"""
        # 从coordinator获取最新设备状态
        devices = (
            self.coordinator.data if isinstance(self.coordinator.data, list) else []
        )

        # 获取当前设备状态
        current_device_online = False
        for device in devices:
            if device.get("id") == self._device.get("id"):
                current_device_online = device.get("online", False)
                break

        # 严格根据设备自身的在线状态决定可用性
        is_available = current_device_online

        _LOGGER.debug(
            f"设备 {self._attr_name} 可用性检查: "
            f"自身在线={current_device_online}, "
            f"最终可用={is_available}"
        )

        return is_available

    @debounce_command(interval=1.0)
    async def async_turn_on(self, **kwargs: Any) -> None:
        """打开开关"""
        if not self.available:
            _LOGGER.warning(f"设备 {self._attr_name} 不可用，无法执行开启操作")
            return

        _LOGGER.info(f"正在打开插座: {self._attr_name}")

        success = await self._send_command("on")

        if success:
            _LOGGER.info(f"插座 {self._attr_name} 已成功打开")
            # 触发协调器更新以获取最新状态
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(f"插座 {self._attr_name} 打开失败")

    @debounce_command(interval=1.0)
    async def async_turn_off(self, **kwargs: Any) -> None:
        """关闭开关"""
        if not self.available:
            _LOGGER.warning(f"设备 {self._attr_name} 不可用，无法执行关闭操作")
            return

        _LOGGER.info(f"正在关闭插座: {self._attr_name}")

        success = await self._send_command("off")

        if success:
            _LOGGER.info(f"插座 {self._attr_name} 已成功关闭")
            # 触发协调器更新以获取最新状态
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(f"插座 {self._attr_name} 关闭失败")

    async def _send_command(self, command: str) -> bool:
        """发送控制命令"""
        try:
            # 获取配置条目中的 token
            token = self.coordinator.config_entry.data.get("token")
            if not token:
                _LOGGER.error("未找到有效的 token")
                return False

            device_id = self._device.get("device_id")
            if not device_id:
                _LOGGER.error("设备device_id为空")
                return False

            # 创建请求配置
            req = RequestConfig()
            opt = req.get_opt()
            sign = req.generate_sign(opt)

            # 构建请求头
            headers = {
                "Authorization": token,
                "Sign": sign,
                "Content-Type": "application/json",
            }

            # 添加 opt 参数到请求头
            opt_str = {str(k): str(v) for k, v in opt.items()}
            merged_headers = {
                **dict(headers),
                **opt_str,
            }

            # 构建请求数据
            request_data = {
                "device_id": device_id,
                "act": command,
            }

            _LOGGER.debug(f"发送命令 {command} 到设备 {device_id}")

            # 发送 API 请求
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{API_BASE}/md_openapi/home_assistant/ctrl",
                    json=request_data,
                    headers=merged_headers,
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.error(f"API请求失败，状态码: {resp.status}")
                        return False

                    response_data = await resp.json()
                    _LOGGER.debug(f"API响应: {response_data}")

                    # 检查响应结果
                    if response_data.get("errcode") == 0:
                        _LOGGER.info(f"设备 {device_id} 命令 {command} 执行成功")

                        # 立即更新本地设备状态
                        new_state = command == "on"
                        self._device["l1_state"] = new_state

                        if hasattr(self.coordinator, "data") and self.coordinator.data:
                            for device in self.coordinator.data:
                                if device.get("id") == self._device.get("id"):
                                    device["l1_state"] = new_state
                                    break

                        # 设置一个标志，防止协调器立即更新覆盖状态
                        self._last_command_time = time.time()

                        # 立即更新HA状态
                        self.async_write_ha_state()

                        _LOGGER.info(f"设备 {device_id} 命令 {command} 执行成功")
                        return True
                    else:
                        error_msg = response_data.get("msg", "未知错误")
                        _LOGGER.error(f"设备控制失败: {error_msg}")
                        return False

        except aiohttp.ClientError as e:
            _LOGGER.error(f"网络请求错误: {e}")
            return False
        except Exception as e:
            _LOGGER.error(f"发送命令失败: {e}")
            return False

    async def _get_device_status(self) -> Dict[str, Any]:
        """获取设备状态"""
        try:
            # 获取配置条目中的 token
            token = self.coordinator.config_entry.data.get("token")
            if not token:
                _LOGGER.error("未找到有效的 token")
                return {}

            device_id = self._device.get("id")
            if not device_id:
                _LOGGER.error("设备ID为空")
                return {}

            # 创建请求配置
            req = RequestConfig()
            opt = req.get_opt()
            sign = req.generate_sign(opt)

            # 构建请求头
            headers = {
                "Authorization": token,
                "Sign": sign,
            }

            # 添加 opt 参数到请求头
            opt_str = {str(k): str(v) for k, v in opt.items()}
            headers.update(opt_str)

            _LOGGER.debug(f"获取设备 {device_id} 状态")

            # 发送 API 请求
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{API_BASE}/md_openapi/home_assistant/device/status?device_id={device_id}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.error(f"获取设备状态失败，状态码: {resp.status}")
                        return {}

                    response_data = await resp.json()
                    _LOGGER.debug(f"设备状态响应: {response_data}")

                    # 检查响应结果
                    if response_data.get("errcode") == 0:
                        return response_data.get("data", {})
                    else:
                        error_msg = response_data.get("msg", "未知错误")
                        _LOGGER.error(f"获取设备状态失败: {error_msg}")
                        return {}

        except aiohttp.ClientError as e:
            _LOGGER.error(f"网络请求错误: {e}")
            return {}
        except Exception as e:
            _LOGGER.error(f"获取设备状态失败: {e}")
            return {}


class MindorSocketPowerEntity(MindorSocketEntity):
    """Mindor 电量插座实体"""

    def __init__(
        self, coordinator: MindorDataUpdateCoordinator, device: Dict[str, Any]
    ):
        super().__init__(coordinator, device)
        self._attr_name = f"{device.get('name', 'Unknown Socket')} (电量)"
        self._attr_unique_id = f"mindor_socket_power_{device.get('id', 'unknown')}"

    @property
    def device_info(self) -> DeviceInfo:
        """返回设备信息"""
        return DeviceInfo(
            identifiers={(DOMAIN, f"socket_power_{self._device.get('id', 'unknown')}")},
            name=f"{self._device.get('name', 'Unknown Device')} (电量版)",
            manufacturer="Mindor",
            model=f"{self._device.get('typ_spu', 'Unknown Model')} (Power)",
            sw_version=self._device.get("firmware_ver", "1.0"),
        )

    @property
    def is_on(self) -> bool:
        """返回设备是否开启"""
        # 如果刚执行过命令（30秒内），优先使用本地状态
        if hasattr(self, "_last_command_time"):
            if time.time() - self._last_command_time < 30:
                return self._device.get("l1_state", False)

        # 否则从协调器数据获取最新状态
        if self.coordinator.data:
            for device in self.coordinator.data:
                if device.get("id") == self._device.get("id"):
                    return device.get("l1_state", False)
        return self._device.get("l1_state", False)

    @property
    def available(self) -> bool:
        """电量插座的可用性检查"""
        # 从coordinator获取最新设备状态
        devices = (
            self.coordinator.data if isinstance(self.coordinator.data, list) else []
        )

        # 检查是否有任何电量插座在线
        any_power_socket_online = False
        for device in devices:
            if device.get("typ_spu") in SOCKET_POWER_LIST and device.get("online"):
                any_power_socket_online = True
                break
        # 获取当前设备状态
        current_device_online = False
        for device in devices:
            if device.get("id") == self._device.get("id"):
                current_device_online = device.get("online", False)
                break

        # 如果有电量插座在线，所有设备都可用；否则检查自身状态
        is_available = any_power_socket_online or current_device_online

        _LOGGER.debug(
            f"电量插座 {self._attr_name} 可用性检查: "
            f"任意电量插座在线={any_power_socket_online}, "
            f"自身在线={current_device_online}, "
            f"最终可用={is_available}"
        )

        return is_available

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """返回额外的状态属性"""
        # 从coordinator获取最新设备状态
        devices = (
            self.coordinator.data if isinstance(self.coordinator.data, list) else []
        )
        current_device = None

        for device in devices:
            if device.get("id") == self._device.get("id"):
                current_device = device
                break

        if not current_device:
            current_device = self._device

        # 检查是否有任何电量插座在线
        any_power_socket_online = False
        for device in devices:
            if device.get("typ_spu") in SOCKET_POWER_LIST and device.get("online"):
                any_power_socket_online = True
                break

    async def async_update(self) -> None:
        """更新设备状态"""
        try:
            # 触发coordinator更新
            await self.coordinator.async_request_refresh()

            # 从coordinator获取最新设备信息
            devices = (
                self.coordinator.data if isinstance(self.coordinator.data, list) else []
            )
            for device in devices:
                if device.get("id") == self._device.get("id"):
                    # 更新设备信息
                    self._device.update(device)
                    break

            _LOGGER.debug(f"电量插座 {self._attr_name} 状态已更新")

        except Exception as e:
            _LOGGER.error(f"更新电量插座 {self._attr_name} 状态失败: {e}")
