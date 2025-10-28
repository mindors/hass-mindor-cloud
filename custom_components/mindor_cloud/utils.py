import asyncio
import time
from functools import wraps
from typing import Dict, Any, Callable
import logging

_LOGGER = logging.getLogger(__name__)


class CommandDebouncer:
    """命令防抖器类，防止频繁调用API"""

    def __init__(self, interval: float = 0.3):
        """初始化防抖器

        Args:
            interval: 防抖间隔时间（秒）
        """
        self.interval = interval
        self._last_command_time: Dict[str, float] = {}
        self._is_processing: Dict[str, bool] = {}

    def can_execute_command(self, entity_id: str) -> bool:
        """检查是否可以执行命令

        Args:
            entity_id: 实体ID，用于区分不同的实体

        Returns:
            bool: 是否可以执行命令
        """
        current_time = time.time()
        last_time = self._last_command_time.get(entity_id, 0)
        is_processing = self._is_processing.get(entity_id, False)

        # 如果正在处理中，拒绝执行
        if is_processing:
            _LOGGER.debug(f"Entity {entity_id}: 命令正在处理中，跳过")
            return False

        # 检查时间间隔
        if current_time - last_time < self.interval:
            _LOGGER.debug(
                f"Entity {entity_id}: 防抖限制，距离上次命令仅 {current_time - last_time:.2f}s"
            )
            return False

        return True

    def mark_command_start(self, entity_id: str):
        """标记命令开始执行"""
        self._last_command_time[entity_id] = time.time()
        self._is_processing[entity_id] = True
        _LOGGER.debug(f"Entity {entity_id}: 命令开始执行")

    def mark_command_end(self, entity_id: str):
        """标记命令执行结束"""
        self._is_processing[entity_id] = False
        _LOGGER.debug(f"Entity {entity_id}: 命令执行结束")

    def reset_entity(self, entity_id: str):
        """重置实体的防抖状态"""
        self._last_command_time.pop(entity_id, None)
        self._is_processing.pop(entity_id, None)
        _LOGGER.debug(f"Entity {entity_id}: 防抖状态已重置")


# 全局防抖器实例
_global_debouncer = CommandDebouncer()


def debounce_command(interval: float = 1.0, use_global: bool = True):
    """防抖装饰器

    Args:
        interval: 防抖间隔时间（秒）
        use_global: 是否使用全局防抖器
    """

    def decorator(func: Callable) -> Callable:
        debouncer = _global_debouncer if use_global else CommandDebouncer(interval)

        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            entity_id = getattr(self, "entity_id", str(id(self)))

            # 检查是否可以执行
            if not debouncer.can_execute_command(entity_id):
                _LOGGER.warning(f"Entity {entity_id}: 命令被防抖限制，请稍后再试")
                return

            # 标记开始执行
            debouncer.mark_command_start(entity_id)

            try:
                # 执行原函数
                result = await func(self, *args, **kwargs)
                return result
            except Exception as e:
                _LOGGER.error(f"Entity {entity_id}: 命令执行失败: {e}")
                raise
            finally:
                # 标记执行结束
                debouncer.mark_command_end(entity_id)

        return wrapper

    return decorator


def get_global_debouncer() -> CommandDebouncer:
    """获取全局防抖器实例"""
    return _global_debouncer


def create_debouncer(interval: float = 1.0) -> CommandDebouncer:
    """创建新的防抖器实例"""
    return CommandDebouncer(interval)
