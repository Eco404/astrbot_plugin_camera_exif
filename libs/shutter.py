"""
相机EXIF分析插件 — 快门次数提取模块

整合多层策略提取相机快门使用次数：
策略1: MakerNote 结构化条目关键字匹配
策略2: 已知厂商 TagID 匹配
策略3: 统一二进制 TIFF IFD 解析（UnifiedIfdScanner）
策略4: latin-1 全文搜索兜底
策略5: exif_data 中的 MakerNote 子标签搜索
"""

from __future__ import annotations

import re
from typing import Any

from astrbot.api import logger

from .constants import (
    SHUTTER_COUNT_TAGS,
    SHUTTER_COUNT_KEYWORDS,
    VENDOR_SIGNATURES,
)
from .ifd_scanner import UnifiedIfdScanner


def find_shutter_count(
    mn_entries: list[tuple[str, str]],
    binary_makernote: bytes,
) -> int | None:
    """从 MakerNote 提取快门次数（4 层策略）。

    Args:
        mn_entries: [(标签短名, 值字符串)] 结构化条目列表
        binary_makernote: MakerNote 原始二进制数据

    Returns:
        快门次数或 None
    """
    # ── 策略1: mn_entries 关键字匹配 ──
    sc = _strategy_keyword_match(mn_entries)
    if sc:
        logger.info(f"[相机EXIF] 快门(策略1-关键字): {sc}")
        return sc

    # ── 策略2: mn_entries 厂商 TagID 匹配 ──
    sc = _strategy_tagid_match(mn_entries)
    if sc:
        logger.info(f"[相机EXIF] 快门(策略2-TagID): {sc}")
        return sc

    # ── 策略3: 统一二进制 TIFF IFD 解析 ──
    sc = parse_makernote_binary(binary_makernote)
    if sc:
        logger.info(f"[相机EXIF] 快门(策略3-IFD): {sc}")
        return sc

    # ── 策略4: latin-1 全文搜索 ──
    sc = _strategy_latin1_search(binary_makernote)
    if sc:
        logger.info(f"[相机EXIF] 快门(策略4-latin1): {sc}")
        return sc

    return None


def find_shutter_count_from_exif(exif_data: dict[str, Any]) -> int | None:
    """从 exif_data 的 MakerNote 子标签中提取快门次数。

    覆盖 Canon/松下/哈苏等无专用二进制解析器的品牌。
    exifread 的结构化 MakerNote 子标签已存入 exif_data。

    Args:
        exif_data: EXIF 数据字典（含 MakerNote 子标签键）

    Returns:
        快门次数或 None
    """
    for key, value in exif_data.items():
        if "MakerNote" not in key:
            continue

        key_lower = key.lower()
        val_str = str(value) if value is not None else ""

        # 方法A: 关键字匹配
        for kw in SHUTTER_COUNT_KEYWORDS:
            if kw in key_lower:
                try:
                    sc = int(float(val_str))
                    if 100 <= sc <= 9999999:
                        logger.info(
                            f"[相机EXIF] 快门(exif_data关键字): key={key} val={sc}"
                        )
                        return sc
                except (ValueError, TypeError):
                    pass

        # 方法B: 已知厂商 TagID 匹配
        hex_match = re.search(r"0x([0-9A-Fa-f]{2,4})", key)
        if hex_match:
            tag_id = int(hex_match.group(1), 16)
            for vendor_tags in SHUTTER_COUNT_TAGS.values():
                if tag_id in vendor_tags:
                    try:
                        sc = int(float(val_str))
                        if 100 <= sc <= 9999999:
                            logger.info(
                                f"[相机EXIF] 快门(exif_data TagID): "
                                f"key={key} tag=0x{tag_id:04X} val={sc}"
                            )
                            return sc
                    except (ValueError, TypeError):
                        pass

    return None


