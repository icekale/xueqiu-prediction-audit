# 公开预测审计 Skill

把雪球（及同类平台）大 V 的**公开发言**和**公开组合**做成可复核的两张浅色报告：

| 报告 | 回答什么 | 不回答什么 |
| --- | --- | --- |
| 预测审计 | 带日期、带多空的判断，事后方向 / 价位 / 照做各打多少 | 粉丝数、组合净值 |
| 组合量化 | 公开组合相对基准的累计、年化、超额、财富倍数 | 实盘盈亏、能不能跟票 |

这不是荐股框架，也不是某个博主的人格模拟。组合默认是模拟盘；作者写明「与实盘不重合」的，净值不当战绩，也**不**并进预测命中。

**爬雪球不是门槛。** 离线样例零配置；新账号优先吃你已有的帖子；行情默认走东财/腾讯/新浪/Yahoo。

English: [English](#english)

## 介绍

真正能对的，是带日期、带多空、能映射到流动标的的句子。本 skill 要求 agent：

1. 先跑自带 CLI，不要手写爬虫。
2. 同一论点只记首次清楚表述；翻案和数字价位另计。
3. 结构（产业/周期）和战术（买卖点）分开打分。
4. 照做用等权、同时报中位；并给出「一直拿住那条结构」的朴素对照。
5. 公开组合另出量化表，不和命中率混成一句「准」。
6. 客户稿用浅色系统黑体单栏；取数失败就降级为薄样本，不要卡死。

样例（雪球 metalslime / 药神，39 条预测 + 11 个组合，截止 2026-08-24）见 [`examples/metalslime.md`](examples/metalslime.md)。

输出仅供研究。不是投资建议，不是买卖指令。

## 机构

| | |
| --- | --- |
| 作者 | [Kale](https://github.com/icekale)（icekale） |
| 机构 | 独立开源；与 [V Push](https://vpush.net) 同源维护 |
| 相关仓库 | [icekale/vpush](https://github.com/icekale/vpush)（雪球等大 V 订阅） |
| 许可 | MIT |
| 仓库 | https://github.com/icekale/xueqiu-prediction-audit |

V Push 负责把公开动态收进来；本 skill 负责把历史发言做成可复核的预测审计。两者独立使用。已在跑 V Push 的人可以把现有时间线 `import-posts`，或把 waf-bot 的 `waf_cookies.json` 交给 `cookie --from-file` / `WAF_COOKIE_FILE` 再 `fetch`，不必再爬一次，也不要把 solver 拷进本仓库。

## 安装

### 1. 装到 agent 能读到的 skills 目录

**Cursor**

```bash
git clone https://github.com/icekale/xueqiu-prediction-audit.git ~/.cursor/skills/xueqiu-prediction-audit
```

**Codex**

```bash
git clone https://github.com/icekale/xueqiu-prediction-audit.git
mkdir -p ~/.codex/skills
cp -R xueqiu-prediction-audit ~/.codex/skills/
```

**Claude Code**

```bash
git clone https://github.com/icekale/xueqiu-prediction-audit.git ~/.claude/skills/xueqiu-prediction-audit
```

重启对应产品。规范见 [agentskills.io](https://agentskills.io/specification)。

### 2. 开箱自检（只需系统 Python 3.10+）

```bash
cd ~/.cursor/skills/xueqiu-prediction-audit
python3 scripts/xueqiu_audit.py doctor
python3 scripts/xueqiu_audit.py example
python3 scripts/xueqiu_audit.py cubes --example
```

`example` 会在 `work/example/` 写出预测报告和组合报告；`cubes --example` 只出组合。本机有 Chrome 再尝试 PDF/PNG。这一步不需要雪球账号。

可选：`pip install -r requirements.txt`，才能从本机浏览器一键导入你自己的雪球登录态。

更新：`cd ~/.cursor/skills/xueqiu-prediction-audit && git pull`

## 使用方式

对 agent 说：

```text
用公开预测审计 skill，审计这个雪球账号：
https://xueqiu.com/u/2292705444
```

或只要组合量化：

```text
量化这个雪球大 V 的公开组合超额
```

或自己跑：

```bash
# A. 已有导出
python3 scripts/xueqiu_audit.py import-posts posts.json --out work/2292705444

# B. V Push sidecar 或浏览器已登录雪球
export WAF_COOKIE_FILE=/path/to/waf_cookies.json
python3 scripts/xueqiu_audit.py cookie --from-file waf_cookies.json
python3 scripts/xueqiu_audit.py cookie
python3 scripts/xueqiu_audit.py fetch 2292705444          # 默认薄样本；也接受主页 URL
python3 scripts/xueqiu_audit.py fetch 2292705444 --mode full

# C. 扫候选 → 按 inclusion.md 改成 calls.json → 打分
python3 scripts/xueqiu_audit.py draft work/2292705444/posts.json --out work/2292705444/candidates.json
python3 scripts/xueqiu_audit.py score work/2292705444/calls.json
python3 scripts/xueqiu_audit.py report work/scorecard.json --out work/report

# D. 公开组合对基准（累计 / 年化 / 超额 / 财富倍数）
python3 scripts/xueqiu_audit.py cubes --example
python3 scripts/xueqiu_audit.py cubes 2292705444 --from-dir work/2292705444
python3 scripts/xueqiu_audit.py cubes --symbol ZH2001629 --from 2019-07-11 --to 2020-11-06
```

`calls.json` 必填：`date` `side` `symbol` `horizon_m` `kind`。详见 [`references/calls.md`](references/calls.md)。入选批注见 [`examples/inclusion.md`](examples/inclusion.md)。

```bash
python3 scripts/xueqiu_audit.py draft work/2292705444/posts.json --out work/2292705444/candidates.json
```

`draft` 只扫关键词，不能直接 `score`。V Push 时间线（`statuses` 数组或带 `statuses` 的对象）可直接 `import-posts`。

行情不绑雪球：A 股前复权走东财 / 腾讯 / 新浪，美股港股走 Yahoo。只有这些都失败且你提供了登录态，才回退雪球日 K。

Cookie 可放环境变量，不要写进仓库：

```bash
export XUEQIU_COOKIE="..."
# 或
export XUEQIU_COOKIE_FILE="$HOME/.config/xueqiu-prediction-audit/cookie"
# 已有 V Push waf-bot 时：
export WAF_COOKIE_FILE="/data/waf_cookies.json"
```

命令说明见 [`references/cli.md`](references/cli.md)。

## 组合量化

公开组合是另一张报告，不是预测样本。公式：

```
累计     = 区间末净值 / 区间首净值 - 1
年化     = (1 + 累计) ** (365.25 / 天数) - 1    # 仅当天数 ≥ 365
超额(pp) = 组合累计% - 基准累计%
财富倍数 = (1 + 组合累计) / (1 + 基准累计)
```

基准按市场：A 股沪深300 / 中证500 / 科创50，美股 QQQ / SPY，港股恒指 / 恒生科技。每一列必须和组合落在同一重叠窗口；科创50 这类晚上市的会标明同窗起算日。要比某一段而不是全寿命时加 `--from` `--to`。短于一年的先写「不足以证明长期能力」，并标停更、非实盘。

药神样例「大票为主」2019-07-11～2020-11-06：+1490.74%，年化 +706.84%，相对沪深300 超额约 +1462pp，财富约 12.3 倍。已停更，作者写明不建议跟票。完整表见 [`examples/metalslime.md`](examples/metalslime.md)。

## 仓库结构

```
xueqiu-prediction-audit/
├── README.md
├── SKILL.md
├── LICENSE
├── requirements.txt          # 可选：browser-cookie3
├── scripts/xueqiu_audit.py   # doctor / example / cookie / fetch / draft / score / report / cubes
├── scripts/audit_core.py
├── scripts/vpush_xueqiu.py   # sidecar cookie + UID / 正文（不内置 solver）
├── references/scoring.md
├── references/calls.md
├── references/report.md
├── references/cli.md
├── examples/metalslime.md
├── examples/inclusion.md
├── examples/metalslime_calls.json
├── examples/metalslime_scorecard.json
├── examples/metalslime_cubes.json
├── examples/draft_posts.json
└── tests/test_core.py
```

## 安全边界

- 不要提交 cookie、token、原始时间线整库。
- 不要把组合净值写进标题战绩。
- 不要对客户提内部版本号。
- 不要把结果说成可跟盘的实盘信号。
- 不要为了取数去绕过站点防护，也不要在本仓库启动 waf-bot solver。

---

## English

# Public Prediction Audit Skill

Two light-theme reports for Xueqiu (and similar) public influencers:

- **Prediction audit:** dated, directional, falsifiable calls scored as **direction**, **price targets**, and **copy-trade P&amp;L**.
- **Cube quant:** public paper portfolios vs benchmarks — cumulative, annualized (only if ≥365 days), excess in percentage points, and wealth multiple `(1+cube)/(1+bench)`. Cube NAV never enters call scoring.

Scraping is not the gate. Bundled CLI: `example` and `cubes --example` work offline; `draft` only proposes candidates (never score them raw); new accounts prefer a local post dump; prices default to East Money / Tencent / Sina / Yahoo. Use `--from`/`--to` to score a cube window instead of its full life.

```bash
git clone https://github.com/icekale/xueqiu-prediction-audit.git ~/.cursor/skills/xueqiu-prediction-audit
python3 ~/.cursor/skills/xueqiu-prediction-audit/scripts/xueqiu_audit.py example
python3 ~/.cursor/skills/xueqiu-prediction-audit/scripts/xueqiu_audit.py cubes --example
```

Author: [Kale](https://github.com/icekale). Independent / [V Push](https://vpush.net). MIT. Not investment advice.
