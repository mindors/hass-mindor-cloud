from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    CoverDeviceClass,
    ATTR_POSITION,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, CURTAIN_LIST, API_BASE
from .utils import debounce_command
from .request_config import RequestConfig
import aiohttp
import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """设置窗帘实体"""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    devices = coordinator.devices
    entities = []

    for dev in devices:
        device_type = dev.get("typ_spu")
        if device_type in CURTAIN_LIST:
            entities.append(MindorCurtainEntity(coordinator, dev))
            _LOGGER.info(f"添加窗帘设备: {dev.get('name', '未知设备')} ({device_type})")

    if entities:
        async_add_entities(entities)
        _LOGGER.info(f"成功添加 {len(entities)} 个窗帘设备")


class MindorCurtainEntity(CoverEntity):
    """Mindor窗帘实体"""

    def __init__(self, coordinator, device_data):
        """初始化窗帘实体"""
        self._coordinator = coordinator
        self._device_data = device_data
        self._device_id = device_data.get("device_id") or device_data.get("id")
        self._name = device_data.get("name", f"窗帘_{self._device_id}")
        self._unique_id = f"mindor_curtain_{self._device_id}"

        # 窗帘状态
        self._current_position = 0  # 当前位置 (0-100)
        self._target_position = 0  # 目标位置 (0-100)
        self._is_opening = False  # 是否正在开启
        self._is_closing = False  # 是否正在关闭
        self._is_closed = True  # 是否关闭

        # 从设备数据初始化状态
        self._update_from_device_data()

    def _update_from_device_data(self):
        """从设备数据更新状态"""
        try:
            act_status = self._device_data.get("device_act_status", [])

            for status in act_status:
                if status.get("act") == "curtain_percent":
                    self._current_position = (
                        int(status.get("val", 0))
                        if str(status.get("val", 0)).isdigit()
                        else 0
                    )
                    self._is_closed = self._current_position == 0
                    break
            else:
                # 如果没有找到curtain_percent，设置默认值
                self._current_position = 0 if self._is_closed else 100

        except Exception as e:
            _LOGGER.error(f"更新窗帘 {self._device_id} 状态时出错: {e}")

    @property
    def name(self):
        """返回设备名称"""
        return self._name

    @property
    def unique_id(self):
        """返回唯一ID"""
        return self._unique_id

    @property
    def device_class(self):
        """返回设备类别"""
        return CoverDeviceClass.CURTAIN

    @property
    def supported_features(self):
        """返回支持的功能"""
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

    @property
    def current_cover_position(self):
        """返回当前位置 (0=关闭, 100=完全打开)"""
        return self._current_position

    @property
    def is_closed(self):
        """返回是否关闭"""
        return self._current_position == 0

    @property
    def is_opening(self):
        """返回是否正在开启"""
        return self._is_opening

    @property
    def is_closing(self):
        """返回是否正在关闭"""
        return self._is_closing

    @property
    def available(self):
        """返回设备是否可用"""
        return self._device_data.get("online", False)

    @property
    def device_info(self):
        """返回设备信息"""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._name,
            manufacturer="Mindor",
            model=self._device_data.get("type", "WCT001"),
            sw_version=self._device_data.get("version", "1.0"),
        )

    def _update_local_device_status(self, act_type: str, value: str):
        """更新本地设备状态"""
        try:
            # 确保device_act_status存在
            if "device_act_status" not in self._device_data:
                self._device_data["device_act_status"] = []

            act_status = self._device_data["device_act_status"]

            # 查找并更新现有状态项
            for item in act_status:
                if item.get("act") == act_type:
                    item["val"] = value
                    _LOGGER.debug(
                        f"窗帘 {self._device_id} 本地状态更新: {act_type} = {value}"
                    )
                    return

            # 如果没找到对应项，则添加新项
            act_status.append({"act": act_type, "val": value})
            _LOGGER.debug(f"窗帘 {self._device_id} 添加本地状态: {act_type} = {value}")

        except Exception as e:
            _LOGGER.error(f"更新窗帘 {self._device_id} 本地状态时出错: {e}")

    @debounce_command()
    async def async_open_cover(self, **kwargs):
        """打开窗帘"""
        _LOGGER.info(f"开始打开窗帘 {self._device_id}")

        # 立即更新本地状态
        self._is_opening = True
        self._is_closing = False
        self.async_write_ha_state()

        # 发送控制命令
        success = await self._send_command("percent", "100")
        if not success:
            self._is_opening = False
            self.async_write_ha_state()
        if success:
            # 预估完全打开需要的时间，更新最终状态
            self._current_position = 100
            self._target_position = 100
            self._is_opening = False
            self._is_closed = False
            self._update_local_device_status("current_position", "100")
            self._update_local_device_status("motor_status", "stop")
            self._update_local_device_status("curtain_state", "open")
        else:
            # 命令失败，恢复状态
            self._is_opening = False

        self.async_write_ha_state()

        # 触发后台数据刷新
        await self._coordinator.async_request_refresh()

    @debounce_command()
    async def async_close_cover(self, **kwargs):
        """关闭窗帘"""
        _LOGGER.info(f"开始关闭窗帘 {self._device_id}")
        self._is_closing = True
        self._is_opening = False
        self.async_write_ha_state()

        success = await self._send_command("percent", "0")
        if not success:
            self._is_closing = False
            self.async_write_ha_state()

        # 发送控制命令
        success = await self._send_command("percent", 0)
        if success:
            # 预估完全关闭需要的时间，更新最终状态
            self._current_position = 0
            self._target_position = 0
            self._is_closing = False
            self._is_closed = True
            self._update_local_device_status("current_position", "0")
            self._update_local_device_status("motor_status", "stop")
            self._update_local_device_status("curtain_state", "close")
        else:
            # 命令失败，恢复状态
            self._is_closing = False

        self.async_write_ha_state()

        # 触发后台数据刷新
        await self._coordinator.async_request_refresh()

    @debounce_command()
    async def async_stop_cover(self, **kwargs):
        """停止窗帘"""
        _LOGGER.info(f"停止窗帘 {self._device_id}")

        # 立即更新本地状态
        self._is_opening = False
        self._is_closing = False
        self._update_local_device_status("motor_status", "stop")
        self.async_write_ha_state()

        # 发送停止命令
        success = await self._send_command("stop", "")
        if not success:
            _LOGGER.error(f"窗帘 {self._device_id} 停止命令发送失败")

        # 触发后台数据刷新
        await self._coordinator.async_request_refresh()

    @debounce_command()
    async def async_set_cover_position(self, **kwargs):
        """设置窗帘位置"""
        position = kwargs.get(ATTR_POSITION, 0)
        _LOGGER.info(f"设置窗帘 {self._device_id} 位置为 {position}%")

        # 立即更新本地状态
        self._target_position = position
        if position > self._current_position:
            self._is_opening = True
            self._is_closing = False
            self._update_local_device_status("motor_status", "opening")
        elif position < self._current_position:
            self._is_opening = False
            self._is_closing = True
            self._update_local_device_status("motor_status", "closing")

        self._update_local_device_status("target_position", str(position))
        self.async_write_ha_state()

        # 发送位置控制命令
        success = await self._send_command("percent", str(position))
        if success:
            # 预估到达目标位置，更新最终状态
            self._current_position = position
            self._is_opening = False
            self._is_closing = False
            self._is_closed = position == 0
            self._update_local_device_status("current_position", str(position))
            self._update_local_device_status("motor_status", "stop")
            self._update_local_device_status(
                "curtain_state",
                "close" if position == 0 else "open" if position == 100 else "partial",
            )
        else:
            # 命令失败，恢复状态
            self._is_opening = False
            self._is_closing = False

        self.async_write_ha_state()

        # 触发后台数据刷新
        await self._coordinator.async_request_refresh()

    async def _send_command(self, act: str, val: str) -> bool:
        """发送窗帘控制命令"""
        try:
            token = self._coordinator.token
            device_id = self._device_id

            if not device_id:
                _LOGGER.error("设备ID为空，无法发送命令")
                return False

            request_config = RequestConfig()
            opt = request_config.get_opt()
            sign = request_config.generate_sign(opt)

            headers = {
                "Content-Type": "application/json",
                "Authorization": token,
                "Sign": sign,
                **{str(k): str(v) for k, v in opt.items()},
            }

            request_data = {"device_id": device_id, "act": act}
            if act != "stop":
                request_data["val"] = val

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{API_BASE}/md_openapi/home_assistant/curtain_ctrl",
                    json=request_data,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.error(f"API请求失败，状态码: {resp.status}")
                        return False

                    response_data = await resp.json()
                    success = response_data.get("errcode") == 0

                    if success:
                        _LOGGER.info(f"窗帘设备 {device_id} 命令 {act}={val} 执行成功")
                    else:
                        _LOGGER.error(
                            f"窗帘设备控制失败: {response_data.get('msg', '未知错误')}"
                        )

                    return success

        except Exception as e:
            _LOGGER.error(f"发送窗帘命令失败: {e}")
            return False

    @callback
    def _handle_coordinator_update(self) -> None:
        """处理协调器更新"""
        # 更新设备数据
        for device in self._coordinator.devices:
            if (
                device.get("device_id") == self._device_id
                or device.get("id") == self._device_id
            ):
                self._device_data = device
                self._update_from_device_data()
                break

        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """实体添加到Home Assistant时调用"""
        await super().async_added_to_hass()
        # 注册协调器更新回调
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    async def async_update(self):
        """更新实体状态"""
        # 从协调器获取最新数据
        await self._coordinator.async_request_refresh()
