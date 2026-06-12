"""
相机EXIF分析插件 — 输出格式化模块

提供三种格式化输出：
- format_display_text()   : 摘要模式（可配置显示字段）
- format_full_exif_text() : 完整模式（全部字段分组展示）
- format_shutter_only()   : 仅快门次数
"""

from __future__ import annotations

import math
from typing import Any

from .constants import (
    EXIF_TAG_NAMES_CN,
    CONFIG_TO_TAG,
)
from .utils import format_fraction


def format_display_text(
    result: dict[str, Any],
    config_fields: dict[str, bool] | None = None,
) -> str:
    """将分析结果格式化为摘要显示文本（可配置字段）。

    Args:
        result: ExifAnalyzer.analyze() 返回的结构化结果
        config_fields: {config_key: enabled} 显示开关, None=全部显示

    Returns:
        格式化后的文本
    """
    # 将 config key 转为 EXIF tag 开关映射
    display_fields: dict[str, bool] = {}
    if config_fields:
        for cfg_key, enabled in config_fields.items():
            tag = CONFIG_TO_TAG.get(cfg_key, cfg_key)
            display_fields[tag] = enabled

    file_info = result.get("file_info", {})
    exif = result.get("exif_data", {})
    sc = result.get("shutter_count")
    gps = result.get("gps", {})
    errors = result.get("errors", [])

    lines: list[str] = []
    lines.append("\U0001f4f8 图片 EXIF 分析结果")
    lines.append("\u2500" * 32)

    # 文件信息
    if file_info.get("is_raw"):
        lines.append(
            f"\U0001f4c1 文件: {file_info.get('name', '')} "
            f"({file_info.get('raw_format', 'RAW')})"
        )
    else:
        lines.append(f"\U0001f4c1 文件: {file_info.get('name', '')}")
    lines.append(f"\U0001f4e6 大小: {file_info.get('size_mb', 0)} MB")

    if not result.get("is_camera_image"):
        lines.append("")
        lines.append("\u26a0\ufe0f 该图片不包含相机 EXIF 数据")
        lines.append("   可能来源：截图、网络下载、手机App生成等")
        if errors:
            lines.append(f"   {errors[0]}")
        return "\n".join(lines)

    sep_count = 0
    shown_labels: set[str] = set()

    def add_field(tag: str, cn_name: str, value_transform=None):
        nonlocal sep_count
        if not display_fields.get(tag, True):
            return
        val = exif.get(tag, "")
        if not val:
            return
        if value_transform:
            val = value_transform(val)
        if tag in shown_labels:
            return
        shown_labels.add(tag)
        if sep_count == 0:
            lines.append("\u2500" * 32)
        lines.append(f"  {cn_name}: {val}")
        sep_count += 1

    # 相机信息
    add_field("Make", EXIF_TAG_NAMES_CN.get("Make", "相机品牌"))
    add_field("Model", EXIF_TAG_NAMES_CN.get("Model", "相机型号"))

    # 镜头信息
    add_field("LensModel", EXIF_TAG_NAMES_CN.get("LensModel", "镜头型号"))
    add_field("LensMake", EXIF_TAG_NAMES_CN.get("LensMake", "镜头品牌"))

    # 拍摄参数
    add_field(
        "FocalLength",
        EXIF_TAG_NAMES_CN.get("FocalLength", "焦距"),
        lambda v: f"{format_fraction(v.split(',')[0] if ',' in v else v)}mm",
    )
    add_field(
        "FocalLengthIn35mmFilm",
        EXIF_TAG_NAMES_CN.get("FocalLengthIn35mmFilm", "35mm等效焦距"),
        lambda v: f"{v}mm",
    )
    add_field(
        "FNumber",
        EXIF_TAG_NAMES_CN.get("FNumber", "光圈值"),
        lambda v: f"f/{format_fraction(v)}",
    )
    add_field(
        "ExposureTime",
        EXIF_TAG_NAMES_CN.get("ExposureTime", "快门速度"),
        lambda v: f"{v}s",
    )
    add_field(
        "ISOSpeedRatings",
        EXIF_TAG_NAMES_CN.get("ISOSpeedRatings", "ISO感光度"),
    )
    add_field(
        "ExposureBiasValue",
        EXIF_TAG_NAMES_CN.get("ExposureBiasValue", "曝光补偿"),
        lambda v: f"{v} EV",
    )
    add_field(
        "ExposureProgram",
        EXIF_TAG_NAMES_CN.get("ExposureProgram", "曝光模式"),
    )
    add_field(
        "ExposureMode",
        EXIF_TAG_NAMES_CN.get("ExposureMode", "曝光模式(Exif)"),
    )

    # 测光与白平衡
    add_field("MeteringMode", EXIF_TAG_NAMES_CN.get("MeteringMode", "测光模式"))
    add_field("WhiteBalance", EXIF_TAG_NAMES_CN.get("WhiteBalance", "白平衡"))
    add_field("Flash", EXIF_TAG_NAMES_CN.get("Flash", "闪光灯"))

    # 图片尺寸
    add_field(
        "ImageWidth",
        EXIF_TAG_NAMES_CN.get("ImageWidth", "图片宽度"),
        lambda v: f"{v} \u00d7 {exif.get('ImageLength', '?')} px",
    )

    # 时间
    add_field(
        "DateTimeOriginal",
        EXIF_TAG_NAMES_CN.get("DateTimeOriginal", "原始拍摄时间"),
    )
    add_field(
        "DateTimeDigitized",
        EXIF_TAG_NAMES_CN.get("DateTimeDigitized", "数字化时间"),
    )
    add_field("DateTime", EXIF_TAG_NAMES_CN.get("DateTime", "拍摄时间"))

    # 处理软件
    add_field("Software", EXIF_TAG_NAMES_CN.get("Software", "处理软件"))

    # 序列号
    add_field(
        "BodySerialNumber",
        EXIF_TAG_NAMES_CN.get("BodySerialNumber", "机身序列号"),
    )
    add_field("SerialNumber", EXIF_TAG_NAMES_CN.get("SerialNumber", "序列号"))

    # 作者/版权
    add_field("Artist", EXIF_TAG_NAMES_CN.get("Artist", "作者"))
    add_field("Copyright", EXIF_TAG_NAMES_CN.get("Copyright", "版权"))

    # 快门次数
    if display_fields.get("shutter_count", True) and sc:
        lines.append("\u2500" * 32)
        lines.append(f"  \U0001f4f7 快门次数: {sc}")

    # GPS
    if display_fields.get("gps", False) and gps:
        lines.append("\u2500" * 32)
        lines.append("  \U0001f4cd GPS信息:")
        if gps.get("latitude"):
            lines.append(f"    纬度: {gps['latitude']}\u00b0")
        if gps.get("longitude"):
            lines.append(f"    经度: {gps['longitude']}\u00b0")
        if gps.get("altitude"):
            lines.append(f"    海拔: {gps['altitude']}m")
        if gps.get("map_url"):
            lines.append(f"    \U0001f5fa\ufe0f {gps['map_url']}")

    if not sep_count and not sc:
        lines.append("  \u26a0\ufe0f 未能提取到EXIF字段数据")

    # ── 数据来源统计 ──
    sources = result.get("_exif_sources", {})
    if sources:
        src_counts: dict[str, int] = {}
        for src in sources.values():
            src_counts[src] = src_counts.get(src, 0) + 1
        src_display = ", ".join(
            f"{s}({c})" for s, c in sorted(src_counts.items(), key=lambda x: -x[1])
        )
        lines.append("\u2500" * 32)
        lines.append("\U0001f4ca 数据来源: " + src_display)

    return "\n".join(lines)


