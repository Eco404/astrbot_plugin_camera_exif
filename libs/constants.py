"""
相机EXIF分析插件 — 常量定义模块
包含 RAW 扩展名映射、快门标签映射、DNG 标签定义、厂商签名、
EXIF 标签中英文对照、曝光/测光/闪光灯模式映射等。
"""

# ================================================================
# RAW 文件扩展名映射
# ================================================================
RAW_EXTENSIONS: dict[str, str] = {
    # Canon
    ".cr2": "Canon CR2",
    ".cr3": "Canon CR3",
    ".crw": "Canon CRW",
    # Nikon
    ".nef": "Nikon NEF",
    ".nrw": "Nikon NRW",
    # Sony
    ".arw": "Sony ARW",
    ".srf": "Sony SRF",
    ".sr2": "Sony SR2",
    # Fujifilm
    ".raf": "Fujifilm RAF",
    # Olympus / OM System
    ".orf": "Olympus ORF",
    # Panasonic
    ".rw2": "Panasonic RW2",
    # Pentax / Ricoh
    ".pef": "Pentax PEF",
    # DNG (统一处理，多厂商使用)
    ".dng": "DNG (Digital Negative)",
    # Leica
    ".raw": "Leica RAW",
    ".rwl": "Leica RWL",
    # Hasselblad
    ".3fr": "Hasselblad 3FR",
    ".fff": "Hasselblad FFF",
    # Phase One
    ".iiq": "Phase One IIQ",
    # Samsung
    ".srw": "Samsung SRW",
    # Minolta
    ".mrw": "Minolta MRW",
    # Sigma
    ".x3f": "Sigma X3F",
    # Epson
    ".erf": "Epson ERF",
    # GoPro
    ".gpr": "GoPro GPR",
}

# ================================================================
# 所有支持的图片 / RAW 文件扩展名
# ================================================================
ALL_IMAGE_EXTS: set[str] = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
    ".heic",
    ".heif",
    ".bmp",
} | set(RAW_EXTENSIONS.keys())

# ================================================================
# DNG 来源厂商映射（根据 Make/UniqueCameraModel 推断）
# ================================================================
DNG_VENDOR_PATTERNS: dict[str, str] = {
    "Leica": "LEICA",
    "Hasselblad": "HASSELBLAD",
    "Pentax": "PENTAX",
    "RICOH": "PENTAX",
    "DJI": "DJI",
    "Apple": "APPLE",
    "GoPro": "GOPRO",
    "Samsung": "SAMSUNG",
    "Sigma": "SIGMA",
}

# ================================================================
# 快门次数 MakerNote 标签映射（按厂商）
# ================================================================
SHUTTER_COUNT_TAGS: dict[str, list[int | str]] = {
    "Canon": [0x0093, 0x0095, 0x0096, 0x0099, "ImageCount", "TotalShutterCount"],
    "NIKON": [0x00A7, 0x00A8, "ShutterCount", "TotalShutterReleases"],
    "SONY": [
        0x9400,
        0x9401,
        0x9402,
        0x9403,
        0x940E,
        "ShutterCount",
        "ImageCount",
    ],
    "FUJIFILM": [0x0010, 0x1431, "ImageCount", "ShutterCount"],
    "PENTAX": [0x003E, 0x004D, "ShutterCount"],
    "OLYMPUS": [0x0207, "ShutterCount", "ImageCount"],
    "Panasonic": [0x0032, "ShutterCount"],
    "LEICA": [0x0010, "ShutterCount"],
    "Minolta": [0x0020, "ShutterCount"],
    "HASSELBLAD": [0x0030, "ShutterCount"],
    "SAMSUNG": [0x0031, "ShutterCount", "ImageCount"],
    "PHASEONE": [0x0102, "ShutterCount"],
    "SIGMA": [0x0018, "ShutterCount", "ImageCount"],
    "DJI": [0x0001, "ShutterCount"],
    "APPLE": [0x0001, "ShutterCount"],
    "GOPRO": [0x0001, "ShutterCount"],
}

# ================================================================
# 快门次数关键字（用于文本匹配）
# ================================================================
SHUTTER_COUNT_KEYWORDS: list[str] = [
    "shuttercount",
    "shutter count",
    "shutter",
    "imagecount",
    "image count",
    "image number",
    "totalpictures",
    "total pictures",
    "totalshutterreleases",
    "total shutter releases",
    "totalshutter",
    "total shutter",
    "mechanicalshuttercount",
]

