# ecs-tools

自动收集 [CST-Cat/ecs](https://github.com/CST-Cat/ecs)（上游项目）所需的全部第三方软件的各发布版本。由 GitHub Actions 定期运行 [scripts/collect.py](scripts/collect.py)，工具清单自动跟随上游 `tools/lock.json` 的变化。

> English: this repository automatically mirrors every published version of the
> third-party software required by the upstream project
> [CST-Cat/ecs](https://github.com/CST-Cat/ecs). Collection runs on a daily
> GitHub Actions schedule ([workflow](.github/workflows/collect.yml)) and the
> tool registry follows the upstream `tools/lock.json` automatically.

## 目录结构

每个软件一个顶层文件夹；软件文件夹内按 版本 → 平台-架构 → 类型 分层存放：

```text
<软件>/<版本>/<平台-架构|all>/<类型>/<文件>
```

| 层级 | 取值 | 说明 |
|---|---|---|
| 软件 | `sysbench`、`zstd`、`openssl`、`fio`、`iperf3`、`nexttrace-tiny`、`ping`、`npb`、`stream` | 与上游 `tools/lock.json` 中的工具名一致（npb-ep / npb-ft 合并到 `npb`） |
| 版本 | 上游 tag 或版本号（如 `v1.5.7`、`3.42`） | stream 上游无版本化发布，使用 `current` 快照 |
| 平台-架构 | `linux-amd64`、`linux-arm64`、`linux-s390x`、`linux-ppc64le`、`freebsd-amd64`、`freebsd-arm64`、`all` | 只收集 Linux 与 FreeBSD 的 amd64 / arm64 / s390x（IBM Z）/ ppc64le（IBM POWER）；源码包、校验和等无平台标记的文件归入 `all` |
| 类型 | `source` / `binary` / `deb` / `rpm` / `msi` / `meta` | `meta` 为校验和与签名文件 |

示例：

```text
zstd/v1.5.7/all/source/zstd-1.5.7.tar.gz
openssl/openssl-3.5.7/all/source/openssl-3.5.7.tar.gz
fio/3.42/all/source/fio-3.42.tar.gz
nexttrace-tiny/v1.7.1/linux-amd64/binary/nexttrace-tiny_linux_amd64
ping/20250605/all/source/iputils-20250605.tar.gz
```

[index.json](index.json) 记录每个文件的大小、SHA-256、来源 URL 和抓取时间，可用于完整性校验。

## 收集范围

| 软件 | 上游 | 来源类型 | 范围 |
|---|---|---|---|
| sysbench | [akopytov/sysbench](https://github.com/akopytov/sysbench) | GitHub Releases | 全部版本（release 无资产时回退 tag 源码包） |
| zstd | [facebook/zstd](https://github.com/facebook/zstd) | GitHub Releases | 全部版本 |
| openssl | [openssl/openssl](https://github.com/openssl/openssl) | GitHub Releases | 全部稳定版本（257 个，约 2.5GB；预发布版不收） |
| fio | [axboe/fio](https://github.com/axboe/fio) | [官方源码站](https://brick.kernel.dk/snaps/) | 全部 212 个版本（GitHub Releases 只有 Windows 安装包） |
| iperf3 | [esnet/iperf](https://github.com/esnet/iperf) | GitHub Releases | 全部版本 |
| nexttrace-tiny | [nxtrace/NTrace-core](https://github.com/nxtrace/NTrace-core) | GitHub Releases | 全部含 `nexttrace-tiny_*` 变体的版本（tiny 于 2024-09 引入，仅收该变体） |
| ping | [iputils/iputils](https://github.com/iputils/iputils) | GitHub Releases | 全部版本 |
| npb（npb-ep / npb-ft） | [NASA NPB](https://www.nas.nasa.gov/software/npb.html) | 固定 URL 列表 | NASA 现存 8 个 + 存档恢复 3 个（2.4.1/3.2.1/3.3，见 [npb/PROVENANCE.md](npb/PROVENANCE.md)），共 11 个 |
| stream | [Virginia CS](https://www.cs.virginia.edu/stream/) | 固定 URL 列表 | 当前源码快照 |

全局默认只收 Linux / FreeBSD 的 amd64、arm64、s390x、ppc64le。所有工具均收集全量稳定版本（预发布版默认不收）；调整范围请编辑 [config/tools.json](config/tools.json)。

## 自动化

- 每天 02:23 UTC 定时运行，也可在 Actions 页面手动触发（`only` 输入可只收集指定软件）。
- 每次运行先读取上游 `tools/lock.json`：ecs 新增依赖软件时自动登记（GitHub 来源）并开始收集；无法自动识别来源的软件会在运行摘要中提示手工配置。
- 单文件超过 `max_asset_mb`（默认 200MB）自动跳过；失败软件不影响其他软件收集。
