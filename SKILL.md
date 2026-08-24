---
name: xueqiu-prediction-audit
description: Use when auditing a Xueqiu or similar public influencer's stock predictions, scoring directional calls, copy-trade P&L, or producing a client prediction-audit PDF/PNG. Triggers include 预测审计, 命中率, 跟单, 雪球 KOL, 药神, metalslime, 公开预测.
---

# 公开预测审计

把大 V 的**公开、可证伪、带日期的方向判断**对行情打分。客户要的是跟单口径，不是粉丝数或组合净值。

**REQUIRED:** 读完本文件再动手。细则见 [references/scoring.md](references/scoring.md)、[references/report.md](references/report.md)。样例见 [examples/metalslime.md](examples/metalslime.md)。

不是投资建议。不要模仿被审计对象的口吻去荐股。

## 流程

1. **身份**：主页、UID、注册时间、粉丝、原创量、长文数、自选。11 个公开组合默认**不是实盘**；作者写明「与实盘不重合 / 不建议跟票」的净值，禁止进入加权。
2. **语料**：拉齐公开原创时间线 + 长文。雪球匿名会被拦；用调用方自己的登录 cookie。**禁止打印、提交、写进报告。**
3. **入选**：有日期、有明确多空、能对到流动标的。同一论点只记**首次清楚表述**；翻案或新数字价位另计。
4. **排除**：段子、复述别人、当天情绪、纯框架无方向、同周心情帖。
5. **三套分数分开写**：方向、价位、照做。禁止合成一个「对」。
6. **切片**：分年、分主题、结构 vs 战术。窗口命中和拿到现在必须并列。给朴素对照（一直拿住那条结构空/多）。
7. **客户稿**：浅色、系统黑体、单栏。结论和跟单口径在最前。禁止写 v1/v2、禁止米色宋体研报。

## 入选字段

每条至少：`date` `side`（+1 多 / −1 空）`symbol` `horizon_m` `kind`（`structure` | `tactical`）`cat` `theme`。有数字价位再加 `price_target`。

horizon 用**作者自己写的期限**。写「五年」就按 60 个月，不要改成 24 个月然后判错。

`structure`：产业/制度/周期框架，持有窗口通常 ≥6 个月。  
`tactical`：买卖点、现在可以买/卖、点位、左右横跳。

## 计分（默认）

价格用前复权收盘。窗口 = `date + horizon`，截到样本截止日。

| 方向 | 对 | 平 | 偏错 | 错 |
| --- | --- | --- | --- | --- |
| 多 | 窗口 > +5% | −5%～+5% | −10%～−5% | < −10% |
| 空 | 窗口 < −8% | −8%～+5% | +5%～+10% | > +10% |

- **价位**：数字目标在约定窗口内打到才算对。方向对、价位没到 = 价位错。
- **照做** = `side × 标的窗口涨跌`，等权，**不**自动对冲沪深300。同时报均值和中位；中位优先讲给客户。
- **回吐**：窗口对但至今为负，或高点回撤 ≥50%，必须写进「窗口对 ≠ 拿到现在」。

## 客户报告

浅色 canvas：白底、`#141414` 字、系统黑体、H1 24/30、正文 14/20、圆角 6–8、无渐变/emoji/阴影/红绿灯。暗色 PDF 经常丢背景，**交付 PDF 用浅色**。

顺序：标题 → 结论 → 四格指标 → 跟单口径 → 照做与对照 → 分年 → 分主题 → 价位/翻案 → 窗口 vs 至今 → 明细 → 方法。

四格默认：结构方向对、战术方向对、战术照做中位、数字价位打中。

## 硬规则

- 禁止把组合净值当战绩。
- 禁止把方向命中和照做盈亏混成一句话「准」。
- 禁止对客户提内部版本号。
- 禁止在聊天、日志、仓库里出现 cookie / `xq_a_token`。
- 禁止输出买卖指令。结尾写「不是投资建议」。
- 2026 年这类近端样本要标明窗口不足。

## 取数

Cookie 只从环境变量或仓库外本地文件读：

```bash
export XUEQIU_COOKIE="..."          # 调用方自己的雪球登录态
# 或：XUEQIU_COOKIE_FILE=/path/outside/repo/cookie.txt
```

时间线：`https://xueqiu.com/v4/statuses/user_timeline.json`  
日 K：`https://stock.xueqiu.com/v5/stock/chart/kline.json`  
User-Agent 用移动 Safari。失败先换 cookie，不要硬打 WAF。

## 常见借口

| 借口 | 处理 |
| --- | --- |
| 组合赚了 N 倍 | 看运行区间和作者免责；不进加权 |
| 这条后来又说对了 | 首次表述和翻案分开计 |
| 窗口太长所以改短 | 用原文期限 |
| 先出深色高大上 PDF | 浅色；深色预览会坏 |
| 命中率 54% 所以水平一般 | 先拆结构/战术，再看对照 |

触发语：「审计这个雪球账号准不准」「帮我看药神能不能跟」「出一份预测审计给客户」。
