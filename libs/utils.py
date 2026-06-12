"""
相机EXIF分析插件 — 工具函数模块
包含 GPS 坐标转换、标签值清理、分数格式化、RAW 预览提取等通用工具函数。
"""

from __future__ import annotations

import asyncio
import math
import os
import uuid
from typing import Any

try:
    from PIL import Image as PILImage

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import rawpy

    HAS_RAWPY = True
except ImportError:
    HAS_RAWPY = False

from .constants import RAW_EXTENSIONS


# ================================================================
# GPS 坐标转换
# ================================================================


def convert_to_degrees(values: Any) -> float:
    """将 GPS 坐标元组（度/分/秒）转换为十进制度数。

    Args:
        values: GPS 坐标值，可以是 (度, 分, 秒) 元组或数值

    Returns:
        十进制度数，转换失败返回 0.0
    """
    try:
        if isinstance(values, (list, tuple)):
            d = float(values[0])
            m = float(values[1])
            s = float(values[2])
            return d + (m / 60.0) + (s / 3600.0)
        return float(values)
    except (ValueError, TypeError, IndexError):
        return 0.0


# ================================================================
# EXIF 标签值清理
# ================================================================


def clean_tag_value(value: Any) -> str:
    """清理 EXIF 标签值，转换为可读字符串。

    Args:
        value: 原始标签值（bytes / int / float / tuple / str）

    Returns:
        清理后的字符串
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace").rstrip("\x00")
        except Exception:
            return str(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            return str(int(value))
    if isinstance(value, tuple):
        return ", ".join(str(v) for v in value)
    return str(value).strip().rstrip("\x00")


def format_fraction(val: Any) -> str:
    """格式化分数值（Rational 类型）。

    Args:
        val: 具有 numerator/denominator 属性的对象或字符串

    Returns:
        格式化后的分数或数值字符串
    """
    if val is None:
        return ""
    try:
        if hasattr(val, "numerator") and hasattr(val, "denominator"):
            if val.denominator == 1:
                return str(val.numerator)
            return f"{val.numerator}/{val.denominator}"
        return str(val)
    except Exception:
        return str(val)


def rational_to_float(val: Any) -> float | None:
    """将 Rational 值转换为浮点数。

    Args:
        val: 具有 numerator/denominator 属性的对象或数值

    Returns:
        浮点数值，转换失败返回 None
    """
    if val is None:
        return None
    try:
        if hasattr(val, "numerator") and hasattr(val, "denominator"):
            if val.denominator != 0:
                return float(val.numerator) / float(val.denominator)
        return float(val)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


# ================================================================
# 图片格式检测
# ================================================================


def is_raw_file(file_ext: str) -> bool:
    """判断文件扩展名是否为 RAW 格式。

    Args:
        file_ext: 小写的文件扩展名（含点号）

    Returns:
        是否为 RAW 格式
    """
    return file_ext in RAW_EXTENSIONS


def get_raw_format_name(file_ext: str) -> str:
    """获取 RAW 格式的显示名称。

    Args:
        file_ext: 小写的文件扩展名（含点号）

    Returns:
        RAW 格式名称，非 RAW 返回空字符串
    """
    return RAW_EXTENSIONS.get(file_ext, "")


# ================================================================
# RAW 预览提取（模块级函数，供 run_in_executor 调用）
# ================================================================


def extract_raw_preview_rawpy(file_path: str):
    """使用 rawpy 提取 RAW 全尺寸预览图像为 numpy array (H, W, 3)。

    Args:
        file_path: RAW 文件路径

    Returns:
        RGB numpy array，失败返回 None
    """
    if not HAS_RAWPY or not file_path:
        return None
    try:
        import rawpy  # type: ignore

        with rawpy.imread(file_path) as raw:
            rgb = raw.postprocess(
                use_camera_wb=True,
                output_color=rawpy.ColorSpace.sRGB,
                no_auto_bright=True,
                output_bps=8,
            )
            return rgb.copy()
    except Exception:
        return None


async def make_preview_thumbnail(file_path: str) -> str | None:
    """生成预览缩略图。

    RAW 文件用 rawpy 提取全尺寸图像，普通图片直接压缩。
    返回临时 JPEG 文件路径，失败返回 None。

    Args:
        file_path: 源图片文件路径

    Returns:
        临时 JPEG 缩略图路径
    """
    if not HAS_PIL:
        return None
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

        ext = os.path.splitext(file_path)[1].lower()
        thumb_path = os.path.join(
            get_astrbot_temp_path(), f"thumb_{uuid.uuid4().hex[:8]}.jpg"
        )

        if ext in RAW_EXTENSIONS and HAS_RAWPY:
            loop = asyncio.get_event_loop()
            img_array = await loop.run_in_executor(
                None, extract_raw_preview_rawpy, file_path
            )
            if img_array is not None:
                img = PILImage.fromarray(img_array)
                img.save(thumb_path, "JPEG", quality=70)
                img.close()
                return thumb_path

        # RAW 回退 / 普通图片
        img = PILImage.open(file_path)
        img.convert("RGB").save(thumb_path, "JPEG", quality=70)
        img.close()
        return thumb_path

    except Exception:
        return None


# ================================================================
# 文本分片工具
# ================================================================


def split_text_chunks(text: str, max_chars: int = 1400) -> list[str]:
    """将文本按指定最大字符数分片，尽量保持行完整。

    Args:
        text: 待分片文本
        max_chars: 每片最大字符数

    Returns:
        分片列表
    """
    chunks: list[str] = []
    buf = ""
    for line in text.split("\n"):
        if len(line) > max_chars:
            line = line[: max_chars - 1] + "\u2026"
        cand = (buf + "\n" + line) if buf else line
        if len(cand) > max_chars and buf:
            chunks.append(buf)
            buf = line
        else:
            buf = cand
    if buf:
        chunks.append(buf)
    return chunks


# ================================================================
# GPS 坐标转换 (WGS-84 → GCJ-02 / BD-09)
# ================================================================

# WGS-84 是 GPS 原始坐标系，中国地图服务需要 GCJ-02(火星坐标系)
# 百度地图使用自己的 BD-09 坐标系

_X_PI = 3.14159265358979324 * 3000.0 / 180.0
_PI = 3.1415926535897932384626
_A = 6378245.0  # 长半轴
_EE = 0.00669342162296594323  # 偏心率平方


def _out_of_china(lat: float, lng: float) -> bool:
    """判断坐标是否在中国境外。"""
    return not (0.8293 <= lat <= 55.8271 and 72.004 <= lng <= 137.8347)


def _transform_lat(lng: float, lat: float) -> float:
    ret = (
        -100.0
        + 2.0 * lng
        + 3.0 * lat
        + 0.2 * lat * lat
        + 0.1 * lng * lat
        + 0.2 * (abs(lng) ** 0.5)
    )
    ret += (
        (20.0 * math.sin(6.0 * lng * _PI) + 20.0 * math.sin(2.0 * lng * _PI))
        * 2.0
        / 3.0
    )
    ret += (20.0 * math.sin(lat * _PI) + 40.0 * math.sin(lat / 3.0 * _PI)) * 2.0 / 3.0
    ret += (
        (160.0 * math.sin(lat / 12.0 * _PI) + 320.0 * math.sin(lat * _PI / 30.0))
        * 2.0
        / 3.0
    )
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    ret = (
        300.0
        + lng
        + 2.0 * lat
        + 0.1 * lng * lng
        + 0.1 * lng * lat
        + 0.1 * (abs(lng) ** 0.5)
    )
    ret += (
        (20.0 * math.sin(6.0 * lng * _PI) + 20.0 * math.sin(2.0 * lng * _PI))
        * 2.0
        / 3.0
    )
    ret += (20.0 * math.sin(lng * _PI) + 40.0 * math.sin(lng / 3.0 * _PI)) * 2.0 / 3.0
    ret += (
        (150.0 * math.sin(lng / 12.0 * _PI) + 300.0 * math.sin(lng / 30.0 * _PI))
        * 2.0
        / 3.0
    )
    return ret


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """WGS-84 → GCJ-02（火星坐标系，高德/腾讯地图使用）。

    Args:
        lat: WGS-84 纬度
        lng: WGS-84 经度

    Returns:
        (gcj_lat, gcj_lng)
    """
    if _out_of_china(lat, lng):
        return lat, lng
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * _PI
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * _PI)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * _PI)
    return lat + dlat, lng + dlng


def wgs84_to_bd09(lat: float, lng: float) -> tuple[float, float]:
    """WGS-84 → BD-09（百度地图坐标系）。

    Args:
        lat: WGS-84 纬度
        lng: WGS-84 经度

    Returns:
        (bd_lat, bd_lng)
    """
    gcj_lat, gcj_lng = wgs84_to_gcj02(lat, lng)
    z = math.sqrt(gcj_lng * gcj_lng + gcj_lat * gcj_lat) + 0.00002 * math.sin(
        gcj_lat * _X_PI
    )
    theta = math.atan2(gcj_lat, gcj_lng) + 0.000003 * math.cos(gcj_lng * _X_PI)
    bd_lng = z * math.cos(theta) + 0.0065
    bd_lat = z * math.sin(theta) + 0.006
    return bd_lat, bd_lng


# 地图 URL 模板
_MAP_URLS: dict[str, str] = {
    "google": ("https://www.google.com/maps?q={lat:.6f},{lng:.6f}"),
    "amap": (
        "https://uri.amap.com/marker?position={lng:.6f},{lat:.6f}"
        "&name=Photo&callnative=1"
    ),
    "baidu": (
        "https://api.map.baidu.com/marker?location={lat:.6f},{lng:.6f}"
        "&title=Photo&content=EXIF&output=html"
    ),
    "tencent": (
        "https://apis.map.qq.com/uri/v1/marker?marker="
        "coord:{lat:.6f},{lng:.6f}&title=Photo&referer=astrbot-exif"
    ),
    "openstreetmap": (
        "https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lng:.6f}"
        "#map=16/{lat:.6f}/{lng:.6f}"
    ),
    "custom": "",  # 用户自定义，运行时替换
}


def build_gps_map_url(
    lat: float,
    lng: float,
    provider: str = "amap",
    custom_url: str = "",
) -> str:
    """根据地图提供商生成 GPS 地图链接。

    自动进行坐标系转换：
    - google/openstreetmap/custom → 保持 WGS-84
    - amap/tencent → 转为 GCJ-02
    - baidu → 转为 BD-09

    Args:
        lat: WGS-84 原始纬度
        lng: WGS-84 原始经度
        provider: 地图提供商 (google/amap/baidu/tencent/openstreetmap/custom)
        custom_url: 自定义 URL 模板（仅 provider="custom" 时使用）
                   支持 {lat} {lng} {lat_wgs} {lng_wgs} 占位符

    Returns:
        地图 URL 字符串
    """
    provider = provider.lower().strip()

    # 坐标系转换
    if provider == "baidu":
        use_lat, use_lng = wgs84_to_bd09(lat, lng)
    elif provider in ("amap", "tencent"):
        use_lat, use_lng = wgs84_to_gcj02(lat, lng)
    else:
        use_lat, use_lng = lat, lng

    # 生成 URL
    if provider == "custom" and custom_url:
        return custom_url.format(
            lat=use_lat,
            lng=use_lng,
            lat_wgs=lat,
            lng_wgs=lng,
        )

    template = _MAP_URLS.get(provider, _MAP_URLS["amap"])
    return template.format(lat=use_lat, lng=use_lng)
