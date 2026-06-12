"""
相机EXIF分析插件 — 统一 TIFF IFD 扫描器模块

跨厂商 MakerNote 快门次数解析 + DNG IFD 扫描。
自动识别厂商签名、字节序、IFD 条目结构，
递归解析嵌套子 IFD 及 Canon FileInfo 特殊结构。

IFD 条目格式（12 字节）:
    TagID(2B) | Type(2B) | Count(4B) | Value/Offset(4B)
Type=3 → SHORT (2B), Type=4 → LONG (4B)
若值 ≤ 4 字节，直接存储在 Value/Offset 字段；否则存储偏移量。
"""

from __future__ import annotations

import struct
from typing import Any

from astrbot.api import logger


class UnifiedIfdScanner:
    """静态工具类：解析 MakerNote / DNG 二进制中的 TIFF IFD 结构。

    支持：
    - 各厂商签名自动识别（Nikon/Sony/Canon/Fujifilm/Olympus/Panasonic/Pentax/
      Leica/Minolta/Hasselblad/Samsung/PhaseOne/Sigma/DJI/GoPro）
    - 自动字节序判断（MM 大端 / II 小端）
    - 递归 IFD 解析（最大深度 4 层）
    - Canon FileInfo 子 IFD (tag 0x0093 → index1 快门计数)
    - DNG Raw IFD 快门搜索
    """

    # IFD 条目大小（字节）
    IFD_ENTRY_SIZE = 12
    # 合理快门次数范围
    SHUTTER_MIN = 100
    SHUTTER_MAX = 9999999
    # 最大递归深度
    MAX_DEPTH = 4
    # 最大条目数
    MAX_ENTRIES = 500

    @staticmethod
    def _unpack(fmt: str, data: bytes, offset: int, bo: str) -> Any:
        """安全解包二进制数据。

        Args:
            fmt: struct 格式符（不含字节序）
            bo: 字节序 "<" 或 ">"
            data: 二进制数据
            offset: 偏移量

        Returns:
            解包后的值，越界返回 None
        """
        size = struct.calcsize(fmt)
        if offset + size > len(data):
            return None
        try:
            return struct.unpack_from(f"{bo}{fmt}", data, offset)[0]
        except struct.error:
            return None

    # ================================================================
    # 入口：扫描二进制数据中的快门次数
    # ================================================================

    @staticmethod
    def scan_for_shutter(
        data: bytes,
        ifd_base: int,
        byte_order: str,
        vendor: str,
    ) -> int | None:
        """从 TIFF IFD 中扫描快门次数标签。

        Args:
            data: MakerNote 或 DNG 原始二进制数据
            ifd_base: TIFF 头起始偏移（指向 "MM\\x00\\x2a" 或 "II\\x2a\\x00"）
            byte_order: "<" 或 ">"
            vendor: 厂商名称（用于选择正确的标签组）

        Returns:
            快门次数，未找到返回 None
        """
        from .constants import SHUTTER_COUNT_TAGS

        bo = byte_order
        if ifd_base + 8 > len(data):
            return None

        ifd0_offset = UnifiedIfdScanner._unpack("I", data, ifd_base + 4, bo)
        if ifd0_offset is None:
            return None
        ifd_pos = ifd_base + ifd0_offset

        # 构建该厂商的目标标签集合
        vendor_upper = vendor.upper()
        target_ids: set[int] = set()
        canon_ids: set[int] = set()

        for vk, tag_list in SHUTTER_COUNT_TAGS.items():
            matched = vk.upper() in vendor_upper or vendor_upper in vk.upper()
            if matched:
                for tid in tag_list:
                    if isinstance(tid, int):
                        target_ids.add(tid)
            if vk == "Canon":
                for tid in tag_list:
                    if isinstance(tid, int):
                        canon_ids.add(tid)

        # 无匹配厂商时使用全部标签
        if not target_ids:
            for tag_list in SHUTTER_COUNT_TAGS.values():
                for tid in tag_list:
                    if isinstance(tid, int):
                        target_ids.add(tid)

        return UnifiedIfdScanner._parse_ifd_entries(
            data, ifd_pos, bo, vendor, target_ids, canon_ids, 0
        )

    # ================================================================
    # 递归 IFD 条目解析
    # ================================================================

    @staticmethod
    def _parse_ifd_entries(
        data: bytes,
        ifd_pos: int,
        bo: str,
        vendor: str,
        target_ids: set[int],
        canon_ids: set[int],
        depth: int,
    ) -> int | None:
        """递归解析 IFD 条目，匹配快门标签。

        Args:
            data: 二进制数据
            ifd_pos: IFD 起始位置
            bo: 字节序
            vendor: 厂商名（大写）
            target_ids: 目标快门 TagID 集合
            canon_ids: Canon 快门 TagID 集合
            depth: 当前递归深度

        Returns:
            快门次数或 None
        """
        if depth > UnifiedIfdScanner.MAX_DEPTH:
            return None

        cnt = UnifiedIfdScanner._unpack("H", data, ifd_pos, bo)
        if cnt is None or cnt > UnifiedIfdScanner.MAX_ENTRIES:
            return None

        vendor_upper = vendor.upper()
        pos = ifd_pos + 2

        for _ in range(min(cnt, 200)):
            if pos + UnifiedIfdScanner.IFD_ENTRY_SIZE > len(data):
                break

            tag_id = UnifiedIfdScanner._unpack("H", data, pos, bo)
            tag_type = UnifiedIfdScanner._unpack("H", data, pos + 2, bo)
            val_offset = UnifiedIfdScanner._unpack("I", data, pos + 8, bo)

            if tag_id is None or tag_type is None or val_offset is None:
                pos += UnifiedIfdScanner.IFD_ENTRY_SIZE
                continue

            # 检查是否为目标标签
            if tag_id in target_ids:
                sc = UnifiedIfdScanner._extract_value(
                    tag_type, val_offset, tag_id, vendor
                )
                if sc:
                    return sc

            # Canon 0x0093 → 解析 CanonFileInfo 子 IFD
            if vendor_upper == "CANON" and tag_id in canon_ids:
                sub_result = UnifiedIfdScanner._try_canon_subifd(
                    data, tag_type, val_offset, bo
                )
                if sub_result:
                    return sub_result

            pos += UnifiedIfdScanner.IFD_ENTRY_SIZE

        # 扫描下一个 IFD
        sc = UnifiedIfdScanner._scan_next_ifd(
            data, pos, bo, vendor, target_ids, canon_ids, depth
        )
        if sc:
            return sc

        return None

    @staticmethod
    def _extract_value(
        tag_type: int, val_offset: int, tag_id: int, vendor: str
    ) -> int | None:
        """从 IFD 条目值中提取快门计数。

        Args:
            tag_type: IFD 数据类型
            val_offset: 值/偏移量
            tag_id: 标签 ID
            vendor: 厂商名

        Returns:
            快门次数或 None
        """
        if tag_type in (3, 4):  # SHORT, LONG
            if (
                UnifiedIfdScanner.SHUTTER_MIN
                <= val_offset
                <= UnifiedIfdScanner.SHUTTER_MAX
            ):
                logger.info(
                    f"[相机EXIF] 统一IFD: 厂商={vendor} "
                    f"Tag=0x{tag_id:04X} Type={tag_type} Val={val_offset}"
                )
                return val_offset
        return None

    @staticmethod
    def _try_canon_subifd(
        data: bytes, tag_type: int, val_offset: int, bo: str
    ) -> int | None:
        """尝试解析 Canon 子 IFD（CanonFileInfo / 其他子结构）。

        Args:
            data: 二进制数据
            tag_type: 标签类型
            val_offset: 值/偏移量
            bo: 字节序

        Returns:
            快门次数或 None
        """
        if tag_type not in (3, 4, 7):
            return None
        sub_ifd_pos = val_offset
        if 0 < sub_ifd_pos < len(data) - 10:
            return UnifiedIfdScanner._parse_canon_fileinfo(data, sub_ifd_pos, bo)
        return None

    @staticmethod
    def _scan_next_ifd(
        data: bytes,
        pos: int,
        bo: str,
        vendor: str,
        target_ids: set[int],
        canon_ids: set[int],
        depth: int,
    ) -> int | None:
        """扫描 IFD 链中的下一个 IFD。

        Args:
            data: 二进制数据
            pos: 当前条目结束后的位置
            bo: 字节序
            vendor: 厂商名
            target_ids: 目标标签集合
            canon_ids: Canon 标签集合
            depth: 当前深度

        Returns:
            快门次数或 None
        """
        next_offset = UnifiedIfdScanner._unpack("I", data, pos, bo)
        if next_offset and 0 < next_offset < len(data):
            return UnifiedIfdScanner._parse_ifd_entries(
                data, next_offset, bo, vendor, target_ids, canon_ids, depth + 1
            )
        return None

    # ================================================================
    # Canon FileInfo 子 IFD 解析
    # ================================================================

    @staticmethod
    def _parse_canon_fileinfo(data: bytes, ifd_pos: int, bo: str) -> int | None:
        """解析 Canon FileInfo 子 IFD (tag 0x0093 指向)。

        Canon FileInfo IFD 结构:
            entry_count(2B) + entries*12B + next_ifd(4B)
        条目 index 1 为快门计数（int32u）。

        Args:
            data: 二进制数据
            ifd_pos: IFD 起始位置
            bo: 字节序

        Returns:
            快门次数或 None
        """
        try:
            if ifd_pos + 2 > len(data):
                return None
            entry_count = UnifiedIfdScanner._unpack("H", data, ifd_pos, bo)
            if entry_count is None or entry_count < 2 or entry_count > 50:
                return None

            # 第二个条目 (index 1): 跳过 count(2B) + first entry(12B)
            entry_pos = ifd_pos + 2 + UnifiedIfdScanner.IFD_ENTRY_SIZE
            if entry_pos + UnifiedIfdScanner.IFD_ENTRY_SIZE > len(data):
                return None

            val = UnifiedIfdScanner._unpack("I", data, entry_pos + 8, bo)
            if (
                val
                and UnifiedIfdScanner.SHUTTER_MIN
                <= val
                <= UnifiedIfdScanner.SHUTTER_MAX
            ):
                logger.info(f"[相机EXIF] Canon FileInfo index1 ShutterCount: {val}")
                return val
        except Exception:
            pass
        return None

    # ================================================================
    # Canon 无签名格式回退扫描
    # ================================================================

    @staticmethod
    def scan_canon_fallback(binary_makernote: bytes) -> int | None:
        """Canon 无签名格式的回退扫描。

        在二进制数据中搜索 TIFF 头，然后遍历 IFD 查找快门标签。
        适用于某些 Canon 相机 MakerNote 不使用标准签名的场景。

        Args:
            binary_makernote: MakerNote 原始二进制数据

        Returns:
            快门次数或 None
        """
        if len(binary_makernote) < 50:
            return None
        try:
            for offset in range(min(50, len(binary_makernote) - 4)):
                chunk = binary_makernote[offset : offset + 4]
                bo: str = ""
                if chunk[:2] == b"MM":
                    bo = ">"
                elif chunk[:2] == b"II":
                    bo = "<"
                else:
                    continue
                if chunk[2:4] not in (b"\x00\x2a", b"\x2a\x00", b"\x00*", b"*\x00"):
                    continue
                if offset + 8 > len(binary_makernote):
                    continue

                ifd_offset = struct.unpack_from(f"{bo}I", binary_makernote, offset + 4)[
                    0
                ]
                ifd_pos = offset + ifd_offset

                from .constants import SHUTTER_COUNT_TAGS

                target_ids: set[int] = set()
                canon_ids: set[int] = set()
                for vk, tag_list in SHUTTER_COUNT_TAGS.items():
                    if vk == "Canon":
                        for tid in tag_list:
                            if isinstance(tid, int):
                                canon_ids.add(tid)
                                target_ids.add(tid)
                    else:
                        for tid in tag_list:
                            if isinstance(tid, int):
                                target_ids.add(tid)

                sc = UnifiedIfdScanner._parse_ifd_entries(
                    binary_makernote, ifd_pos, bo, "Canon", target_ids, canon_ids, 0
                )
                if sc:
                    return sc
        except Exception:
            pass
        return None

    # ================================================================
    # TIFF 头定位工具
    # ================================================================

    @staticmethod
    def find_tiff_header(data: bytes, start: int = 0) -> tuple[int, str] | None:
        """在二进制数据中搜索 TIFF 头。

        Args:
            data: 二进制数据
            start: 搜索起始偏移

        Returns:
            (偏移量, 字节序 "<"/">") 或 None
        """
        end = len(data) - 4
        for offset in range(start, min(start + 100, end)):
            chunk = data[offset : offset + 4]
            if chunk[:2] == b"MM" and chunk[2:4] in (b"\x00\x2a", b"\x00*"):
                return (offset, ">")
            if chunk[:2] == b"II" and chunk[2:4] in (b"\x2a\x00", b"*\x00"):
                return (offset, "<")
        return None

    # ================================================================
    # DNG IFD 扫描
    # ================================================================

    @staticmethod
    def scan_dng_ifd(
        raw_exif: bytes,
        target_tag_ids: set[int] | None = None,
    ) -> dict[int, Any]:
        """扫描 DNG TIFF/EP 结构中的所有 IFD，提取指定标签。

        遍历 IFD0 → SubIFD → ExifIFD，提取目标标签的值。

        Args:
            raw_exif: 原始 EXIF 字节数据（已剥离 Exif 前缀）
            target_tag_ids: 目标标签 ID 集合，None 则提取全部

        Returns:
            {tag_id: raw_value} 映射
        """
        result: dict[int, Any] = {}
        if not raw_exif or len(raw_exif) < 8:
            return result

        # 检测字节序
        bo: str = "<"
        if raw_exif[:2] == b"MM":
            bo = ">"
        elif raw_exif[:2] != b"II":
            return result

        # 读取第一个 IFD 偏移
        ifd0_offset = UnifiedIfdScanner._unpack("I", raw_exif, 4, bo)
        if ifd0_offset is None:
            return result

        # 遍历 IFD 链
        visited: set[int] = set()
        UnifiedIfdScanner._scan_dng_ifd_chain(
            raw_exif, ifd0_offset, bo, target_tag_ids, result, visited, 0
        )
        return result

    @staticmethod
    def _scan_dng_ifd_chain(
        data: bytes,
        ifd_offset: int,
        bo: str,
        target_ids: set[int] | None,
        result: dict[int, Any],
        visited: set[int],
        depth: int,
    ) -> None:
        """递归扫描 DNG IFD 链。

        Args:
            data: 原始 EXIF 字节数据
            ifd_offset: 当前 IFD 偏移（相对 data 起始）
            bo: 字节序
            target_ids: 目标标签集合
            result: 结果字典
            visited: 已访问偏移集合
            depth: 递归深度
        """
        if depth > 5 or ifd_offset in visited:
            return
        if ifd_offset + 2 > len(data):
            return
        visited.add(ifd_offset)

        cnt = UnifiedIfdScanner._unpack("H", data, ifd_offset, bo)
        if cnt is None or cnt > 1000:
            return

        pos = ifd_offset + 2
        sub_ifds: list[int] = []

        for _ in range(min(cnt, 500)):
            if pos + UnifiedIfdScanner.IFD_ENTRY_SIZE > len(data):
                break

            tag_id = UnifiedIfdScanner._unpack("H", data, pos, bo)
            tag_type = UnifiedIfdScanner._unpack("H", data, pos + 2, bo)
            tag_count = UnifiedIfdScanner._unpack("I", data, pos + 4, bo)

            if tag_id is None or tag_type is None or tag_count is None:
                pos += UnifiedIfdScanner.IFD_ENTRY_SIZE
                continue

            if target_ids is None or tag_id in target_ids:
                val_raw = UnifiedIfdScanner._read_ifd_value(
                    data, pos, bo, tag_type, tag_count
                )
                if val_raw is not None:
                    result[tag_id] = val_raw

            # 记录子 IFD 链接
            if tag_id in (0x014A, 0x8769, 0x8825, 0xA005):
                sub_offset = UnifiedIfdScanner._unpack("I", data, pos + 8, bo)
                if sub_offset and 0 < sub_offset < len(data):
                    sub_ifds.append(sub_offset)

            pos += UnifiedIfdScanner.IFD_ENTRY_SIZE

        # 下一个 IFD
        next_offset = UnifiedIfdScanner._unpack("I", data, pos, bo)
        if next_offset and 0 < next_offset < len(data):
            UnifiedIfdScanner._scan_dng_ifd_chain(
                data, next_offset, bo, target_ids, result, visited, depth + 1
            )

        # 子 IFD
        for sub_off in sub_ifds:
            UnifiedIfdScanner._scan_dng_ifd_chain(
                data, sub_off, bo, target_ids, result, visited, depth + 1
            )

    @staticmethod
    def _read_ifd_value(
        data: bytes, entry_pos: int, bo: str, tag_type: int, count: int
    ) -> Any:
        """从 IFD 条目读取实际值。

        Args:
            data: 二进制数据
            entry_pos: 条目起始位置
            bo: 字节序
            tag_type: 数据类型
            count: 数据项数量

        Returns:
            解析后的值
        """
        total_size = UnifiedIfdScanner._type_size(tag_type) * count
        val_offset = UnifiedIfdScanner._unpack("I", data, entry_pos + 8, bo)

        if val_offset is None:
            return None

        if total_size <= 4:
            # 值直接存储在 offset 字段中
            raw_bytes = data[entry_pos + 8 : entry_pos + 8 + total_size]
            return UnifiedIfdScanner._decode_value(raw_bytes, bo, tag_type, count)
        else:
            # 值为偏移量
            if val_offset + total_size > len(data):
                return None
            raw_bytes = data[val_offset : val_offset + total_size]
            return UnifiedIfdScanner._decode_value(raw_bytes, bo, tag_type, count)

    @staticmethod
    def _type_size(tag_type: int) -> int:
        """根据 TIFF 数据类型返回每元素字节数。"""
        sizes = {
            1: 1,
            2: 1,
            3: 2,
            4: 4,
            5: 8,
            6: 1,
            7: 1,
            8: 2,
            9: 4,
            10: 8,
            11: 4,
            12: 8,
        }
        return sizes.get(tag_type, 1)

    @staticmethod
    def _decode_value(raw: bytes, bo: str, tag_type: int, count: int) -> Any:
        """解码 TIFF 数据类型值。

        Args:
            raw: 原始字节
            bo: 字节序
            tag_type: 数据类型
            count: 元素数量

        Returns:
            解码后的 Python 值
        """
        try:
            if tag_type == 1:  # BYTE
                return list(raw[:count])
            elif tag_type == 2:  # ASCII
                return raw.rstrip(b"\x00").decode("utf-8", errors="replace")
            elif tag_type == 3:  # SHORT
                vals = struct.unpack(f"{bo}{count}H", raw[: count * 2])
                return vals[0] if count == 1 else vals
            elif tag_type == 4:  # LONG
                vals = struct.unpack(f"{bo}{count}I", raw[: count * 4])
                return vals[0] if count == 1 else vals
            elif tag_type == 5:  # RATIONAL
                vals = struct.unpack(f"{bo}{count * 2}I", raw[: count * 8])
                return [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]
            elif tag_type == 7:  # UNDEFINED
                return raw[:count]
            elif tag_type == 10:  # SRATIONAL
                vals = struct.unpack(f"{bo}{count * 2}i", raw[: count * 8])
                return [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]
            else:
                return raw[:count]
        except Exception:
            return raw
