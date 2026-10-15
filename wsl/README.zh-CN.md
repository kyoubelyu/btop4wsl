# btop4wsl

在 WSL 内查看 Windows 宿主机硬件监控数据，基于 btop 1.4.7。

CPU 沿用 btop4win 的语义：每个逻辑处理器独立显示利用率，超线程兄弟共用物理核心温度，默认显示最高核心频率。桥接通过 Windows 亲和性 API 获取映射，不依赖 WSL 虚拟 CPU 编号，也不固定核心数。

支持 CPU 温度、频率、功耗；GPU 负载、频率、硬件报告的温度、功耗与显存；已挂载 Windows 磁盘的容量、实际 SSD 温度和读写速率。IO 和温度来自物理磁盘，同盘多个分区共享这些读数。未报告的传感器显示不可用。Warning/Critical Temperature 属于阈值，不混作当前温度。

内存、网络和进程面板保留 WSL 原生统计；进程操作作用于 Linux 进程。

## 安装

Windows 安装并启动 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases)，启用 Remote Web Server，并允许 WSL 访问该服务。

```bash
git clone https://github.com/kyoubelyu/btop4wsl.git
cd btop4wsl
make -j4 GPU_SUPPORT=true
bash wsl/install.sh
sudo bash wsl/wsl-root-setup.sh
hash -r
btop4wsl
```

需要 GCC 13+、make、Python 3.10+，后台服务安装需要 systemd。发布包包含 Ubuntu 24.04 构建的 Linux x86-64 程序，解压后可跳过编译步骤。用户程序默认安装在 `~/.local`，发行版原程序保留。

## 配置与使用

桥接统一配置：`/etc/btop4wsl.conf`，参见 [配置示例](bridge.conf.example)。支持修改 LHM URL、主机、端口、采样间隔、过期时间、状态目录、PowerShell 路径和可选温度上限。默认自动发现 Windows 网关/localhost、盘符、核心拓扑及 PowerShell 所在挂载盘。Intel TjMax 从温度与余量推导，未知上限不会伪装成硬件读数。

命令行参数优先于 `BTOP4WSL_*` 环境变量，环境变量优先于 INI 文件。`BTOP4WSL_CONFIG` 指定统一配置路径；客户端和后台服务应使用相同路径/状态目录。

用户界面配置：`~/.config/btop/btop-wsl.conf`（尊重 `XDG_CONFIG_HOME`），与原版配置分开。`wsl/install.sh --prefix DIR` 指定安装位置；`--configure` 重新启用推荐布局。root 安装器提供 `--dry-run`、`--prefix`、`--config`、`--state-dir`、`--url` 和 `--no-start`。默认不覆盖 `/sys/class/hwmon`；旧版兼容可启用 `--compat-hwmon`。

`1`–`4` 开关主面板，`5` 查看 GPU 详情，`i` 切换磁盘 IO 图，`p` 切换预设。`btop4wsl --preset 4` 仅显示 CPU，为小终端留出空间。

```bash
btop-hardware
btop-hardware --json
systemctl status wsl-hwmon
journalctl -u wsl-hwmon -n 30
```

正常采样、断线、快照文件消失和恢复均会更新当前状态。历史曲线保留历史含义，过期的当前温度/频率不会继续显示。

## 测试与来源

[主 README](../README.md#test) 列出单元测试、快照验证和终端测试命令。终端默认使用合成数据，不依赖某台电脑的 CPU 或盘符。

保留 [btop 上游文档](../README.upstream.md)、提交历史及 [Apache-2.0 许可证](../LICENSE)。LibreHardwareMonitor 为外部依赖，不附带在仓库中。