def format_full_exif_text(result: dict[str, Any]) -> str:
    """格式化完整的 EXIF 数据（用于 /exif 指令）。

    Args:
        result: ExifAnalyzer.analyze() 返回的结构化结果

    Returns:
        格式化后的完整 EXIF 文本
    """
    exif = result.get("exif_data", {})
    file_info = result.get("file_info", {})
    sc = result.get("shutter_count")
    gps = result.get("gps", {})

    lines: list[str] = []
    lines.append("\U0001f4f8 完整 EXIF 元数据")
    lines.append("\u2550" * 36)

    # 文件信息
    if file_info.get("is_raw"):
        lines.append(f"文件: {file_info.get('name')} ({file_info.get('raw_format')})")
    else:
        lines.append(f"文件: {file_info.get('name')}")
    lines.append(f"大小: {file_info.get('size_mb')} MB")

    if not result.get("is_camera_image"):
        lines.append("\u26a0\ufe0f 该图片不含相机EXIF数据")
        return "\n".join(lines)

    # ── 器材 ──
    _section_camera(exif, lines)

    # ── 模式 ──
    _section_mode(exif, lines)

    # ── 曝光 ──
    _section_exposure(exif, lines)

    # ── 焦距 ──
    _section_focal(exif, lines)

    # ── 色彩 ──
    _section_color(exif, lines)

    # ── 时间 ──
    _section_time(exif, lines)

    # ── 快门次数 ──
    if sc:
        lines.append("\u2500" * 36)
        lines.append(f"\U0001f4f7 快门次数: {sc}")

    # ── 闪光灯 ──
    flash = exif.get("Flash", "")
    if flash:
        lines.append("\u2500" * 36)
        lines.append(f"\U0001f4a1 闪光灯: {flash}")

    # ── 图片属性 ──
    _section_image_props(exif, lines)

    # ── 版权 ──
    _section_copyright(exif, lines)

    # ── GPS ──
    if gps:
        _section_gps(gps, lines)

    # ── XMP ──
    xmp = result.get("xmp", {})
    if xmp:
        lines.append("\u2500" * 36)
        lines.append("\U0001f4dd XMP信息:")
        for k in ("CreatorTool", "CreateDate", "Rating"):
            if xmp.get(k):
                lines.append(f"  {k}: {xmp[k]}")

    # ── MakerNote ──
    mn = result.get("maker_note", {})
    if mn:
        shown = {k: v for k, v in mn.items() if k != "shutter_count" and v}
        if shown:
            lines.append("\u2500" * 36)
            lines.append("\U0001f527 相机内部信息:")
            for k, v in shown.items():
                lines.append(f"  {k}: {v}")

    # ── DNG 信息 ──
    dng_info = _format_dng_info(exif, result)
    for line in dng_info:
        lines.append(line)

    # ── 其他未归类标签 ──
    listed = _build_listed_set()
    others = {
        k: v
        for k, v in exif.items()
        if k not in listed
        and not k.startswith("_")
        and not k.startswith("GPS")
        and k not in ("MakerNote", "UserComment")
    }
    # ── 数据来源统计 ──
    sources = result.get("_exif_sources", {})
    if sources:
        src_counts: dict[str, int] = {}
        for src in sources.values():
            src_counts[src] = src_counts.get(src, 0) + 1
        src_display = ", ".join(
            f"{s}({c})" for s, c in sorted(src_counts.items(), key=lambda x: -x[1])
        )
        lines.append("\u2500" * 36)
        lines.append("\U0001f4ca 数据来源: " + src_display)

    if others:
        current_len = len("\n".join(lines))
        if current_len > 6000:
            lines.append("\u2500" * 36)
            lines.append(f"\U0001f4cb 其他标签({len(others)})")
            lines.append(f"  \u2139\ufe0f 元数据过多，已省略")
        else:
            lines.append("\u2500" * 36)
            lines.append(f"\U0001f4cb 其他标签({len(others)}):")
            shown = 0
            for k, v in others.items():
                cn = EXIF_TAG_NAMES_CN.get(k, k)
                val_str = str(v)
                displayable, reason = _check_displayable(k, val_str)
                if not displayable:
                    lines.append(f"  {cn}: <{reason}>")
                elif len(val_str) <= 200:
                    lines.append(f"  {cn}: {val_str}")
                else:
                    lines.append(f"  {cn}:")
                    for i in range(0, len(val_str), 120):
                        chunk = val_str[i : i + 120]
                        lines.append(f"    {chunk}")
                shown += 1
                if len("\n".join(lines)) > 7500:
                    lines.append(f"  ... 还有 {len(others) - shown} 条已省略")
                    break

    return "\n".join(lines)


