# 公开预测审计 Skill

把雪球（及同类平台）大 V 的公开发言，收成可证伪的方向样本，分开计算**方向 / 价位 / 照做**，并导出给客户看的浅色 PDF/PNG。

这不是荐股框架，也不是某个博主的人格模拟。它只回答一件事：这些公开判断，事后对行情成立了多少。

English: [English](#english)

## 介绍

公开组合净值和粉丝数都不能当战绩。真正能对的，是带日期、带多空、能映射到流动标的的句子。

本 skill 要求 agent：

1. 拉齐公开原创和长文，而不是只看热门帖。
2. 同一论点只记首次清楚表述；翻案和数字价位另计。
3. 结构（产业/周期）和战术（买卖点）分开打分。
4. 照做用等权、同时报中位；并给出「一直拿住那条结构」的朴素对照。
5. 客户稿用浅色系统黑体单栏，结论和跟单口径放最前。

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

V Push 负责把公开动态收进来；本 skill 负责把历史发言做成可复核的预测审计。两者独立使用。

## 安装

克隆到 agent 能读到的 skills 目录，然后重启对应产品。

### Cursor

```bash
git clone https://github.com/icekale/xueqiu-prediction-audit.git ~/.cursor/skills/xueqiu-prediction-audit
```

重启 Cursor，或新开一轮对话。

### Codex

```bash
git clone https://github.com/icekale/xueqiu-prediction-audit.git
mkdir -p ~/.codex/skills
cp -R xueqiu-prediction-audit ~/.codex/skills/
```

重启 Codex。

### Claude Code / 其他兼容 Agent Skills 的产品

```bash
git clone https://github.com/icekale/xueqiu-prediction-audit.git ~/.claude/skills/xueqiu-prediction-audit
```

规范见 [agentskills.io](https://agentskills.io/specification)。目录里必须有 `SKILL.md`。

### 更新

```bash
cd ~/.cursor/skills/xueqiu-prediction-audit && git pull
```

## 使用方式

直接对 agent 说：

```text
用公开预测审计 skill，审计这个雪球账号准不准：
https://xueqiu.com/u/2292705444
```

或：

```text
按 xueqiu-prediction-audit 的口径，给客户出一份浅色 PDF
```

agent 应自动：核对身份 → 入选可证伪方向 → 分方向/价位/照做 → 分年与对照 → 浅色报告。

### 雪球登录态（可选，但匿名经常失败）

雪球会拦未登录抓取。把**你自己账号**的 cookie 放到环境变量，不要写进仓库：

```bash
export XUEQIU_COOKIE="xq_a_token=...; ..."
```

或：

```bash
export XUEQIU_COOKIE_FILE="$HOME/.config/xueqiu/cookie.txt"
```

Agent 和脚本不得打印 cookie，不得写进 PDF/PNG。

价格默认走雪球前复权日 K：`https://stock.xueqiu.com/v5/stock/chart/kline.json`。

## 仓库结构

```
xueqiu-prediction-audit/
├── README.md                 # 介绍、机构、安装、使用
├── SKILL.md                  # agent 主指令
├── LICENSE
├── references/scoring.md     # 入选、阈值、对照
├── references/report.md      # 浅色客户稿版式
└── examples/metalslime.md    # 完整样例（药神）
```

## 安全边界

- 不要提交 cookie、token、`config.yaml`、原始时间线整库。
- 不要把组合净值写进标题战绩。
- 不要对客户提内部版本号。
- 不要把结果说成可跟盘的实盘信号。

---

## English

# Public Prediction Audit Skill

Audit dated, directional, falsifiable calls from Xueqiu (and similar public feeds). Score **direction**, **price targets**, and **copy-trade P&amp;L** separately, then export a light-theme client PDF/PNG.

Not a stock-picking persona. Not investment advice.

### Organization

Author: [Kale](https://github.com/icekale). Independent / maintained alongside [V Push](https://vpush.net). MIT.

### Install (Cursor)

```bash
git clone https://github.com/icekale/xueqiu-prediction-audit.git ~/.cursor/skills/xueqiu-prediction-audit
```

### Usage

```text
Audit this Xueqiu user with the public prediction-audit skill:
https://xueqiu.com/u/2292705444
```

Put your own Xueqiu cookie in `XUEQIU_COOKIE`. Never commit or print it.
