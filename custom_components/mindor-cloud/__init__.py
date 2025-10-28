from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, PLATFORMS
from .coordinator import MindorDataUpdateCoordinator
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.area_registry import async_get as async_get_area_registry
import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """设置配置条目"""
    coordinator = MindorDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # 自动创建区域并绑定设备
    await _setup_areas_and_devices(hass, entry, coordinator)

    # 加载实体平台
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # 启动WebSocket连接
    if entry.data.get("enable_websocket", True):
        _LOGGER.info("正在启动WebSocket连接...")
        await coordinator._setup_websocket()
    else:
        _LOGGER.info("WebSocket连接已禁用，使用轮询模式")

    return True


async def _setup_areas_and_devices(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: MindorDataUpdateCoordinator
):
    """设置区域和设备"""
    area_registry = async_get_area_registry(hass)
    device_registry = async_get_device_registry(hass)

    # 获取设备列表
    devices = coordinator.devices or []
    _LOGGER.info(f"开始处理 {len(devices)} 个设备的区域分配")

    for device in devices:
        try:
            device_id = device.get("id") or device.get("device_id")
            area_name = device.get("areable_name", "默认房间").strip()
            device_name = device.get("name", f"设备_{device_id}")

            if not device_id:
                _LOGGER.warning(f"设备 {device_name} 缺少设备ID，跳过处理")
                continue

            # 查找或创建区域
            area = area_registry.async_get_area_by_name(area_name)
            if not area:
                _LOGGER.info(f"创建新区域: {area_name}")
                area = area_registry.async_create(name=area_name)
            else:
                _LOGGER.debug(f"使用现有区域: {area_name}")

            # 查找设备并更新区域
            # 尝试多种设备标识符格式
            device_identifiers = [
                (DOMAIN, str(device_id)),
                (DOMAIN, f"socket_{device_id}"),
                (DOMAIN, f"socket_power_{device_id}"),
            ]

            device_found = False
            for identifier in device_identifiers:
                device_entry = device_registry.async_get_device(
                    identifiers={identifier}
                )
                if device_entry:
                    if device_entry.area_id != area.id:
                        _LOGGER.info(f"将设备 {device_name} 分配到区域 {area_name}")
                        device_registry.async_update_device(
                            device_entry.id, area_id=area.id
                        )
                    else:
                        _LOGGER.debug(f"设备 {device_name} 已在正确区域 {area_name}")
                    device_found = True
                    break

            if not device_found:
                _LOGGER.debug(
                    f"设备 {device_name} 尚未创建，将在实体创建时自动分配区域"
                )

        except Exception as e:
            _LOGGER.error(f"处理设备区域分配时出错: {e}")
            continue

    _LOGGER.info("设备区域分配处理完成")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载配置条目"""
    # 获取coordinator并清理WebSocket连接
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if hasattr(coordinator, 'async_shutdown'):
        await coordinator.async_shutdown()
    
    # 卸载平台
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok
