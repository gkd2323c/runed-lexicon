# SHELL-MISMATCH-002：POSIX 侧字面 `nul` 文件

## 样本与范围

本案例属于工程执行事故，与 [shell-mismatch-001](../shell-mismatch-001/README.md) 是同一失效形态的两个实例：重定向目标是**宿主绑定**的写法，在错误宿主里落到磁盘成为真实文件。001 是 PowerShell/CMD 混用产生 `$null`，本案是 POSIX（Git Bash）/cmd 混用产生 `nul`。

与 001 不同，本案已完成完整归因：命令原文、重定向符号、执行者与发生时刻均已核实（001 中对应的「未核实」项在本案全部有据）。

原件状态：**三个事故文件已清理**（内容与时间戳记录如下），未保留原始字节文件。

| 位置 | 大小 | 最后写入 |
| --- | --- | --- |
| `<repo>/nul` | 45 B | 2026-10-12 22:45:11.401 |
| `<repo>/.work/<plugin>/batches/INFO-088/nul` | 0 B | 2026-10-12 22:50:47.749 |
| `<repo>/.work/<plugin>/batches/INFO-090/nul` | 0 B | 2026-10-12 22:52:40.947 |

根目录那个 45 字节文件的可读内容（十六进制 `46 49 4e 44 53 54 52 3a 20 … 0d 0a`）：

```text
FINDSTR: Cannot open INFO-146-canonical.txt
```

两个 `.work` 下的文件为 0 字节——同一条命令形态，但对应执行没有产生 stderr，`2>nul` 仍建立了空文件。

## 事实、归因与机制

**已观察事实**：根目录存在 45 字节字面 `nul` 文件，内容是一条 `findstr` 的 stderr 诊断；写入时刻 22:45:11.401，与同批次材料 `INFO-146-canonical.txt` 的生成时刻（22:45:11.352）相差 49 毫秒。

**已核实归因**（证据链完整）：

- **执行者**：Devin CLI（本地 agent，ACP server 模式，版本 3000.10.48），会话 `humble-fahrenheit`（工作目录为仓库根，时段 22:28–23:00）。
- **执行环境**：Git Bash（POSIX）。Devin 的 exec 工具契约要求**命令以 bash 语法书写**（该要求写在其工具描述里），而会话命令中混入了 cmd 惯用的 `2>nul`。
- **命令原文**（代表性一条）：

  ```bash
  cd "<repo>" && py -3 .agents/skills/translation-review-tools/scripts/query.py \
    --xml mods/<plugin>/<plugin>_english_chinese_translated.xml \
    --idx-file .work/<plugin>/batches/INFO-146/index.txt \
    > _tmp/data/nsr-review/INFO-146-canonical.txt \
    && findstr /c:"rows" _tmp/data/nsr-review/INFO-146-canonical.txt 2>nul; \
    type _tmp\data\nsr-review\INFO-146-canonical.txt | find /c /v ""
  ```

- **重定向语义**：`2>nul` 在 cmd.exe 里是空设备；在 POSIX shell 里 `nul` 是普通文件名，重定向在命令主体执行前生效，于是 stderr（findstr 的报错）写入了工作目录下真实的 `nul` 文件。
- **同一路径族**：该会话共 14 条命令含 `2>nul`，cwd 落在仓库根与若干批次子目录，因此同一形态在多个 cwd 各留下一个 `nul`。

**同一批命令的旁证**：命令里 Windows 风格的反斜杠在 bash 中被吞（`type _tmp\data\...` 变成连串字符串而报 not found），`find /c /v ""` 被 bash 解析为「对 `/c` 挂载点跑 find」，扫描了大量系统目录。这些都是同一个病根：Windows 习惯的命令被交给 POSIX shell 解释。

## 机制与安全边界

- 空设备写法不可移植。同一 token 在不同宿主里含义相反：`>nul`（cmd 设备 / POSIX 文件）、`>$null` 与 `> $null`（PowerShell 设备 / 后者是文件）。
- 重定向先于命令主体生效，目标文件可能在命令失败前就已创建或截断。命令失败不等于无文件副作用。
- 文件系统枚举对保留名不可靠：`git ls-files` 看不见带扩展名的保留名（如 `con.log`），只有 `\\?\` 前缀直读目录项才能看到。任何只依赖单一枚举源的扫描都有盲区。
- 事故文件可落在 git 忽略的运行时目录（`.work/`、`_tmp/`）里，push 前的仓库检查天然看不见它们。

## 防线分层（本案之后的落地）

1. **规则层**：`AGENTS.md` §2.1 载明宿主 × 空设备矩阵与事故实例索引；该文件由执行者作为 always-on 规则加载。
2. **事后兜底**：`tools/pre-push-check.py` 第 5 步扫描重定向事故产物（保留名 ∪ `$` 前缀），枚举源为「git 列表 ∪ `\\?\` 直读」并集，覆盖 git 盲区。位置在下游，只缩短发现时延，不阻止产生。

产生前拦截（唯一能阻止文件诞生的一层）属宿主职责，本仓不承载该实现。

## 场景验收（文档检查，未自动化）

1. POSIX 上下文收到含 `2>nul` 的命令：应在执行前改写为 `2>/dev/null`，或显式经 `cmd /c` 执行。
2. 需要丢弃错误输出且宿主不确定：先确认执行环境，不猜测 `nul` / `$null` / `/dev/null`。
3. 命令失败且带输出重定向：核查工作目录新增项，不声明「失败所以无副作用」。
4. 目标为正式译文或源码：不直接重定向覆盖，走确定性写回流程。

不得为了演示本事故对正式文件执行破坏性重定向。若未来建立自动化回归，应使用隔离测试目录与合成内容。
