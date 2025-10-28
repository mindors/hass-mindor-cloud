from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
import aiohttp
import voluptuous as vol
from .const import DOMAIN, API_BASE
from .request_config import RequestConfig
import logging

_LOGGER = logging.getLogger(__name__)


class MindorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required("phone"): str,
                    }
                ),
            )

        phone = user_input["phone"]
        req = RequestConfig()

        # 生成 opt 和签名
        opt = req.get_opt()
        sign = req.generate_sign(opt)
        headers = {"Sign": sign, "Content-Type": "application/json"}
        opt_str = {str(k): str(v) for k, v in opt.items()}
        merged_headers = {
            **dict(headers),
            **opt_str,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/md_openapi/home_assistant/login",
                json={"mobile": phone},
                headers=merged_headers,
            ) as resp:
                if resp.status != 200:
                    return self.async_abort(reason="Login failed")
                data = await resp.json()
                _LOGGER.warning(f"Login Response: {data}")
                if data.get("errcode") != 0:
                    return self.async_abort(reason=data.get("msg"))
                token = data["token"]

            opt2 = req.get_opt()
            sign2 = req.generate_sign(opt2)
            headers2 = {
                "Authorization": token,
                "Sign": sign2,
            }
            opt_str2 = {str(k): str(v) for k, v in opt2.items()}
            merged_headers2 = {
                **dict(headers2),
                **opt_str2,
            }
            async with session.get(
                f"{API_BASE}/md_openapi/home_assistant/devices",
                headers=merged_headers2,
            ) as resp:
                devices = await resp.json()
                _LOGGER.warning(f"Devices: {devices}")
                if devices.get("errcode") != 0:
                    return self.async_abort(reason=devices.get("msg"))
                _LOGGER.debug(f"设备列表: {devices}")
                devices = devices["records"]
        return self.async_create_entry(
            title=f"Mindor User {phone}",
            data={
                "phone": phone,
                "token": token,
                "devices": devices,
                "user_id": data["user_id"],
                "enable_websocket": True,
            },
        )