def format_shutter_only(result: dict[str, Any]) -> str:
    """仅返回快门次数信息。

    Args:
        result: ExifAnalyzer.analyze() 返回的结构化结果

    Returns:
        快门次数文本
    """
    sc = result.get("shutter_count")
    exif = result.get("exif_data", {})
    make = exif.get("Make", "")
    model = exif.get("Model", "")

    if not result.get("is_camera_image"):
        return "\u26a0\ufe0f 该图片不含相机EXIF数据，无法获取快门次数"

    lines = ["\U0001f4f7 快门次数查询"]
    lines.append("\u2500" * 20)
    if make or model:
        camera_info = f"{make} {model}".strip()
        lines.append(f"相机: {camera_info}")
    if sc:
        lines.append(f"\U0001f4f8 快门次数: {sc}")
    else:
        lines.append("\u26a0\ufe0f 未能从该图片提取快门次数")
        lines.append("   可能原因：")
        lines.append("   \u2022 该相机型号不支持在EXIF中记录快门次数")
        lines.append("   \u2022 图片经过后期处理丢失了MakerNote数据")
        lines.append("   \u2022 相机厂商将快门次数存储在非标准标签中")
    return "\n".join(lines)


# ================================================================
# 分区格式化辅助函数
# ================================================================


def _section_camera(exif: dict[str, Any], lines: list[str]) -> None:
    """器材分区。"""
    make = exif.get("Make", "")
    model = exif.get("Model", "")
    lens = exif.get("LensModel", "")
    body = f"{make} {model}".strip() if make or model else ""
    camera_line = body
    if lens:
        camera_line += (", " + lens) if camera_line else lens
    if camera_line:
        lines.append("\u2500" * 36)
        lines.append("\U0001f4f7 器材")
        lines.append(f"  {camera_line}")
    sn = exif.get("BodySerialNumber") or exif.get("SerialNumber")
    if sn:
        lines.append(f"  机身序列号: {sn}")
    # DNG 特有
    unique = exif.get("UniqueCameraModel", "")
    if unique and unique != model:
        lines.append(f"  DNG型号: {unique}")


