# -*- coding: utf-8 -*-
"""
统一日志配置模块

功能：
- 统一日志格式和级别配置
- 支持从配置文件/环境变量加载日志设置
- 避免日志处理器重复或配置冲突

用法：
    from Dumplings.logging_config import logger, setup_logging

    # 使用默认配置
    logger = setup_logging()

    # 或自定义配置
    logger = setup_logging(
        log_dir="custom_logs",
        rotation="100 MB",
        retention="7 days",
        level="DEBUG"
    )

    # 使用日志
    logger.info("这是一条日志")
"""
import os
import sys
from loguru import logger
from typing import Optional


# 移除默认的默认处理器（避免重复）
logger.remove()


def setup_logging(
    log_dir: str = "logs",
    rotation: str = "500 MB",
    retention: str = "10 days",
    level: Optional[str] = None,
    console_format: Optional[str] = None,
    add_console_handler: bool = True
) -> logger:
    """
    统一日志配置

    Args:
        log_dir: 日志目录，默认 "logs"
        rotation: 日志轮转大小，默认 "500 MB"
        retention: 日志保留天数，默认 "10 days"
        level: 日志级别，默认从环境变量 LOGURU_LEVEL 读取，其次为 "INFO"
        console_format: 控制台日志格式，默认使用预设格式
        add_console_handler: 是否添加控制台处理器，默认 True

    Returns:
        logger: 配置后的 loguru logger 实例

    Example:
        >>> logger = setup_logging(level="DEBUG")
        >>> logger.info("应用启动")
    """
    # 确定日志级别
    if level is None:
        level = os.getenv("LOGURU_LEVEL", "INFO")

    # 创建日志目录
    os.makedirs(log_dir, exist_ok=True)

    # 默认控制台格式
    if console_format is None:
        console_format = (
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )

    # 添加文件处理器
    logger.add(
        os.path.join(log_dir, "app.log"),
        rotation=rotation,
        retention=retention,
        compression="zip",
        level=level,
        backtrace=True,
        diagnose=True,
        encoding="utf-8"
    )

    # 添加控制台处理器（可选）
    if add_console_handler:
        logger.add(
            sys.stderr,
            level=level,
            format=console_format,
            colorize=True
        )

    return logger


def get_logger(name: Optional[str] = None) -> logger:
    """
    获取logger 实例

    Args:
        name: logger 名称，可选

    Returns:
        logger: loguru logger 实例

    Example:
        >>> logger = get_logger("my_module")
        >>> logger.info("模块日志")
    """
    if name:
        return logger.bind(name=name)
    return logger


def remove_handlers() -> None:
    """
    移除所有日志处理器
    用于重新配置或清理
    """
    logger.remove()


# 自动初始化（如果 LOGURU_DISABLED 未设置）
if not os.getenv("LOGURU_DISABLED"):
    setup_logging()


# 导出 logger
__all__ = ["logger", "setup_logging", "get_logger", "remove_handlers"]