def parse_makernote_binary(binary_makernote: bytes) -> int | None:
    """统一的 MakerNote 二进制快门次数解析器。

    自动识别厂商格式，定位 TIFF IFD，解析快门标签。
    覆盖：Nikon/Sony/Canon/Fujifilm/Olympus/Panasonic/Pentax/Leica/
          Minolta/Hasselblad/Samsung/PhaseOne/Sigma/DJI/GoPro

    MakerNote 二进制结构（通用）:
        [厂商签名字符串] [TIFF头(4B)] [IFD条目数(2B)] [12B条目 × N]

    Args:
        binary_makernote: MakerNote 原始二进制数据

    Returns:
        快门次数或 None
    """
    if not binary_makernote or len(binary_makernote) < 20:
        return None

    ifd_start = 0
    byte_order = "<"
    vendor_found = ""

    # 搜索已知厂商签名
    for sig, ifd_offset, vendor_name in VENDOR_SIGNATURES:
        idx = binary_makernote.find(sig)
        if idx >= 0:
            vendor_found = vendor_name
            if vendor_name == "NIKON":
                ifd_start = idx + 10
            elif vendor_name == "AOC":
                ifd_start = idx + 4
            else:
                ifd_start = idx + ifd_offset
            logger.debug(
                f"[相机EXIF] MakerNote签名: {vendor_found} @ offset={ifd_start}"
            )
            break

    # 未匹配到已知签名：通用 TIFF 头搜索
    if not vendor_found:
        tiff = UnifiedIfdScanner.find_tiff_header(binary_makernote)
        if tiff:
            ifd_start, byte_order = tiff
            vendor_found = "(unknown)"
        else:
            return None

    if ifd_start + 8 >= len(binary_makernote):
        return None

    # 确定字节序
    tiff_magic = binary_makernote[ifd_start : ifd_start + 4]
    if tiff_magic[:2] == b"MM":
        byte_order = ">"
    elif tiff_magic[:2] == b"II":
        byte_order = "<"

    # 解析 IFD
    sc = UnifiedIfdScanner.scan_for_shutter(
        binary_makernote, ifd_start, byte_order, vendor_found
    )
    if sc:
        return sc

    # Canon 专项回退
    if not vendor_found or vendor_found == "(unknown)":
        sc = UnifiedIfdScanner.scan_canon_fallback(binary_makernote)
        if sc:
            return sc

    return None


# ================================================================
# 内部策略函数
# ================================================================


def _strategy_keyword_match(
    mn_entries: list[tuple[str, str]],
) -> int | None:
    """策略1: 关键字匹配结构化条目。

    Args:
        mn_entries: [(标签短名, 值字符串)] 列表

    Returns:
        快门次数或 None
    """
    for k_str, v_str in mn_entries:
        kl = k_str.lower()
        for kw in SHUTTER_COUNT_KEYWORDS:
            if kw in kl:
                try:
                    sc = int(float(v_str))
                    if 100 <= sc <= 9999999:
                        return sc
                except ValueError:
                    pass
    return None


def _strategy_tagid_match(
    mn_entries: list[tuple[str, str]],
) -> int | None:
    """策略2: 已知厂商 TagID 匹配。

    Args:
        mn_entries: [(标签短名, 值字符串)] 列表

    Returns:
        快门次数或 None
    """
    for k_str, v_str in mn_entries:
        for tag_ids in SHUTTER_COUNT_TAGS.values():
            for tag_id in tag_ids:
                if isinstance(tag_id, int) and str(tag_id) in k_str:
                    try:
                        sc = int(float(v_str))
                        if 100 <= sc <= 9999999:
                            return sc
                    except ValueError:
                        pass
                if isinstance(tag_id, str) and tag_id.lower() in k_str.lower():
                    try:
                        sc = int(float(v_str))
                        if 100 <= sc <= 9999999:
                            return sc
                    except ValueError:
                        pass
    return None


def _strategy_latin1_search(binary_makernote: bytes) -> int | None:
    """策略4: latin-1 全文搜索兜底。

    Args:
        binary_makernote: MakerNote 原始二进制数据

    Returns:
        快门次数或 None
    """
    if len(binary_makernote) < 100:
        return None
    try:
        text = binary_makernote.decode("latin-1", errors="ignore")
        for keyword in ("ShutterCount", "ImageCount", "Shutter", "Total"):
            idx = text.find(keyword)
            if 0 <= idx < len(text) - 5:
                match = re.search(r"(\d{3,7})", text[idx : idx + 100])
                if match:
                    sc = int(match.group(1))
                    if 100 <= sc <= 9999999:
                        return sc
    except Exception:
        pass
    return None
