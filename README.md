# 📸 相机EXIF分析插件

AstrBot 相机图片 EXIF 元数据自动检测与解析插件。

自动识别接收的图片是否为相机拍摄，提取完整的 EXIF 数据（快门次数、光圈、ISO、焦距、GPS 等），支持所有主流相机厂商 RAW/DNG 格式解析，自动回传元数据至原始聊天渠道。

---

## ✨ 核心功能

- 🔍 **自动检测**：接收图片后自动分析 EXIF，无需手动触发
- 📊 **19 种字段指令**：独立查询快门次数、光圈、焦距、ISO 等
- 📸 **RAW 全格式**：CR2/CR3/NEF/ARW/RAF/ORF/RW2/PEF/DNG 等 20+ 格式
- 📦 **DNG 深度解析**：80+ DNG 标准标签 + PrivateData 厂商私有数据
- 🎯 **Canon MakerNote ModelID**：从 MakerNote Tag 0x0010 直接解析 EOS 型号
- 📱 **手机识别**：正确区分 iPhone/Android 与专业相机
- 🗺️ **GPS 地图**：支持高德/百度/腾讯/Google/OSM，自动坐标系转换
- 📋 **多源合并**：PIL + exifread + rawpy + DNG  + XMP 并行提取+智能去重
- 📊 **数据来源追踪**：每个标签标注来源（pil/exifread/dng/xmp）
- 🔐 **权限隔离**：每人只能查自己发的图，A 群图片 B 群不可查
- ⏳ **等待模式**：输入指令后等待发图，超时自动退出
- 🛡️ **安全**：频率限制、黑白名单、路径校验、分析超时

---

## 📋 可用指令

### 完整查询
| 指令 | 别名 | 说明 |
|------|------|------|
| `/exif` | — | 查询完整 EXIF 元数据 |
| `/exif帮助` | `/exif help` `/exif菜单` | 显示帮助菜单 |

### 字段单独查询
| 指令 | 别名 | 说明 |
|------|------|------|
| `/快门次数` | `/shuttercount` `/SC` | 快门使用次数 |
| `/快门` | `/shutterspeed` | 快门速度 |
| `/相机型号` | `/model` | 相机型号 |
| `/相机品牌` | `/make` | 相机品牌/制造商 |
| `/镜头型号` | `/lens` | 镜头型号 |
| `/镜头品牌` | `/lensmake` | 镜头品牌 |
| `/焦距` | `/focal` | 拍摄焦距 |
| `/光圈` | `/aperture` | 光圈值 |
| `/ISO` | `/iso` | ISO感光度 |
| `/测光模式` | `/metering` | 测光模式 |
| `/曝光模式` | `/exposure` | 曝光模式 |
| `/曝光补偿` | `/EV` | 曝光补偿值 |
| `/闪光灯` | `/flash` | 闪光灯状态 |
| `/白平衡` | `/wb` | 白平衡设置 |
| `/拍摄时间` | `/datetime` | 原始拍摄时间 |
| `/机身序列号` | `/serial` `/sn` | 相机机身序列号 |
| `/图片尺寸` | `/resolution` `/size` | 图片分辨率/尺寸 |
| `/处理软件` | `/software` | 后期处理软件 |
| `/GPS` | `/位置` | GPS 位置信息 |

### 使用方式
1. `[图片] /exif` — 发送图片的同时附带指令
2. `[引用图片] /exif` — @引用已发送的图片后输入指令
3. `/exif` → 等待图片 — 先输入指令，再发图片（超时 120s）

---

## 📋 EXIF 显示格式

```
📸 完整 EXIF 元数据
════════════════════════════════════
文件: DSC_2095.NEF (Nikon NEF)
大小: 28.52 MB
────────────────────────────────────
📷 器材
  NIKON CORPORATION NIKON Z 6_2, NIKKOR Z 24-50mm f/4-6.3
  机身序列号: 10000000
────────────────────────────────────
🎯 模式
  曝光模式:Manual, 测光模式:Spot, 曝光补偿:0
────────────────────────────────────
⚙️ 曝光
  光圈:63/10, 快门:1/100秒, ISO1250
  焦距: 45.0 mm (35mm等效: 45.0 mm), 视角:51.3°
────────────────────────────────────
🎨 色彩
  白平衡:Auto, 色彩空间:sRGB
────────────────────────────────────
📅 时间
  2026:04:30 22:05:53.62
────────────────────────────────────
📷 快门次数: 1925
────────────────────────────────────
🖼️ 图片属性
  尺寸: 6048 × 4024 px
  软件: Ver.01.70
────────────────────────────────────
👤 版权信息
  作者: HAOTIANSHOUWANG_1484475153
────────────────────────────────────
📝 XMP信息:
  CreatorTool: NIKON Z 6_2 Ver.01.70
  Rating: 0
🖼️[图片预览]
[图片]

```

---

## ⚙️ 配置项