def _section_mode(exif: dict[str, Any], lines: list[str]) -> None:
    """拍摄模式分区。"""
    mode_items = []
    ep = exif.get("ExposureProgram", "")
    if ep:
        mode_items.append(f"曝光模式:{ep}")
    mm = exif.get("MeteringMode", "")
    if mm:
        mode_items.append(f"测光模式:{mm}")
    ev = exif.get("ExposureBiasValue", "")
    if ev:
        mode_items.append(f"曝光补偿:{ev}")
    if mode_items:
        lines.append("\u2500" * 36)
        lines.append("\U0001f3af 模式")
        lines.append(f"  {', '.join(mode_items)}")


def _section_exposure(exif: dict[str, Any], lines: list[str]) -> None:
    """曝光分区。"""
    exp_items = []
    fn = exif.get("FNumber", "")
    if fn:
        exp_items.append(f"光圈:{fn}")
    et = exif.get("ExposureTime", "")
    if et:
        exp_items.append(f"快门:{et}秒")
    iso = exif.get("ISOSpeedRatings", "")
    if iso:
        exp_items.append(f"ISO{iso}")
    if exp_items:
        lines.append("\u2500" * 36)
        lines.append("\u2699\ufe0f 曝光")
        lines.append(f"  {', '.join(exp_items)}")


def _section_focal(exif: dict[str, Any], lines: list[str]) -> None:
    """焦距分区。"""
    fl = exif.get("FocalLength", "")
    fl35 = exif.get("FocalLengthIn35mmFilm", "")
    if fl:
        fl_text = f"焦距: {fl} mm"
        if fl35:
            fl_text += f" (35mm等效: {fl35} mm)"
            try:
                fl35_val = float(fl35)
                angle = 2 * math.atan(43.27 / (2 * fl35_val)) * 180 / math.pi
                fl_text += f", 视角:{angle:.1f}\u00b0"
            except Exception:
                pass
        lines.append(f"  {fl_text}")


