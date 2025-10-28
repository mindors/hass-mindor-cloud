from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import (
    HVACMode,
    HVACAction,
    FAN_AUTO,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from typing import Any, Dict, List, Optional
import aiohttp
import asyncio
import logging

from . import DOMAIN
from .const import AIR_LIST, API_BASE
from .coordinator import MindorDataUpdateCoordinator
from .request_config import RequestConfig
from .utils import debounce_command, get_global_debouncer

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """设置空调实体"""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    devices = coordinator.devices
    entities = []

    for dev in devices:
        device_type = dev.get("typ_spu", "")
        if device_type in AIR_LIST:
            entities.append(MindorClimateEntity(coordinator, dev))
            _LOGGER.info(f"添加空调设备: {dev.get('name', 'Unknown')} ({device_type})")

    async_add_entities(entities)
    _LOGGER.info(f"已添加 {len(entities)} 个空调设备")


class MindorClimateEntity(CoordinatorEntity, ClimateEntity):
    """Mindor 空调伴侣实体 - 无延迟版本"""

    def __init__(
        self, coordinator: MindorDataUpdateCoordinator, device: Dict[str, Any]
    ):
        super().__init__(coordinator)
        self._device = device
        self._attr_name = device.get("name", "Unknown Air Conditioner")
        self._attr_unique_id = f"mindor_climate_{device.get('id', 'unknown')}"

        # 获取全局防抖器
        self._debouncer = get_global_debouncer()

        # 添加第一次开机标志
        self._first_power_on = True

        # 空调基本配置
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
        )

        # 支持的模式
        self._attr_hvac_modes = [
            HVACMode.OFF,
            HVACMode.AUTO,
            HVACMode.COOL,
            HVACMode.HEAT,
            HVACMode.DRY,
            HVACMode.FAN_ONLY,
        ]

        # 支持的风速
        self._attr_fan_modes = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]

        # 支持的摆风模式
        self._attr_swing_modes = ["关闭扫风", "上下扫风", "左右扫风"]

        # 温度范围
        self._attr_min_temp = 17
        self._attr_max_temp = 30
        self._attr_target_temperature_step = 1

    @property
    def device_info(self) -> DeviceInfo:
        """返回设备信息"""
        return DeviceInfo(
            identifiers={(DOMAIN, f"climate_{self._device.get('id')}")},
            name=self._attr_name,
            manufacturer="Mindor",
            model=f"{self._device.get('typ_spu', 'Unknown Model')} (空调伴侣)",
            sw_version=self._device.get("firmware_ver", "1.0"),
        )

    @property
    def available(self) -> bool:
        """返回设备可用性"""
        if not self.coordinator.data:
            return False

        for device in self.coordinator.data:
            if device.get("id") == self._device.get("id"):
                return device.get("online", False)
        return False

    def _get_current_device_data(self) -> Optional[Dict[str, Any]]:
        """获取当前设备的最新数据"""
        if not self.coordinator.data:
            return None

        for device in self.coordinator.data:
            if device.get("id") == self._device.get("id"):
                return device
        return None

    def _get_act_status_value(
        self, device_data: Dict[str, Any], act_name: str
    ) -> Optional[str]:
        """从device_act_status数组中获取指定act的值"""
        act_status = device_data.get("device_act_status", [])
        for item in act_status:
            if item.get("act") == act_name:
                return item.get("val")
        return None

    def _update_local_device_status(self, act_name: str, new_value: str) -> None:
        """立即更新本地设备状态数据"""
        if not self.coordinator.data:
            return

        # 找到当前设备数据
        for device in self.coordinator.data:
            if device.get("id") == self._device.get("id"):
                # 获取设备状态数组
                act_status = device.get("device_act_status", [])

                # 查找并更新对应的状态项
                for item in act_status:
                    if item.get("act") == act_name:
                        item["val"] = new_value
                        _LOGGER.debug(f"已更新本地设备状态: {act_name} = {new_value}")
                        return

                # 如果没找到对应项，则添加新项
                act_status.append({"act": act_name, "val": new_value})
                _LOGGER.debug(f"已添加本地设备状态: {act_name} = {new_value}")
                break

    @property
    def hvac_mode(self) -> HVACMode:
        """返回当前HVAC模式"""
        device_data = self._get_current_device_data()
        if device_data:
            power = self._get_act_status_value(device_data, "On")
            if power == "00":
                return HVACMode.OFF

            mode = self._get_act_status_value(device_data, "mode")
            mode_mapping = {
                "01": HVACMode.COOL,  # 制冷
                "02": HVACMode.HEAT,  # 制热
                "03": HVACMode.DRY,  # 除湿
                "04": HVACMode.FAN_ONLY,  # 送风
                "05": HVACMode.AUTO,  # 自动
            }
            return mode_mapping.get(mode, HVACMode.AUTO)
        return HVACMode.OFF

    @property
    def current_temperature(self) -> Optional[float]:
        """返回当前环境温度（从传感器读取）"""
        device_data = self._get_current_device_data()
        if device_data:
            # 应该从环境温度传感器字段读取，不是thermoregulation
            # 需要确认API中环境温度的字段名，可能是 "current_temp" 或 "room_temp"
            temp = self._get_act_status_value(
                device_data, "current_temp"
            )  # 需要确认字段名
            if temp is not None:
                try:
                    return float(temp)
                except (ValueError, TypeError):
                    pass
        return None

    @property
    def target_temperature(self) -> Optional[float]:
        """返回目标设定温度"""
        device_data = self._get_current_device_data()
        if device_data:
            target_temp = self._get_act_status_value(device_data, "thermoregulation")
            if target_temp is not None:
                try:
                    return float(target_temp)
                except (ValueError, TypeError):
                    pass
        return None

    # 确保所有控制方法都立即更新状态
    @debounce_command(interval=1.0)
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """设置HVAC模式 - 确保状态立即更新"""
        if hvac_mode == HVACMode.OFF:
            success = await self._send_command("switch", "off")
            if success:
                # 立即更新本地设备状态
                self._update_local_device_status("On", "00")
                # 立即通知UI更新
                self.async_write_ha_state()
                self._first_power_on = True
                _LOGGER.info(f"空调 {self._attr_name} 已关机")
        else:
            # 检查当前电源状态
            device_data = self._get_current_device_data()
            current_power = None
            if device_data:
                current_power = self._get_act_status_value(device_data, "On")

            # 如果需要开机，先发送开机命令
            is_first_power_on = False
            if current_power == "00":
                power_success = await self._send_command("switch", "on")
                if power_success:
                    # 立即更新本地电源状态
                    self._update_local_device_status("On", "01")
                    _LOGGER.info(f"空调 {self._attr_name} 开机成功")
                    # 标记这是第一次开机
                    is_first_power_on = self._first_power_on
                    self._first_power_on = False
            else:
                power_success = True

            if power_success:
                # 如果是第一次开机，延迟1秒再发送模式指令
                if is_first_power_on:
                    _LOGGER.info(
                        f"空调 {self._attr_name} 第一次开机，延迟1秒发送模式指令"
                    )
                    await asyncio.sleep(1)

                # 设置模式
                mode_mapping = {
                    HVACMode.COOL: "01",
                    HVACMode.HEAT: "02",
                    HVACMode.DRY: "03",
                    HVACMode.FAN_ONLY: "04",
                    HVACMode.AUTO: "05",
                }
                mode_val = mode_mapping.get(hvac_mode, "05")
                success = await self._send_command("mode", mode_val)

                if success:
                    # 立即更新本地模式状态
                    self._update_local_device_status("mode", mode_val)
                    # 立即通知UI更新
                    self.async_write_ha_state()
                    _LOGGER.info(f"空调 {self._attr_name} 模式已设置为 {hvac_mode}")
            else:
                success = False

        # 无论成功失败，都要确保UI状态正确
        if not success:
            _LOGGER.error(f"空调 {self._attr_name} 模式设置失败")
            # 失败时也要刷新状态，确保UI显示正确
            self.async_write_ha_state()

    @debounce_command(interval=1.0)
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """设置目标温度 - 确保状态立即更新"""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        # 确保温度在有效范围内
        temperature = max(
            self._attr_min_temp, min(self._attr_max_temp, int(temperature))
        )

        success = await self._send_command("temp", str(temperature))

        if success:
            # 立即更新本地温度状态
            self._update_local_device_status("thermoregulation", str(temperature))
            # 立即通知UI更新
            self.async_write_ha_state()
            _LOGGER.info(f"空调 {self._attr_name} 目标温度已设置为 {temperature}°C")
        else:
            _LOGGER.error(f"空调 {self._attr_name} 温度设置失败")
            # 失败时也要刷新状态
            self.async_write_ha_state()

    @debounce_command(interval=1.0)
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """设置风速模式 - 优化版本"""
        fan_mapping = {
            FAN_AUTO: "00",
            FAN_LOW: "01",
            FAN_MEDIUM: "02",
            FAN_HIGH: "03",
        }
        wind_gear = fan_mapping.get(fan_mode)
        if wind_gear:
            # 发送命令到设备
            success = await self._send_command("speed", wind_gear)

            if success:
                # 只在命令成功后更新一次状态，让WebSocket处理实际的状态同步
                _LOGGER.info(f"空调 {self._attr_name} 风速已设置为 {fan_mode}")
                # 移除立即的本地状态更新，依赖WebSocket推送
            else:
                _LOGGER.error(f"空调 {self._attr_name} 风速设置失败")
                # 失败时刷新状态确保UI正确
                self.async_write_ha_state()

    @debounce_command(interval=1.0)
    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """设置摆风模式 - 优化版本"""
        swing_mapping = {
            "关闭扫风": "00",
            "上下扫风": "01",
            "左右扫风": "02",
        }

        swing_val = swing_mapping.get(swing_mode, "00")
        success = await self._send_command("van", swing_val)

        if success:
            _LOGGER.info(f"空调 {self._attr_name} 摆风模式已设置为 {swing_mode}")
            # 移除立即的本地状态更新和后台刷新，依赖WebSocket推送
        else:
            _LOGGER.error(f"空调 {self._attr_name} 摆风设置失败")
            self.async_write_ha_state()

    @debounce_command(interval=1.0)
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """设置目标温度 - 优化版本"""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        temperature = max(
            self._attr_min_temp, min(self._attr_max_temp, int(temperature))
        )

        success = await self._send_command("temp", str(temperature))

        if success:
            _LOGGER.info(f"空调 {self._attr_name} 目标温度已设置为 {temperature}°C")
            # 移除立即的本地状态更新，依赖WebSocket推送
        else:
            _LOGGER.error(f"空调 {self._attr_name} 温度设置失败")
            self.async_write_ha_state()

    async def _send_command(self, act: str, val: str) -> bool:
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
                "act": act,
                "val": val,
            }

            _LOGGER.debug(f"发送空调命令 {act}={val} 到设备 {device_id}")
            _LOGGER.debug(f"请求数据: {request_data}")

            # 发送 API 请求
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{API_BASE}/md_openapi/home_assistant/ac_ctrl",
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
                        _LOGGER.info(f"空调设备 {device_id} 命令 {act}={val} 执行成功")
                        return True
                    else:
                        error_msg = response_data.get("msg", "未知错误")
                        _LOGGER.error(f"空调设备控制失败: {error_msg}")
                        return False

        except aiohttp.ClientError as e:
            _LOGGER.error(f"网络请求错误: {e}")
            return False
        except Exception as e:
            _LOGGER.error(f"发送空调命令失败: {e}")
            return False

    @property
    def fan_mode(self) -> Optional[str]:
        """返回风速模式"""
        device_data = self._get_current_device_data()
        if device_data:
            wind_gear = self._get_act_status_value(device_data, "windGear")
            fan_mapping = {
                "00": FAN_AUTO,
                "01": FAN_LOW,
                "02": FAN_MEDIUM,
                "03": FAN_HIGH,
                # 兼容单位数字格式
                "0": FAN_AUTO,
                "1": FAN_LOW,
                "2": FAN_MEDIUM,
                "3": FAN_HIGH,
            }
            return fan_mapping.get(wind_gear, FAN_AUTO)
        return FAN_AUTO

    @property
    def swing_mode(self) -> Optional[str]:
        """返回摆风模式"""
        device_data = self._get_current_device_data()
        if device_data:
            air_swing = self._get_act_status_value(device_data, "airSwing")
            swing_mapping = {
                "00": "关闭扫风",
                "01": "上下扫风",
                "02": "左右扫风",
            }
            return swing_mapping.get(air_swing, "关闭扫风")
        return "关闭扫风"
