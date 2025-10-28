# Mindor Cloud Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/mindors/hass-mindor-cloud.svg)](https://github.com/mindors/hass-mindor-cloud/releases)

这是一个用于 Home Assistant 的 Mindor Cloud 集成，支持 Mindor 智能设备的控制和监控。

## 功能特性

- 🔌 **智能插座控制** - 远程开关控制
- 🌡️ **空调伴侣** - 远程控制空调
- 🏠 **窗帘控制** - 智能窗帘开关
- 📊 **传感器数据** - 实时设备状态监控
- 🔄 **实时同步** - WebSocket 实时状态更新

## 支持的设备

- Mindor 智能插座
- Mindor 智能空调
- Mindor 智能窗帘

## 安装方法

### 通过 HACS 安装（推荐）

1. 确保已安装 [HACS](https://hacs.xyz/)
2. 在 HACS 中点击 "Integrations"
3. 点击右上角的三个点，选择 "Custom repositories"
4. 添加此仓库 URL：`https://github.com/mindors/hass-mindor-cloud`
5. 类别选择 "Integration"
6. 点击 "ADD"
7. 搜索 "Mindor Cloud" 并安装

### 手动安装

1. 下载最新版本的 `mindor_cloud.zip`
2. 解压到 `custom_components/mindor_cloud/` 目录
3. 重启 Home Assistant

## 配置

1. 在 Home Assistant 中转到 **配置** > **设备与服务**
2. 点击 **添加集成**
3. 搜索 "Mindor Cloud"
4. 输入您的 Mindor 账户凭据
5. 完成配置

## 使用说明

配置完成后，您的 Mindor 设备将自动出现在 Home Assistant 中。您可以：

- 在仪表板中控制设备
- 创建自动化规则
- 监控设备状态
- 查看历史数据

## 故障排除

### 常见问题

**Q: 设备无法连接**
A: 请检查网络连接和账户凭据是否正确

**Q: 状态更新延迟**
A: 这是正常现象，云端同步可能有 1-2 秒延迟

**Q: 某些设备不显示**
A: 请确保设备已在 Mindor 官方 App 中正确配置

### 调试日志

如需启用调试日志，请在 `configuration.yaml` 中添加：

```yaml
logger:
  default: warning
  logs:
    custom_components.mindor_cloud: debug
```

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

## 支持

如果您觉得这个项目有用，请给个 ⭐️！

---
