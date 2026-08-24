# CLI

脚本在 `scripts/xueqiu_audit.py`。agent **执行它**，不要重写一套爬虫。

```bash
cd /path/to/xueqiu-prediction-audit
python3 scripts/xueqiu_audit.py doctor
python3 scripts/xueqiu_audit.py example          # 零配置，离线出浅色报告
```

## 审计新账号（按顺序试）

1. **自备语料**（最稳）

```bash
python3 scripts/xueqiu_audit.py import-posts posts.json --out work/UID
```

`posts.json` 可以是数组，或带 `posts` / `statuses` 的对象。字段至少有日期和正文。

2. **本机已经登录雪球**

```bash
pip install -r requirements.txt
python3 scripts/xueqiu_audit.py cookie            # 从 Chrome/Safari 读自己的登录态
python3 scripts/xueqiu_audit.py fetch UID         # 默认 thin：长文+热门+近页
python3 scripts/xueqiu_audit.py fetch UID --mode full   # 全原创，慢
```

Cookie 写到 `~/.config/xueqiu-prediction-audit/cookie`（600）。也可用 `XUEQIU_COOKIE` 或 `XUEQIU_COOKIE_FILE`。禁止打印。

3. **没有登录态**

`fetch` 会尝试公开 RSS。历史短，只够薄样本。失败就退回 1 或 2，不要打 WAF。

## 打分和报告

入选仍由 agent 根据 `posts.json` 写 `calls.json`（字段见 SKILL.md）。之后是机械步骤：

```bash
python3 scripts/xueqiu_audit.py score work/UID/calls.json --out work/UID/scorecard.json
python3 scripts/xueqiu_audit.py report work/UID/scorecard.json --out work/UID/report
```

`score` 缺行情时自动拉：A 股走东财 / 腾讯 / 新浪前复权，美股港股走 Yahoo；有 cookie 时才用雪球日 K。不要依赖雪球行情当唯一源。

Chrome 在的话会尝试 PDF/PNG。headless 有时僵死，**HTML 才是完成标准**。

## 覆盖缺口

| 模式 | 语料 | 能下的结论 |
| --- | --- | --- |
| example | 自带药神 39 条 | 演示方法 |
| import / RSS / thin | 长文+热门+近页 | 近端与框架，须标明薄样本 |
| full + cookie | 全原创时间线 | 生涯审计 |

组合列表可以拉，净值不进加权。
