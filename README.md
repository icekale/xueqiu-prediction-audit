# 公开预测审计 Skill

把雪球（及同类平台）大 V 的公开发言，收成可证伪的方向样本，分开计算**方向 / 价位 / 照做**，并导出给客户看的浅色 HTML/PDF。

这不是荐股框架，也不是某个博主的人格模拟。它只回答一件事：这些公开判断，事后对行情成立了多少。

**爬雪球不是门槛。** 仓库带可执行脚本：离线样例零配置；新账号优先吃你已有的帖子；行情默认走东财/腾讯/新浪/Yahoo，不依赖雪球登录。

English: [English](#english)

## 介绍

公开组合净值和粉丝数都不能当战绩。真正能对的，是带日期、带多空、能映射到流动标的的句子。

本 skill 要求 agent：

1. 先跑自带 CLI，不要手写爬虫。
2. 同一论点只记首次清楚表述；翻案和数字价位另计。
3. 结构（产业/周期）和战术（买卖点）分开打分。
4. 照做用等权、同时报中位；并给出「一直拿住那条结构」的朴素对照。
5. 客户稿用浅色系统黑体单栏；取数失败就降级为薄样本，不要卡死。

样例（雪球 metalslime / 药神，39 条，截止 2026-08-24）见 [`examples/metalslime.md`](examples/metalslime.md)。

输出仅供研究。不是投资建议，不是买卖指令。

## 机构

| | |
| --- | --- |
| 作者 | [Kale](https://github.com/icekale)（icekale） |
| 机构 | 独立开源；与 [V Push](https://vpush.net) 同源维护 |
| 相关仓库 | [icekale/vpush](https://github.com/icekale/vpush)（雪球等大 V 订阅） |
| 许可 | MIT |
| 仓库 | https://github.com/icekale/xueqiu-prediction-audit |

V Push 负责把公开动态收进来；本 skill 负责把历史发言做成可复核的预测审计。两者独立使用。已在跑 V Push 的人可以把现有时间线 `import-posts`，不必再爬一次。

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
```

`example` 会在 `work/example/` 写出浅色 HTML；本机有 Chrome 再尝试 PDF/PNG。这一步不需要雪球账号。

可选：`pip install -r requirements.txt`，才能从本机浏览器一键导入你自己的雪球登录态。

更新：`cd ~/.cursor/skills/xueqiu-prediction-audit && git pull`

## 使用方式

对 agent 说：

```text
用公开预测审计 skill，审计这个雪球账号：
https://xueqiu.com/u/2292705444
```

或自己跑：

```bash
# A. 已有导出
python3 scripts/xueqiu_audit.py import-posts posts.json --out work/2292705444

# B. 浏览器已登录雪球
python3 scripts/xueqiu_audit.py cookie
python3 scripts/xueqiu_audit.py fetch 2292705444          # 默认薄样本
python3 scripts/xueqiu_audit.py fetch 2292705444 --mode full

# C. 入选（这一步由人/agent 判断）后打分出报告
python3 scripts/xueqiu_audit.py score work/2292705444/calls.json
python3 scripts/xueqiu_audit.py report work/scorecard.json --out work/report
```

`calls.json` 字段：`date` `side` `symbol` `horizon_m` `kind` `cat` `theme`。详见 `SKILL.md`。

行情不绑雪球：A 股前复权走东财 / 腾讯 / 新浪，美股港股走 Yahoo。只有这些都失败且你提供了登录态，才回退雪球日 K。

Cookie 可放环境变量，不要写进仓库：

```bash
export XUEQIU_COOKIE="..."
# 或
export XUEQIU_COOKIE_FILE="$HOME/.config/xueqiu-prediction-audit/cookie"
```

命令说明见 [`references/cli.md`](references/cli.md)。

## 仓库结构

```
xueqiu-prediction-audit/
├── README.md
├── SKILL.md
├── LICENSE
├── requirements.txt          # 可选：browser-cookie3
├── scripts/xueqiu_audit.py   # doctor / example / cookie / fetch / score / report
├── scripts/audit_core.py
├── references/scoring.md
├── references/report.md
├── references/cli.md
├── examples/metalslime.md
├── examples/metalslime_calls.json
├── examples/metalslime_scorecard.json
└── tests/test_core.py
```

## 安全边界

- 不要提交 cookie、token、原始时间线整库。
- 不要把组合净值写进标题战绩。
- 不要对客户提内部版本号。
- 不要把结果说成可跟盘的实盘信号。
- 不要为了取数去绕过站点防护。

---

## English

# Public Prediction Audit Skill

Audit dated, directional, falsifiable calls from Xueqiu (and similar public feeds). Score **direction**, **price targets**, and **copy-trade P&amp;L** separately. Export a light-theme client report.

Scraping is not the gate. Bundled CLI: offline example works with zero config; new accounts prefer a local post dump; prices default to East Money / Tencent / Sina / Yahoo.

```bash
git clone https://github.com/icekale/xueqiu-prediction-audit.git ~/.cursor/skills/xueqiu-prediction-audit
python3 ~/.cursor/skills/xueqiu-prediction-audit/scripts/xueqiu_audit.py example
```

Author: [Kale](https://github.com/icekale). Independent / [V Push](https://vpush.net). MIT. Not investment advice.