# ================================================================
# 厂商 MakerNote 签名（用于二进制 TIFF IFD 定位）
# ================================================================
VENDOR_SIGNATURES: list[tuple[bytes, int, str]] = [
    # (签名字节, 签名后到 TIFF 头的偏移, 厂商名)
    (b"Nikon\x00", 10, "NIKON"),
    (b"NIKON\x00", 10, "NIKON"),
    (b"SONY DSC", 12, "SONY"),
    (b"FUJIFILM", 8, "FUJIFILM"),
    (b"OLYMPUS", 8, "OLYMPUS"),
    (b"Panasonic", 8, "Panasonic"),
    (b"PENTAX", 8, "PENTAX"),
    (b"AOC", 4, "PENTAX"),  # Pentax 旧格式
    (b"LEICA", 8, "LEICA"),
    (b"Minolta", 8, "Minolta"),
    (b"HASSELBLAD", 8, "HASSELBLAD"),
    (b"SAMSUNG", 8, "SAMSUNG"),
    (b"Phase One", 10, "PHASEONE"),
    (b"SIGMA", 8, "SIGMA"),
    (b"DJI", 4, "DJI"),
    (b"GoPro", 8, "GOPRO"),
]

# ================================================================
# DNG 标准标签（TagID → 中文名称）
# ================================================================
DNG_TAGS: dict[int, tuple[str, str]] = {
    # TagID: (英文名, 中文名)
    0xC612: ("DNGVersion", "DNG版本"),
    0xC613: ("DNGBackwardVersion", "DNG向后兼容版本"),
    0xC614: ("UniqueCameraModel", "唯一相机型号"),
    0xC615: ("LocalizedCameraModel", "本地化相机型号"),
    0xC616: ("CFAPlaneColor", "CFA平面颜色"),
    0xC617: ("CFALayout", "CFA布局"),
    0xC618: ("LinearizationTable", "线性化表"),
    0xC619: ("BlackLevel", "黑电平"),
    0xC61A: ("WhiteLevel", "白电平"),
    0xC61B: ("DefaultScale", "默认缩放"),
    0xC61C: ("DefaultCropOrigin", "默认裁切原点"),
    0xC61D: ("DefaultCropSize", "默认裁切尺寸"),
    0xC61E: ("ColorMatrix1", "色彩矩阵1"),
    0xC61F: ("ColorMatrix2", "色彩矩阵2"),
    0xC620: ("CameraCalibration1", "相机校准1"),
    0xC621: ("CameraCalibration2", "相机校准2"),
    0xC622: ("ReductionMatrix1", "降维矩阵1"),
    0xC623: ("ReductionMatrix2", "降维矩阵2"),
    0xC624: ("AnalogBalance", "模拟白平衡"),
    0xC625: ("AsShotNeutral", "拍摄时中性色"),
    0xC626: ("AsShotWhiteXY", "拍摄时白点XY"),
    0xC627: ("BaselineExposure", "基准曝光"),
    0xC628: ("BaselineNoise", "基准噪声"),
    0xC629: ("BaselineSharpness", "基准锐度"),
    0xC62A: ("BayerGreenSplit", "拜耳绿色分离"),
    0xC62B: ("LinearResponseLimit", "线性响应限制"),
    0xC62C: ("CameraSerialNumber", "相机序列号"),
    0xC62D: ("LensInfo", "镜头信息"),
    0xC62E: ("ChromaBlurRadius", "色度模糊半径"),
    0xC62F: ("AntiAliasStrength", "抗锯齿强度"),
    0xC630: ("ShadowScale", "阴影缩放"),
    0xC634: ("DNGPrivateData", "DNG私有数据"),
    0xC635: ("MakerNoteSafety", "MakerNote安全性"),
    0xC636: ("CalibrationIlluminant1", "校准光源1"),
    0xC637: ("CalibrationIlluminant2", "校准光源2"),
    0xC638: ("BestQualityScale", "最佳质量缩放"),
    0xC639: ("RawDataUniqueID", "RAW数据唯一ID"),
    0xC63A: ("OriginalRawFileName", "原始RAW文件名"),
    0xC63B: ("OriginalRawFileData", "原始RAW文件数据"),
    0xC63C: ("ActiveArea", "有效区域"),
    0xC63D: ("MaskedAreas", "遮罩区域"),
    0xC63E: ("AsShotICCProfile", "拍摄时ICC配置文件"),
    0xC63F: ("AsShotPreProfileMatrix", "拍摄前色彩矩阵"),
    0xC640: ("CurrentICCProfile", "当前ICC配置文件"),
    0xC641: ("CurrentPreProfileMatrix", "当前色彩矩阵"),
    0xC65A: ("CalibrationIlluminant3", "校准光源3"),
    0xC65B: ("CameraCalibration3", "相机校准3"),
    0xC65C: ("ColorMatrix3", "色彩矩阵3"),
    0xC65D: ("ForwardMatrix1", "前向矩阵1"),
    0xC65E: ("ForwardMatrix2", "前向矩阵2"),
    0xC65F: ("ForwardMatrix3", "前向矩阵3"),
    0xC660: ("PreviewApplicationName", "预览应用名称"),
    0xC661: ("PreviewApplicationVersion", "预览应用版本"),
    0xC662: ("PreviewSettingsName", "预览设置名称"),
    0xC663: ("PreviewSettingsDigest", "预览设置摘要"),
    0xC664: ("PreviewColorSpace", "预览色彩空间"),
    0xC665: ("PreviewDateTime", "预览日期时间"),
    0xC666: ("RawImageDigest", "RAW图像摘要"),
    0xC667: ("OriginalRawFileDigest", "原始RAW文件摘要"),
    0xC668: ("SubTileBlockSize", "子块大小"),
    0xC669: ("RowInterleaveFactor", "行交错因子"),
    0xC66A: ("ProfileLookTableData", "配置文件查找表"),
    0xC66B: ("ProfileLookTableEncoding", "配置文件查找表编码"),
    0xC66C: ("ProfileLookTableDims", "配置文件查找表维度"),
    0xC66D: ("ProfileHueSatMapData1", "色调饱和度映射1"),
    0xC66E: ("ProfileHueSatMapData2", "色调饱和度映射2"),
    0xC66F: ("ProfileHueSatMapData3", "色调饱和度映射3"),
    0xC670: ("ProfileHueSatMapEncoding", "色调饱和度映射编码"),
    0xC671: ("ProfileHueSatMapDims", "色调饱和度映射维度"),
    0xC672: ("ProfileToneCurve", "色调曲线"),
    0xC673: ("ProfileEmbedPolicy", "配置文件嵌入策略"),
    0xC674: ("ProfileCopyright", "配置文件版权"),
    0xC675: ("NoiseProfile", "噪声配置文件"),
    0xC676: ("GainMapHueSatMapData1", "增益色调饱和度映射1"),
    0xC677: ("GainMapHueSatMapData2", "增益色调饱和度映射2"),
    0xC678: ("GainMapHueSatMapData3", "增益色调饱和度映射3"),
    0xC679: ("GainMapHueSatMapEncoding", "增益色调饱和度映射编码"),
    0xC67A: ("GainMapHueSatMapDims", "增益色调饱和度映射维度"),
    0xC67B: ("SemanticInstanceID", "语义实例ID"),
    0xC67C: ("SemanticMask", "语义遮罩"),
    0xC67D: ("CalibrationSemanticMask", "校准语义遮罩"),
    0xC67E: ("JPGCompressedSize", "JPG压缩大小"),
    0xC67F: ("JPEGTables", "JPEG表"),
    0xC680: ("DefaultUserCrop", "默认用户裁切"),
}

