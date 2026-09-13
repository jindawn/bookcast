# Phase 3 真实公开书籍演示

完成时间：2026-09-13T23:16:49Z（北京时间 2026-09-14）。运行环境：macOS、Python 3.12，使用仓库 `bookcast acquire` 入口。此演示调用真实官方镜像，没有用测试 fixture 替代下载。

## 书名与候选

查询 `The Wealth of Nations` 返回两个独立候选：

| 候选 ID | 标题 | 目录作者/贡献者 |
| --- | --- | --- |
| gutenberg:3300 | An Inquiry into the Nature and Causes of the Wealth of Nations | Smith, Adam, 1723-1790 |
| gutenberg:38194 | An Inquiry Into the Nature and Causes of the Wealth of Nations | Smith, Adam, 1723-1790；Garnier, M. (Germain), 1754-1821 |

程序要求显式选择。本演示使用 `gutenberg:3300`，语言 en，目录发行日期 2002-06-01；isbn、edition、publication_year 均为 null。未把目录日期误认为原版出版年份。

## 实际命令

```sh
.venv/bin/bookcast acquire 'The Wealth of Nations' --list \
  --output-dir imports/phase3-demo \
  --resolve gutenberg.pglaf.org=69.55.231.8 --download-timeout 600

.venv/bin/bookcast acquire 'The Wealth of Nations' --edition gutenberg:3300 \
  --output-dir imports/phase3-demo \
  --resolve gutenberg.pglaf.org=69.55.231.8 --download-timeout 600
```

当时系统 DNS 返回 Fake-IP `198.18.0.37`，默认公网检查拒绝。通过公开 DNS HTTPS 查询核验真实 A 记录为 `69.55.231.8` 后显式指定该地址，原域名 TLS/SNI 验证保持开启。这个 IP 是本次记录，不是永久端点；普通网络可不加 --resolve，重用覆盖地址前应重新核验。

首次约 21 MB 目录下载超过默认 120 秒上限，受控中止并清理临时文件；600 秒配置完成下载。默认 64 MiB 目录限制、32 MiB 书籍限制、MIME 与文件结构校验没有放宽。

## 来源与结果

来源为 [官方镜像清单](https://www.gutenberg.org/MIRRORS.ALL) 中的 Project Gutenberg 镜像。所选书籍的 [RDF](https://gutenberg.pglaf.org/cache/epub/3300/pg3300.rdf) 在书籍层声明 `Public domain in the USA.`，记录 jurisdiction=US；保留其地区范围。下载使用 [UTF-8 TXT](https://gutenberg.pglaf.org/cache/epub/3300/pg3300.txt)，并验证头部 eBook 编号 3300。

| 项目 | 实测值 |
| --- | --- |
| 获取状态 | parsed |
| acquisition_id | 7e7a23a67db5bbc3d961bd5c |
| book_id（用于后续 Pipeline） | 79bcca4514ce7d1191bfa5e3 |
| MIME | text/plain |
| 源文件大小 | 2,468,951 字节 |
| 解析文本段 | 67（按现有 TXT 标题规则分段，含前后附文） |
| 解析文本字符总数 | 2,432,512 |
| coverage | complete |
| metadata.json SHA-256 | d4e7a4e0e04a48a54f6c8ae7c38a912ab76d6c2254e0d14fd7fe26f2f73640aa |

源文件 SHA-256：

```text
e91d52dca43fd0fd69baf7776b00a5d8e74b69675b6b608ca8a0378aaf3ae58d
```

版权 RDF SHA-256：

```text
8c9332537cf731ee0f73f186754ebdc4afbdc03f7f2eeda441299d20021c612f
```

目录 CSV：21,205,940 字节，SHA-256：

```text
e1de271b5c6bdd5039ee9995eb740b05066dec79d58492b33a0dcd9e3dc48dc2
```

产物保留在本机 `imports/phase3-demo/7e7a23a67db5bbc3d961bd5c/`，原始目录和 RDF 证据在其同级 `.catalog/gutenberg/` 下。所有生成 JSON 与源文件均核对 acquisition.json 中的 SHA-256。这些数据被 Git 忽略；仓库只保存本演示记录，其他机器运行后可重新获得产物。镜像文件可能更新，之后运行的哈希不保证相同。

重复同一获取命令会重新核验 RDF，书籍文件、全部解析产物及 acquisition.json 的字节和修改时间保持不变。生成阶段的桥接使用自制书籍通过离线端到端测试：acquire --generate → Mock MP3 → --resume，并保留身份/来源元数据。

本真实演示止于获取与解析，没有调用付费 AI 或生成《国富论》的音频。coverage 表示文本解析覆盖，印刷版次还原、章节质量与播客内容质量未在本阶段验收。