def _section_color(exif: dict[str, Any], lines: list[str]) -> None:
    """色彩分区。"""
    color_items = []
    wb = exif.get("WhiteBalance", "")
    if wb:
        color_items.append(f"白平衡:{wb}")
    cs = exif.get("ColorSpace", "")
    if cs:
        color_items.append(f"色彩空间:{cs}")
    if color_items:
        lines.append("\u2500" * 36)
        lines.append("\U0001f3a8 色彩")
        lines.append(f"  {', '.join(color_items)}")


def _section_time(exif: dict[str, Any], lines: list[str]) -> None:
    """时间分区。"""
    dt = exif.get("DateTimeOriginal", "")
    subsec = exif.get("SubSecTimeOriginal", "")
    if dt:
        time_str = dt.strip()
        if subsec:
            time_str += f".{subsec.rstrip()}"
        lines.append("\u2500" * 36)
        lines.append("\U0001f4c5 时间")
        lines.append(f"  {time_str}")
    elif dt2 := exif.get("DateTime", ""):
        lines.append("\u2500" * 36)
        lines.append("\U0001f4c5 时间")
        lines.append(f"  {dt2}")


def _section_image_props(exif: dict[str, Any], lines: list[str]) -> None:
    """图片属性分区。"""
    w = exif.get("ImageWidth", "")
    h = exif.get("ImageLength", "")
    orient = exif.get("Orientation", "")
    sw = exif.get("Software", "")
    if w and h:
        lines.append("\u2500" * 36)
        lines.append("\U0001f5bc\ufe0f 图片属性")
        lines.append(f"  尺寸: {w} \u00d7 {h} px")
        if orient:
            lines.append(f"  方向: {orient}")
        if sw:
            lines.append(f"  软件: {sw}")


def _section_copyright(exif: dict[str, Any], lines: list[str]) -> None:
    """版权分区。"""
    artist = exif.get("Artist", "")
    copyright_str = exif.get("Copyright", "")
    if artist or copyright_str:
        lines.append("\u2500" * 36)
        lines.append("\U0001f464 版权信息")
        if artist:
            lines.append(f"  作者: {artist}")
        if copyright_str:
            lines.append(f"  版权: {copyright_str}")


def _section_gps(gps: dict[str, Any], lines: list[str]) -> None:
    """GPS 分区。"""
    lines.append("\u2500" * 36)
    lines.append("\U0001f4cd GPS信息:")
    for k, v in gps.items():
        if k != "map_url":
            lines.append(f"  {k}: {v}")
    if gps.get("map_url"):
        lines.append(f"  \U0001f5fa\ufe0f {gps['map_url']}")