# ================================================================
# EXIF 标签名称 → 中文名称映射
# ================================================================
EXIF_TAG_NAMES_CN: dict[str, str] = {
    # 基础信息
    "Make": "相机品牌",
    "Model": "相机型号",
    "Software": "处理软件",
    "DateTime": "修改时间",
    "DateTimeOriginal": "原始拍摄时间",
    "DateTimeDigitized": "数字化时间",
    "SubSecTimeOriginal": "亚秒时间",
    "ImageDescription": "图像描述",
    "Orientation": "方向",
    "Artist": "作者",
    "Copyright": "版权",
    # 拍摄参数
    "ExposureTime": "快门速度",
    "FNumber": "光圈值",
    "ExposureProgram": "曝光程序",
    "ISOSpeedRatings": "ISO感光度",
    "FocalLength": "焦距",
    "FocalLengthIn35mmFilm": "35mm等效焦距",
    "ExposureBiasValue": "曝光补偿",
    "MaxApertureValue": "最大光圈",
    "MeteringMode": "测光模式",
    "Flash": "闪光灯",
    "WhiteBalance": "白平衡",
    "ExposureMode": "曝光模式(Exif)",
    "ColorSpace": "色彩空间",
    "SceneCaptureType": "场景类型",
    "Contrast": "对比度",
    "Saturation": "饱和度",
    "Sharpness": "锐度",
    "GainControl": "增益控制",
    "LightSource": "光源",
    "SubjectDistance": "拍摄距离",
    "DigitalZoomRatio": "数码变焦比",
    "ShutterSpeedValue": "快门速度值(APEX)",
    "ApertureValue": "光圈值(APEX)",
    "BrightnessValue": "亮度值(APEX)",
    # 镜头信息
    "LensModel": "镜头型号",
    "LensMake": "镜头品牌",
    "LensSpecification": "镜头规格",
    "LensSerialNumber": "镜头序列号",
    # 序列号
    "BodySerialNumber": "机身序列号",
    "SerialNumber": "序列号",
    "CameraSerialNumber": "相机序列号(DNG)",
    # 图片信息
    "ImageWidth": "图片宽度",
    "ImageLength": "图片高度",
    "PixelXDimension": "有效像素宽度",
    "PixelYDimension": "有效像素高度",
    "CompressedBitsPerPixel": "压缩比特率",
    "ComponentsConfiguration": "分量配置",
    # GPS
    "GPSLatitude": "GPS纬度",
    "GPSLongitude": "GPS经度",
    "GPSAltitude": "GPS海拔",
    "GPSInfo": "GPS信息",
    "GPSVersionID": "GPS版本",
    "GPSLatitudeRef": "纬度参考",
    "GPSLongitudeRef": "经度参考",
    "GPSAltitudeRef": "海拔参考",
    "GPSTimeStamp": "GPS时间戳",
    "GPSSatellites": "卫星数",
    "GPSStatus": "GPS状态",
    "GPSMeasureMode": "测量模式",
    "GPSDOP": "精度因子",
    "GPSSpeed": "移动速度",
    "GPSTrack": "航向",
    "GPSImgDirection": "图像方向",
    "GPSMapDatum": "大地坐标系",
    "GPSDateStamp": "GPS日期",
    # DNG 特有
    "DNGVersion": "DNG版本",
    "DNGBackwardVersion": "DNG兼容版本",
    "UniqueCameraModel": "唯一相机型号",
    "LocalizedCameraModel": "本地化相机型号",
    "OriginalRawFileName": "原始RAW文件名",
    # 快门
    "ShutterCount": "快门次数",
    "TotalShutterReleases": "总快门释放次数",
    "ImageCount": "图像计数",
    "MechanicalShutterCount": "机械快门次数",
    # XMP
    "CreatorTool": "创建工具",
    "CreateDate": "创建日期",
    "Rating": "评分",
    # 色彩
    "AsShotNeutral": "中性色参考",
    "BaselineExposure": "基准曝光补偿",
    "BaselineSharpness": "基准锐度",
    "BlackLevel": "黑电平",
    "WhiteLevel": "白电平",
}

# ================================================================
# 曝光程序映射
# ================================================================
EXPOSURE_PROGRAMS: dict[int, str] = {
    0: "未定义",
    1: "手动(M)",
    2: "程序自动(P)",
    3: "光圈优先(A/Av)",
    4: "快门优先(S/Tv)",
    5: "创意程序",
    6: "运动模式",
    7: "人像模式",
    8: "风景模式",
    9: "夜景模式",
}

# ================================================================
# 测光模式映射
# ================================================================
METERING_MODES: dict[int, str] = {
    0: "未知",
    1: "平均测光",
    2: "中央重点测光",
    3: "点测光",
    4: "多点测光",
    5: "多区测光(评价测光)",
    6: "局部测光",
    255: "其他",
}

# ================================================================
# 闪光灯状态映射
# ================================================================
FLASH_STATUS: dict[int, str] = {
    0x0: "未闪光",
    0x1: "已闪光",
    0x5: "闪光(未检测到返回光)",
    0x7: "闪光(检测到返回光)",
    0x8: "关闭",
    0x9: "强制闪光",
    0xD: "强制闪光(未检测到返回光)",
    0xF: "强制闪光(检测到返回光)",
    0x10: "未闪光(强制关闭)",
    0x18: "自动",
    0x19: "自动(已闪光)",
    0x1D: "自动(闪光,未检测到返回光)",
    0x1F: "自动(闪光,检测到返回光)",
    0x20: "无闪光功能",
    0x41: "防红眼",
    0x45: "防红眼(未检测到返回光)",
    0x47: "防红眼(检测到返回光)",
    0x49: "防红眼(强制闪光)",
    0x4D: "防红眼(强制,未检测到返回光)",
    0x4F: "防红眼(强制,检测到返回光)",
    0x59: "防红眼(自动闪光)",
    0x5D: "防红眼(自动,未检测到返回光)",
    0x5F: "防红眼(自动,检测到返回光)",
}

# ================================================================
# 白平衡模式映射
# ================================================================
WHITE_BALANCE_MODES: dict[int, str] = {
    0: "自动",
    1: "手动",
}

# ================================================================
# 色彩空间映射
# ================================================================
COLOR_SPACES: dict[int, str] = {
    1: "sRGB",
    2: "Adobe RGB",
    65535: "未校准",
}

# ================================================================
# 方向映射
# ================================================================
ORIENTATIONS: dict[int, str] = {
    1: "正常",
    2: "水平翻转",
    3: "旋转180°",
    4: "垂直翻转",
    5: "顺时针90°+水平翻转",
    6: "顺时针90°",
    7: "逆时针90°+水平翻转",
    8: "逆时针90°",
}

# ================================================================
# 配置字段 → EXIF 标签映射
# ================================================================
CONFIG_TO_TAG: dict[str, str] = {
    "camera_make": "Make",
    "camera_model": "Model",
    "lens_model": "LensModel",
    "focal_length": "FocalLength",
    "aperture": "FNumber",
    "shutter_speed": "ExposureTime",
    "iso": "ISOSpeedRatings",
    "exposure_mode": "ExposureProgram",
    "white_balance": "WhiteBalance",
    "metering_mode": "MeteringMode",
    "flash": "Flash",
    "exposure_compensation": "ExposureBiasValue",
    "date_time": "DateTimeOriginal",
    "image_size": "ImageWidth",
    "gps": "gps",
    "software": "Software",
    "shutter_count": "shutter_count",
    "serial_number": "BodySerialNumber",
}

# ================================================================
# 字段查询指令映射
# ================================================================
FIELD_COMMAND_MAP: dict[str, str] = {
    "快门次数": "shutter_count",
    "快门次数查询": "shutter_count",
    "shuttercount": "shutter_count",
    "sc": "shutter_count",
    "快门": "ExposureTime",
    "快门速度": "ExposureTime",
    "快门值": "ExposureTime",
    "快门查询": "ExposureTime",
    "曝光时间": "ExposureTime",
    "曝光时间查询": "ExposureTime",
    "shutterspeed": "ExposureTime",
    "shutter": "ExposureTime",
    "相机型号": "Model",
    "相机型号查询": "Model",
    "型号": "Model",
    "型号查询": "Model",
    "model": "Model",
    "相机品牌": "Make",
    "相机品牌查询": "Make",
    "品牌": "Make",
    "品牌查询": "Make",
    "make": "Make",
    "镜头型号": "LensModel",
    "镜头型号查询": "LensModel",
    "镜头": "LensModel",
    "镜头查询": "LensModel",
    "lens": "LensModel",
    "镜头品牌": "LensMake",
    "镜头品牌查询": "LensMake",
    "lensmake": "LensMake",
    "焦距": "FocalLength",
    "焦距查询": "FocalLength",
    "focal": "FocalLength",
    "focallength": "FocalLength",
    "光圈": "FNumber",
    "光圈查询": "FNumber",
    "光圈值": "FNumber",
    "光圈值查询": "FNumber",
    "aperture": "FNumber",
    "fnumber": "FNumber",
    "ISO": "ISOSpeedRatings",
    "ISO查询": "ISOSpeedRatings",
    "ISO感光度": "ISOSpeedRatings",
    "感光度": "ISOSpeedRatings",
    "感光度查询": "ISOSpeedRatings",
    "ISOSpeed": "ISOSpeedRatings",
    "测光模式": "MeteringMode",
    "测光模式查询": "MeteringMode",
    "测光": "MeteringMode",
    "测光查询": "MeteringMode",
    "metering": "MeteringMode",
    "曝光模式": "ExposureProgram",
    "曝光模式查询": "ExposureProgram",
    "曝光": "ExposureProgram",
    "曝光查询": "ExposureProgram",
    "exposure": "ExposureProgram",
    "曝光补偿": "ExposureBiasValue",
    "曝光补偿查询": "ExposureBiasValue",
    "EV": "ExposureBiasValue",
    "exposurebias": "ExposureBiasValue",
    "闪光灯": "Flash",
    "闪光灯查询": "Flash",
    "闪光": "Flash",
    "闪光查询": "Flash",
    "flash": "Flash",
    "白平衡": "WhiteBalance",
    "白平衡查询": "WhiteBalance",
    "whitebalance": "WhiteBalance",
    "wb": "WhiteBalance",
    "拍摄时间": "DateTimeOriginal",
    "拍摄时间查询": "DateTimeOriginal",
    "时间": "DateTimeOriginal",
    "时间查询": "DateTimeOriginal",
    "datetime": "DateTimeOriginal",
    "date": "DateTimeOriginal",
    "机身序列号": "BodySerialNumber",
    "机身序列号查询": "BodySerialNumber",
    "序列号": "BodySerialNumber",
    "序列号查询": "BodySerialNumber",
    "serial": "BodySerialNumber",
    "sn": "BodySerialNumber",
    "图片尺寸": "image_size",
    "图片尺寸查询": "image_size",
    "尺寸": "image_size",
    "尺寸查询": "image_size",
    "分辨率": "image_size",
    "分辨率查询": "image_size",
    "size": "image_size",
    "resolution": "image_size",
    "处理软件": "Software",
    "处理软件查询": "Software",
    "软件": "Software",
    "软件查询": "Software",
    "software": "Software",
    "GPS": "gps",
    "GPS查询": "gps",
    "GPS信息": "gps",
    "位置": "gps",
    "位置查询": "gps",
    "定位": "gps",
    "定位查询": "gps",
}

FIELD_CN_NAMES: dict[str, str] = {
    "shutter_count": "快门次数",
    "Make": "相机品牌",
    "Model": "相机型号",
    "LensModel": "镜头型号",
    "LensMake": "镜头品牌",
    "FocalLength": "焦距",
    "FNumber": "光圈值",
    "ExposureTime": "快门速度",
    "ISOSpeedRatings": "ISO感光度",
    "ExposureBiasValue": "曝光补偿",
    "ExposureProgram": "曝光模式",
    "MeteringMode": "测光模式",
    "Flash": "闪光灯",
    "WhiteBalance": "白平衡",
    "DateTimeOriginal": "拍摄时间",
    "BodySerialNumber": "机身序列号",
    "Software": "处理软件",
    "image_size": "图片尺寸",
    "gps": "GPS信息",
}

# ================================================================
# MakerNote 详细字段中文映射
# ================================================================
MAKERNOTE_CN_MAP: dict[str, str] = {
    "af_fine_tune": "AF微调",
    "af_tune": "AF微调",
    "focus_distance": "对焦距离",
    "focus_mode": "对焦模式",
    "vibration_reduction": "防抖",
    "vr_mode": "防抖模式",
    "active_d_lighting": "动态D-Lighting",
    "picture_control": "照片调控",
    "high_iso_nr": "高ISO降噪",
    "lens_type": "镜头类型",
    "flash_mode": "闪光模式",
    "flash_compensation": "闪光补偿",
    "image_stabilization": "图像稳定",
    "focus_point": "对焦点",
    "af_area_mode": "AF区域模式",
    "auto_bracket": "自动包围",
    "hdr": "HDR模式",
    "multi_exposure": "多重曝光",
    "sensor_pixel_size": "传感器像素尺寸",
    "serial_number": "序列号",
}

