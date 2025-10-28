import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.const import (
    UnitOfPower,
    UnitOfEnergy,
)

from .const import DOMAIN, SOCKET_POWER_LIST
from .coordinator import MindorDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# 存储相关常量
ENERGY_STORAGE_VERSION = 1
ENERGY_STORAGE_KEY = "mindor_energy_data"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置传感器实体"""
    coordinator: MindorDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    # 获取设备列表
    devices = coordinator.data if isinstance(coordinator.data, list) else []
    _LOGGER.debug(f"获取到 {len(devices)} 个设备用于传感器设置")

    entities = []

    for device in devices:
        device_type = device.get("typ_spu", "")
        if device_type in SOCKET_POWER_LIST:
            _LOGGER.debug(f"为电量插座创建传感器: {device.get('name')}")

            # 创建功率传感器
            entities.append(MindorPowerSensor(coordinator, device))

            # 创建今日用电量传感器
            entities.append(MindorTodayEnergySensor(coordinator, device))

            # 创建本月用电量传感器
            entities.append(MindorMonthEnergySensor(coordinator, device))

    if entities:
        async_add_entities(entities, True)
        _LOGGER.info(f"成功添加 {len(entities)} 个传感器实体")
    else:
        _LOGGER.warning("未找到支持的电量插座设备")


class MindorPowerSensorBase(CoordinatorEntity, SensorEntity):
    """Mindor电量传感器基类"""

    def __init__(
        self, coordinator: MindorDataUpdateCoordinator, device: Dict[str, Any]
    ):
        super().__init__(coordinator)
        self._device = device
        self._device_id = device.get("id", "unknown")
        self._device_name = device.get("name", "Unknown Socket")

    @property
    def device_info(self) -> DeviceInfo:
        """返回设备信息"""
        return DeviceInfo(
            identifiers={(DOMAIN, f"socket_power_{self._device_id}")},
            name=f"{self._device_name} (电量版)",
            manufacturer="Mindor",
            model=f"{self._device.get('typ_spu', 'Unknown Model')} (Power)",
            sw_version=self._device.get("firmware_ver", "1.0"),
        )

    @property
    def available(self) -> bool:
        """返回传感器可用性"""
        if not self.coordinator.data:
            return False

        # 检查设备是否在线
        for device in self.coordinator.data:
            if device.get("id") == self._device_id:
                return device.get("online", False)
        return False

    def _get_current_device_data(self) -> Optional[Dict[str, Any]]:
        """获取当前设备的最新数据"""
        if not self.coordinator.data:
            return None

        for device in self.coordinator.data:
            if device.get("id") == self._device_id:
                return device
        return None


class MindorPowerSensor(MindorPowerSensorBase):
    """功率传感器"""

    def __init__(
        self, coordinator: MindorDataUpdateCoordinator, device: Dict[str, Any]
    ):
        super().__init__(coordinator, device)
        self._attr_name = f"{self._device_name} 功率"
        self._attr_unique_id = f"mindor_power_{self._device_id}"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_icon = "mdi:flash"

    @property
    def native_value(self) -> Optional[float]:
        """返回功率值"""
        device_data = self._get_current_device_data()
        if device_data:
            # 从device_act_status数组中查找power值
            device_act_status = device_data.get("device_act_status", [])
            if device_act_status:
                for status_item in device_act_status:
                    if (
                        isinstance(status_item, dict)
                        and status_item.get("act") == "power"
                    ):
                        power_val = status_item.get("val")
                        if power_val is not None:
                            try:
                                return float(power_val)
                            except (ValueError, TypeError):
                                pass
        return None


class MindorTodayEnergySensor(MindorPowerSensorBase):
    """今日用电量传感器 - 基于实时功率计算"""

    def __init__(
        self, coordinator: MindorDataUpdateCoordinator, device: Dict[str, Any]
    ):
        super().__init__(coordinator, device)
        self._attr_name = f"{self._device_name} 今日用电量"
        self._attr_unique_id = f"mindor_energy_today_{self._device_id}"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:counter"

        # 初始化存储（拆分为独立的today键）
        self._store = Store(
            coordinator.hass,
            ENERGY_STORAGE_VERSION,
            f"{ENERGY_STORAGE_KEY}_today_{self._device_id}",
        )
        self._energy_data = None
        self._last_power = None
        self._last_update_time = None

        # 加载历史数据
        coordinator.hass.async_create_task(self._load_energy_data())

    async def _load_energy_data(self):
        """加载历史用电量数据"""
        try:
            data = await self._store.async_load()
            if data:
                self._energy_data = data
                _LOGGER.debug(f"加载设备 {self._device_id} 的今日用电量数据: {data}")
            else:
                # 兼容旧版合并存储，尝试从旧键读取
                legacy_store = Store(
                    self.coordinator.hass,
                    ENERGY_STORAGE_VERSION,
                    f"{ENERGY_STORAGE_KEY}_{self._device_id}",
                )
                legacy = await legacy_store.async_load()
                if legacy:
                    self._energy_data = {
                        "today_energy": float(legacy.get("today_energy", 0.0)),
                        "last_reset_date": legacy.get(
                            "last_reset_date", datetime.now().date().isoformat()
                        ),
                    }
                    _LOGGER.info(
                        f"从旧存储迁移今日用电量: {self._device_id} -> {self._energy_data}"
                    )
                else:
                    self._energy_data = {
                        "today_energy": 0.0,
                        "last_reset_date": datetime.now().date().isoformat(),
                    }
        except Exception as e:
            _LOGGER.error(f"加载今日用电量数据失败: {e}")
            self._energy_data = {
                "today_energy": 0.0,
                "last_reset_date": datetime.now().date().isoformat(),
            }

    async def _save_energy_data(self):
        """保存用电量数据"""
        try:
            await self._store.async_save(self._energy_data)
        except Exception as e:
            _LOGGER.error(f"保存用电量数据失败: {e}")

    def _calculate_energy_increment(self, current_power: float) -> float:
        """计算用电量增量"""
        if self._last_power is None or self._last_update_time is None:
            self._last_power = current_power
            self._last_update_time = datetime.now()
            return 0.0

        current_time = datetime.now()
        time_diff_hours = (current_time - self._last_update_time).total_seconds() / 3600

        # 使用平均功率计算用电量：(上次功率 + 当前功率) / 2 * 时间间隔
        avg_power = (self._last_power + current_power) / 2
        energy_increment = avg_power * time_diff_hours / 1000  # 转换为kWh

        self._last_power = current_power
        self._last_update_time = current_time

        return energy_increment

    def _check_and_reset_daily(self):
        """检查并重置日用电量"""
        if not self._energy_data:
            return

        current_date = datetime.now().date().isoformat()
        if self._energy_data.get("last_reset_date") != current_date:
            _LOGGER.info(f"设备 {self._device_id} 重置今日用电量")
            self._energy_data["today_energy"] = 0.0
            self._energy_data["last_reset_date"] = current_date

    @property
    def native_value(self) -> Optional[float]:
        """返回今日用电量"""
        if not self._energy_data:
            return None

        # 检查是否需要重置
        self._check_and_reset_daily()

        # 获取当前功率
        device_data = self._get_current_device_data()
        if device_data:
            device_act_status = device_data.get("device_act_status", [])
            current_power = None

            for status_item in device_act_status:
                if isinstance(status_item, dict) and status_item.get("act") == "power":
                    power_val = status_item.get("val")
                    if power_val is not None:
                        try:
                            current_power = float(power_val)
                            break
                        except (ValueError, TypeError):
                            pass

            if current_power is not None:
                # 计算用电量增量
                energy_increment = self._calculate_energy_increment(current_power)
                if energy_increment > 0:
                    self._energy_data["today_energy"] += energy_increment
                    # 异步保存数据
                    self.hass.async_create_task(self._save_energy_data())
                    _LOGGER.debug(
                        f"设备 {self._device_id} 今日用电量增加 {energy_increment:.6f} kWh，总计 {self._energy_data['today_energy']:.3f} kWh"
                    )

        return round(self._energy_data.get("today_energy", 0.0), 3)


class MindorMonthEnergySensor(MindorPowerSensorBase):
    """本月用电量传感器 - 基于实时功率计算"""

    def __init__(
        self, coordinator: MindorDataUpdateCoordinator, device: Dict[str, Any]
    ):
        super().__init__(coordinator, device)
        self._attr_name = f"{self._device_name} 本月用电量"
        self._attr_unique_id = f"mindor_energy_month_{self._device_id}"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:calendar-month"

        # 初始化存储（拆分为独立的month键）
        self._store = Store(
            coordinator.hass,
            ENERGY_STORAGE_VERSION,
            f"{ENERGY_STORAGE_KEY}_month_{self._device_id}",
        )
        self._energy_data = None
        self._last_power = None
        self._last_update_time = None

        # 加载历史数据
        coordinator.hass.async_create_task(self._load_energy_data())

    async def _load_energy_data(self):
        """加载历史用电量数据"""
        try:
            data = await self._store.async_load()
            if data:
                self._energy_data = data
            else:
                # 兼容旧版合并存储，尝试从旧键读取
                legacy_store = Store(
                    self.coordinator.hass,
                    ENERGY_STORAGE_VERSION,
                    f"{ENERGY_STORAGE_KEY}_{self._device_id}",
                )
                legacy = await legacy_store.async_load()
                if legacy:
                    self._energy_data = {
                        "month_energy": float(legacy.get("month_energy", 0.0)),
                        "last_reset_month": legacy.get(
                            "last_reset_month", datetime.now().strftime("%Y-%m")
                        ),
                    }
                    _LOGGER.info(
                        f"从旧存储迁移本月用电量: {self._device_id} -> {self._energy_data}"
                    )
                else:
                    self._energy_data = {
                        "month_energy": 0.0,
                        "last_reset_month": datetime.now().strftime("%Y-%m"),
                    }
        except Exception as e:
            _LOGGER.error(f"加载本月用电量数据失败: {e}")
            self._energy_data = {
                "month_energy": 0.0,
                "last_reset_month": datetime.now().strftime("%Y-%m"),
            }

    async def _save_energy_data(self):
        """保存用电量数据"""
        try:
            await self._store.async_save(self._energy_data)
        except Exception as e:
            _LOGGER.error(f"保存用电量数据失败: {e}")

    def _calculate_energy_increment(self, current_power: float) -> float:
        """计算用电量增量"""
        if self._last_power is None or self._last_update_time is None:
            self._last_power = current_power
            self._last_update_time = datetime.now()
            return 0.0

        current_time = datetime.now()
        time_diff_hours = (current_time - self._last_update_time).total_seconds() / 3600

        # 使用平均功率计算用电量
        avg_power = (self._last_power + current_power) / 2
        energy_increment = avg_power * time_diff_hours / 1000  # 转换为kWh

        self._last_power = current_power
        self._last_update_time = current_time

        return energy_increment

    def _check_and_reset_monthly(self):
        """检查并重置月用电量"""
        if not self._energy_data:
            return

        current_month = datetime.now().strftime("%Y-%m")
        if self._energy_data.get("last_reset_month") != current_month:
            _LOGGER.info(f"设备 {self._device_id} 重置本月用电量")
            self._energy_data["month_energy"] = 0.0
            self._energy_data["last_reset_month"] = current_month

    @property
    def native_value(self) -> Optional[float]:
        """返回本月用电量"""
        if not self._energy_data:
            return None

        # 检查是否需要重置
        self._check_and_reset_monthly()

        # 获取当前功率
        device_data = self._get_current_device_data()
        if device_data:
            device_act_status = device_data.get("device_act_status", [])
            current_power = None

            for status_item in device_act_status:
                if isinstance(status_item, dict) and status_item.get("act") == "power":
                    power_val = status_item.get("val")
                    if power_val is not None:
                        try:
                            current_power = float(power_val)
                            break
                        except (ValueError, TypeError):
                            pass

            if current_power is not None:
                # 计算用电量增量
                energy_increment = self._calculate_energy_increment(current_power)
                if energy_increment > 0:
                    self._energy_data["month_energy"] += energy_increment
                    # 异步保存数据
                    self.hass.async_create_task(self._save_energy_data())
                    _LOGGER.debug(
                        f"设备 {self._device_id} 本月用电量增加 {energy_increment:.6f} kWh，总计 {self._energy_data['month_energy']:.3f} kWh"
                    )

        return round(self._energy_data.get("month_energy", 0.0), 3)
