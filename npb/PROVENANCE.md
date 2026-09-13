# npb 版本来源说明

NASA 官方服务器（`assets/nas/npb/`）现仅存 8 个版本（2.3、3.0、3.3.1、3.4~3.4.4），
由自动收集任务维护。以下 3 个版本来自第三方存档，因官方已不再提供而手工恢复入库，
入库前均做过指纹校验，`index.json` 中记录了各自的获取来源。

| 版本 | 来源 | 校验方式 |
|---|---|---|
| 2.4.1 | Wayback Machine 存档的 NASA 官方原始文件（2021-03-19 抓取） | gzip 完整，官方目录结构 `NPB2.4.1/NPB2.4-MPI`，内部时间戳 2004-03-01 |
| 3.2.1 | GitHub [0intro/hare](https://github.com/0intro/hare)（Plan 9 发行版内嵌的未修改源码，`sys/src/cmd/NPB3.2.1` @ main） | Changes.log 与官方 NPB3.4.4 内置日志逐字 diff 一致（仅一处官方后续修正的用词差异）；目录结构符合官方规范（SER/OMP/MPI/HPF/JAV，含 LU-HP、DC、UA） |
| 3.3 | GitHub [wzzhang-HIT/NAS-Parallel-Benchmark](https://github.com/wzzhang-HIT/NAS-Parallel-Benchmark)（2015 年快照，master） | 同上校验；顶层结构与官方 3.3.1 包一致（Changes.log、README、SER/OMP/MPI、HPF/JAV README） |

未收录：NPB 2.4、3.1、3.2 —— 官方服务器、Wayback Machine 及已发现的 GitHub 镜像
均无完整可信副本。若日后发现可信来源，可按上述校验流程补充。