# ================================================================
# Canon MakerNote ModelID → 型号名称映射
# ================================================================
CANON_MODEL_MAP: dict[int, str] = {
    # EOS DSLR
    0x80000001: "EOS-1D",
    0x80000167: "EOS-1Ds",
    0x80000168: "EOS 10D",
    0x80000169: "EOS-1D Mark III",
    0x80000170: "EOS Digital Rebel / 300D",
    0x80000174: "EOS-1D Mark II",
    0x80000175: "EOS 20D",
    0x80000176: "EOS-1Ds Mark II",
    0x80000188: "EOS Digital Rebel XT / 350D",
    0x80000189: "EOS Digital Rebel XTi / 400D",
    0x80000190: "EOS 5D",
    0x80000192: "EOS 30D",
    0x80000213: "EOS-1D Mark II N",
    0x80000215: "EOS-1D Mark IV",
    0x80000218: "EOS-1Ds Mark III",
    0x80000232: "EOS Digital Rebel XSi / 450D",
    0x80000234: "EOS 40D",
    0x80000236: "EOS Digital Rebel XS / 1000D",
    0x80000250: "EOS 50D",
    0x80000252: "EOS 5D Mark II",
    0x80000254: "EOS Digital Rebel T1i / 500D",
    0x80000261: "EOS 7D",
    0x80000269: "EOS-1D X",
    0x80000270: "EOS Digital Rebel T2i / 550D",
    0x80000271: "EOS 60D",
    0x80000273: "EOS Digital Rebel T3i / 600D",
    0x80000281: "EOS Digital Rebel T3 / 1100D",
    0x80000283: "EOS 5D Mark III",
    0x80000285: "EOS Digital Rebel T4i / 650D",
    0x80000287: "EOS 6D",
    0x80000288: "EOS-1D C",
    0x80000298: "EOS 70D",
    0x80000301: "EOS Digital Rebel T5i / 700D",
    0x80000302: "EOS Rebel SL1 / 100D",
    0x80000324: "EOS 5D Mark III (firmware 1.2+)",
    0x80000325: "EOS 70D (firmware 1.1+)",
    0x80000326: "EOS 6D (firmware 1.1+)",
    0x80000328: "EOS-1D X (firmware 2.0+)",
    0x80000331: "EOS Rebel T5 / 1200D",
    0x80000346: "EOS Rebel T6i / 750D",
    0x80000347: "EOS Rebel T6s / 760D",
    0x80000349: "EOS 5DS",
    0x80000350: "EOS 5DS R",
    0x80000355: "EOS 5D Mark IV",
    0x80000382: "EOS 80D",
    0x80000384: "EOS-1D X Mark II",
    0x80000387: "EOS Rebel T7i / 800D",
    0x80000388: "EOS 77D / 9000D",
    0x80000392: "EOS 6D Mark II",
    0x80000393: "EOS Rebel SL2 / 200D",
    0x80000401: "EOS M50",
    0x80000404: "EOS Rebel T7 / 2000D",
    0x80000406: "EOS 5D Mark IV (firmware)",
    0x80000408: "EOS R",
    0x80000410: "EOS RP",
    0x80000412: "EOS 90D",
    0x80000414: "EOS M6 Mark II",
    0x80000416: "EOS M200",
    0x80000417: "EOS-1D X Mark III",
    0x80000421: "EOS Rebel T8i / 850D",
    0x80000424: "EOS R5",
    0x80000427: "EOS R6",
    0x80000428: "EOS R3",
    0x80000432: "EOS R7",
    0x80000433: "EOS R10",
    0x80000435: "EOS R6 Mark II",
    0x80000436: "EOS R8",
    0x80000439: "EOS R50",
    0x80000442: "EOS R100",
    0x80000446: "EOS R5 Mark II",
    0x80000447: "EOS R1",
    # PowerShot
    0x1010000: "PowerShot G1",
    0x1040000: "PowerShot G3",
    0x1060000: "PowerShot G5",
    0x1080000: "PowerShot Pro1",
    0x1090000: "PowerShot S60",
    0x1100000: "PowerShot S70",
    0x1110000: "PowerShot G6",
    0x1120000: "PowerShot S80",
    0x1130000: "PowerShot G7",
    0x1140000: "PowerShot G9",
    0x1150000: "PowerShot G10",
    0x1160000: "PowerShot G11",
    0x1170000: "PowerShot G12",
    0x1180000: "PowerShot G15",
    0x1190000: "PowerShot G16",
    0x1200000: "PowerShot G1 X",
    0x1210000: "PowerShot G1 X Mark II",
    0x1220000: "PowerShot G1 X Mark III",
    0x1230000: "PowerShot G3 X",
    0x1240000: "PowerShot G5 X",
    0x1250000: "PowerShot G7 X",
    0x1260000: "PowerShot G7 X Mark II",
    0x1270000: "PowerShot G9 X",
    0x1280000: "PowerShot G9 X Mark II",
    0x1290000: "PowerShot SX1 IS",
    0x1300000: "PowerShot S90",
    0x1310000: "PowerShot S95",
    0x1320000: "PowerShot S100",
    0x1330000: "PowerShot S110",
    0x1340000: "PowerShot S120",
}
