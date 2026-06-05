# 📸 相机EXIF分析插件

AstrBot 相机图片 EXIF 元数据自动检测与解析插件。

自动识别接收的图片是否为相机拍摄，提取完整的 EXIF 数据（快门次数、光圈、ISO、焦距、GPS 等），支持所有主流相机厂商 RAW 格式解析，自动回传元数据至原始聊天渠道。

---

## ✨ 核心功能

- 🔍 **自动检测**：接收图片后自动分析 EXIF，无需手动触发
- 📊 **19 种字段指令**：独立查询快门次数、光圈、焦距、ISO 等
- 🎯 **快门次数**：支持 Nikon / Canon / Sony 等品牌快门计数提取
- 📸 **RAW 格式**：支持 CR2/CR3/NEF/ARW/RAF/DNG/ORF 等 20+ 种 RAW
- 🔐 **权限隔离**：每人只能查自己发的图，A 群图片 B 群不可查
- 🖼️ **图片预览**：可选附带原尺寸压缩预览图
- 📤 **转发模式**：支持合并转发，外显自定义名称
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
| `/快门次数` | `/快门次数查询` `/shuttercount` `/SC` | 快门使用次数(如 2863) |
| `/快门` | `/快门速度` `/曝光时间` `/shutterspeed` | 快门速度(如 1/25s) |
| `/相机型号` | `/型号` `/model` | 相机型号 |
| `/相机品牌` | `/品牌` `/make` | 相机品牌/制造商 |
| `/镜头型号` | `/镜头` `/lens` | 镜头型号 |
| `/镜头品牌` | `/lensmake` | 镜头品牌 |
| `/焦距` | `/focal` `/focallength` | 拍摄焦距 |
| `/光圈` | `/光圈值` `/aperture` `/fnumber` | 光圈值 |
| `/ISO` | `/iso` `/感光度` `/ISO查询` | ISO感光度 |
| `/测光模式` | `/测光` `/metering` | 测光模式 |
| `/曝光模式` | `/曝光` `/exposure` | 曝光模式(手动/光圈优先等) |
| `/曝光补偿` | `/EV` `/ev` | 曝光补偿值 |
| `/闪光灯` | `/闪光` `/flash` | 闪光灯状态 |
| `/白平衡` | `/whitebalance` `/wb` | 白平衡设置 |
| `/拍摄时间` | `/时间` `/datetime` | 原始拍摄时间 |
| `/机身序列号` | `/序列号` `/serial` `/sn` | 相机机身序列号 |
| `/图片尺寸` | `/分辨率` `/size` | 图片分辨率/尺寸 |
| `/处理软件` | `/软件` `/software` | 后期处理软件 |
| `/GPS` | `/位置` `/定位` | GPS 位置信息 |

### 使用方式
1. `[图片] /exif` — 发送图片的同时附带指令
2. `[引用图片] /exif` — @引用已发送的图片后输入指令
3. `/exif` → 等待图片 — 先输入指令，再发图片（可配置超时）

---

## ⚙️ 配置项

| 配置 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `enabled` | bool | true | 插件总开关 |
| `auto_detect_enabled` | bool | true | 启用自动检测 |
| `max_image_size_mb` | int | 50 | 最大处理图片大小(MB) |
| `wait_timeout_seconds` | int | 120 | 等待图片超时(秒) |
| `reply_mode` | select | 文本发送 | 回复模式：文本发送/转发发送/不发送 |
| `forward_display_name` | string | 相机EXIF分析 | 转发显示名称 |
| `allow_reference_query` | bool | false | 允许引用查询他人图片 |
| `show_analyzing_hint` | bool | true | 显示"正在分析..."提示 |
| `send_preview_thumbnail` | bool | false | 发送图片预览缩略图 |
| `show_detailed_exif_default` | bool | false | 默认显示详细EXIF |
| `group_chat_filter` | object | — | 群聊黑白名单 |
| `private_chat_filter` | object | — | 私聊黑白名单 |

---

## 🔐 权限与安全

- **用户隔离**：A 发送的图片，B 在 B 群无法查询
- **跨群隔离**：A 群图片不能在 B 群查询
- **引用控制**：关闭 `allow_reference_query` 后别人无法引用你的图片查 EXIF
- **频率限制**：10 秒内自动检测最多 3 次，指令最多 5 次
- **路径安全**：图片清理仅限 AstrBot temp 目录
- **分析超时**：单次分析 45 秒超时保护

---

## 📦 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/haotianshouwang/astrbot_plugin_camera_exif.git
cd astrbot_plugin_camera_exif
pip install -r requirements.txt
# 可选：RAW 深度解析
pip install rawpy
```

在 WebUI → 插件管理 中重载插件即可使用。

---

## 📸 支持格式

- **图片**：JPEG / TIFF / PNG / HEIC / WebP
- **RAW**：CR2 / CR3 (Canon)、NEF / NRW (Nikon)、ARW / SRF (Sony)
  RAF (Fujifilm)、ORF (Olympus)、RW2 (Panasonic)、PEF / DNG (Pentax)
  SRW (Samsung)、3FR (Hasselblad)、MRW (Minolta)、X3F (Sigma) 等

---

## 🧪 依赖

| 库 | 必需 | 用途 |
|----|------|------|
| Pillow | ✅ | 基础 EXIF 提取 |
| exifread | ✅ | 详细 MakerNote 解析 |
| rawpy | ❌ | RAW 文件深度解析（可选） |

---

## 👤 作者

**昊天兽王** — [GitHub](https://github.com/haotianshouwang)

## 📄 许可证

MIT License