| 配置 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `enabled` | bool | true | 插件总开关 |
| `auto_detect_enabled` | bool | true | 启用自动检测 |
| `max_image_size_mb` | int | 50 | 最大处理图片大小(MB) |
| `wait_timeout_seconds` | int | 120 | 等待图片超时(秒) |
| `text_chunk_interval` | float | 1.0 | 文本分片发送间隔(秒) |
| `reply_mode` | select | 文本发送 | 文本发送/转发发送/不发送 |
| `forward_display_name` | string | 相机EXIF分析 | 转发显示名称 |
| `allow_reference_query` | bool | false | 允许引用查询他人图片 |
| `show_analyzing_hint` | bool | true | 显示"正在分析..."提示 |
| `send_preview_thumbnail` | bool | false | 发送预览图 |
| `show_detailed_exif_default` | bool | false | 默认显示详细EXIF |
| `gps_map_provider` | select | 高德地图 | GPS地图提供商 |
| `gps_custom_map_url` | string | — | 自定义地图URL模板 |
| `group_chat_filter` | object | — | 群聊黑白名单 |
| `private_chat_filter` | object | — | 私聊黑白名单 |

### 地图提供商
| 选项 | 坐标系 | 说明 |
|------|--------|------|
| 高德地图 | GCJ-02（自动转换） | 国内推荐 |
| 百度地图 | BD-09（自动转换） | 国内推荐 |
| 腾讯地图 | GCJ-02（自动转换） | 国内推荐 |
| Google Maps | WGS-84 | 海外可用 |
| OpenStreetMap | WGS-84 | 开源地图 |
| 自定义 | WGS-84 | `{lat}` `{lng}` 占位符 |

| 模式 | 效果 |
|------|------|
| **文本发送** | 自适应分片纯文本（每片≤1400字）+ 独立预览图，间隔可配 |
| **转发发送** | 合并转发 3 条消息：EXIF 数据 / 🖼️[图片预览] / 预览图 |
| **不发送** | 不回复任何消息 |

---

## 🔐 权限与安全

- **用户隔离**：A 发送的图片，B 无法跨用户查询
- **跨群隔离**：A 群图片不能在 B 群查询
- **引用控制**：`allow_reference_query=false` 时别人无法引用你的图片
- **频率限制**：10s 内自动检测≤3 次，指令≤5 次
- **路径安全**：图片清理仅限 AstrBot temp 目录
- **分析超时**：单次 45s 超时保护

---

## 📦 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/haotianshouwang/astrbot_plugin_camera_exif.git
cd astrbot_plugin_camera_exif
pip install -r requirements.txt
# 可选：RAW 深度解析
pip install rawpy numpy
```

---

## 📸 支持格式

- **图片**：JPEG / TIFF / PNG / HEIC / WebP
- **RAW**：CR2 / CR3 (Canon)、NEF / NRW (Nikon)、ARW / SRF (Sony)
  RAF (Fujifilm)、ORF (Olympus)、RW2 (Panasonic)、PEF / DNG (Pentax)
  SRW (Samsung)、3FR (Hasselblad)、MRW (Minolta)、X3F (Sigma) 等

---

## 🧪 测试状态

⚠️ 个人能力有限，目前仅测试以下机型：

| 品牌 | 机型 | JPG | RAW | 快门 |
|------|------|-----|-----|------|
| Nikon | Z6 II | ✅ | ✅ NEF | ✅ |
| Canon | 80D | ✅ | ⚠️ CR2 | ⚠️ |
| Sony | A7M5 | ✅ | ✅ ARW | ⚠️ |
| Sony | A7R6 | ✅ | ✅ ARW | ⚠️ |

> Sony ARW 快门方案已编码，无实机验证。其他品牌因器材不足未深度测试，**理论上均支持**。
 **索尼最新机器貌似都是电子快门，所以识别出不来**

🙏 **欢迎网友帮忙测试并提交 PR 反馈！** 如遇解析不全或快门获取失败，请在 [GitHub Issues](https://github.com/haotianshouwang/astrbot_plugin_camera_exif/issues) 提供机型+样片。

---

## 🧪 依赖

| 库 | 必需 | 用途 |
|----|------|------|
| Pillow >= 10.0.0 | ✅ | 基础 EXIF 提取 |
| exifread >= 3.0.0 | ✅ | 详细 MakerNote 解析 |
| aiohttp >= 3.9.0 | ✅ | 异步 HTTP |
| rawpy >= 0.18.0 | ❌ | RAW 全尺寸预览 |
| numpy >= 1.24.0 | ❌ | rawpy 依赖 |

---

## ⚠️ 已知问题

### QQ 图片压缩导致 EXIF 丢失

通过 QQ 以**图片消息**发送的照片，LLOneBot / Lagrange 等协议适配器会在传输过程中对图片重新编码，导致 IFD0 中部分 EXIF 标签被剥离（实测 PIL getexif() 从 17 个标签降至 11 个，Make / Model 可能丢失）。

**解决方案**：以**文件消息**（File）方式发送图片，可保留完整 EXIF 元数据。

---

## 👤 作者

**昊天兽王** — [GitHub](https://github.com/haotianshouwang)

## 开发过程
项目开发借助**CodeBuddy**辅助完成。

## 📄 许可证
[![CC BY-NC 4.0](https://licensebuttons.net/l/by-nc/4.0/88x31.png)](https://creativecommons.org/licenses/by-nc/4.0/)

本插件采用 CC BY-NC 4.0 协议开源，允许二次修改、免费非商业使用；**禁止商用**