def _format_dng_info(exif: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """格式化 DNG 特有信息。"""
    dng_lines: list[str] = []
    dng_ver = exif.get("DNGVersion", "")
    if dng_ver:
        dng_lines.append("\u2500" * 36)
        dng_lines.append("\U0001f4e6 DNG 信息")
        dng_lines.append(f"  DNG版本: {_fmt_dng_version(dng_ver)}")
        # 兼容版本
        back_ver = exif.get("DNGBackwardVersion", "")
        if back_ver:
            dng_lines.append(f"  兼容版本: {_fmt_dng_version(back_ver)}")
        # 原始 RAW 文件名
        orig = exif.get("OriginalRawFileName", "")
        if orig:
            dng_lines.append(f"  原始RAW: {orig}")
    return dng_lines


def _fmt_dng_version(raw: str) -> str:
    """格式化 DNG 版本字符串。

    如果值为原始字节串（如 "\\x01\\x07"），转换为 "1.7" 格式。
    否则直接返回原值。
    """
    if not raw:
        return ""
    # 尝试检测是否为转义的字节串
    if raw.startswith("\\x") or (len(raw) <= 10 and "\\x" in raw):
        try:
            # 尝试解析为字节
            parts = []
            for ch in raw:
                parts.append(str(ord(ch)))
            return ".".join(parts)
        except Exception:
            pass
    # 尝试解析为 "1.7.0.0" 格式
    if "." in raw and all(p.isdigit() or p == "" for p in raw.split(".")):
        return raw
    return raw


def _check_displayable(key: str, val: str) -> tuple[bool, str]:
    """检查标签值是否适合在 QQ 消息中显示。

    返回 (是否显示, 不显示原因)。

    DNG 文件包含大量 RAW 校准数据（色相饱和度表、Profile 查找表、
    噪声配置文件等），每条可达数万到数十万字符，会撑爆 QQ 转发限制。
    这些数据仅对 RAW 处理有意义，对一般用户无阅读价值。
    """
    # DNG RAW 校准标签关键词
    raw_calibration_keys = {
        "profile",
        "noise",
        "calibration",
        "matrix",
        "huesat",
        "lookup",
        "tonecurve",
        "colormatrix",
        "forwardmatrix",
        "reductionmatrix",
        "cameracalibration",
        "analogbalance",
        "asshotneutral",
        "baseline",
        "blacklevel",
        "whitelevel",
        "linearization",
        "maskedareas",
        "activearea",
        "defaultscale",
        "defaultcrop",
        "chromablur",
        "antialias",
        "shadowscale",
        "rawdatauniqueid",
        "originalrawfiledata",
        "dngprivate",
        "semanticmask",
        "calibrationsemantic",
        "bestquality",
        "subfile",
        "subifds",
        "tileoffsets",
        "tilebytecounts",
        "tilewidth",
        "tilelength",
        "stripoffsets",
        "stripbytecounts",
        "jpeginterchange",
        "rowsperstrip",
        "planarconfig",
        "sampleformat",
        "samplesperpixel",
        "bitspersample",
        "photometric",
        "compression",
        "ycbcr",
        "referenceblackwhite",
        "newsubfile",
    }
    kl = key.lower().replace(" ", "").replace("_", "")

    # 1. 校准数据标签 + 超大值 → 跳过
    if any(kw in kl for kw in raw_calibration_keys):
        if len(val) > 500:
            return False, f"RAW校准数据, {len(val)}字"
        if any(c in val[:100] for c in ("\x00", "\x01", "\x02", "\x03")):
            return False, f"RAW校准二进制数据, {len(val)}字"

    # 2. 超过 10000 字的任意标签 → 跳过
    if len(val) > 10000:
        return False, f"数据过大({len(val)}字)"

    # 3. 含有大量非可打印字符 → 二进制数据
    if len(val) > 100:
        non_printable = sum(1 for c in val if ord(c) < 32 or ord(c) > 126)
        if non_printable > len(val) * 0.15:
            return False, f"二进制数据, {len(val)}字"

    # 4. 纯数字/逗号/方括号组成的数组 > 2000 字 → 跳过
    if len(val) > 2000:
        bracket_chars = sum(1 for c in val if c in "0123456789.,[]()e+- \n")
        if bracket_chars > len(val) * 0.95:
            return False, f"大型数值数组, {len(val)}字"

    return True, ""


def _build_listed_set() -> set:
    """构建已列出的 EXIF 标签键集合。"""
    return {
        "Make",
        "Model",
        "LensModel",
        "BodySerialNumber",
        "SerialNumber",
        "ExposureProgram",
        "MeteringMode",
        "ExposureBiasValue",
        "FNumber",
        "ExposureTime",
        "ISOSpeedRatings",
        "FocalLength",
        "FocalLengthIn35mmFilm",
        "WhiteBalance",
        "ColorSpace",
        "DateTimeOriginal",
        "DateTime",
        "SubSecTimeOriginal",
        "Flash",
        "ImageWidth",
        "ImageLength",
        "Orientation",
        "Software",
        "Artist",
        "Copyright",
        "SceneCaptureType",
        "ExposureMode",
        "LensMake",
        "LensSerialNumber",
        "UniqueCameraModel",
        "LocalizedCameraModel",
        "DNGVersion",
        "DNGBackwardVersion",
        "OriginalRawFileName",
        "XMLPacket",
        "_raw_makernote",
    }
