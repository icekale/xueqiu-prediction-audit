---
name: xueqiu-prediction-audit
description: Use when auditing a Xueqiu or similar public influencer's stock predictions, scoring directional calls, copy-trade P&L, quantifying public cubes against benchmarks, or producing a client prediction-audit PDF/PNG. Triggers include 预测审计, 命中率, 跟单, 雪球 KOL, 药神, metalslime, 公开预测, 组合量化, 超额收益, 雪球组合.
---

# 公开预测审计

把大 V 的**公开、可证伪、带日期的方向判断**对行情打分。客户要的是跟单口径，不是粉丝数。公开组合另做净值对基准（累计 / 年化 / 超额 / 财富倍数），**不**并进命中加权。

**REQUIRED:** 读完本文件再动手。计分见 [references/scoring.md](references/scoring.md)，版式见 [references/report.md](references/report.md)，命令见 [references/cli.md](references/cli.md)。样例见 [examples/metalslime.md](examples/metalslime.md)。

不是投资建议。不要模仿被审计对象的口吻去荐股。

## 先跑脚本，不要手写爬虫

仓库自带 CLI。**执行它**，不要另写取数/打分/导出。

```bash
python3 scripts/xueqiu_audit.py doctor
python3 scripts/xueqiu_audit.py example
python3 scripts/xueqiu_audit.py cubes --example
```

`example` 零配置，离线出浅色报告。新账号按下面顺序，**卡在取数就降级，不要停死**。

1. 用户已有 `posts.json` → `import-posts`
2. 本机浏览器已登录雪球 → `cookie` 然后 `fetch UID`（默认 thin）
3. 都没有 → `fetch` 走公开 RSS；仍失败就请用户登录或导出，同时用长文/已贴文本做薄样本
4. 根据 `posts.json` 写出 `calls.json`（这步是判断，脚本不做）
5. `score`（缺行情自动拉东财/腾讯/新浪/Yahoo）→ `report`
6. 组合量化：`cubes UID` 或 `cubes --from-dir work/UID`（`fetch` 有登录态时会顺带存净值）

禁止打 WAF、禁止写绕过、禁止打印 cookie。详情见 [references/cli.md](references/cli.md)。

## 入选

有日期、有明确多空、能对到流动标的。同一论点只记**首次清楚表述**；翻案或新数字价位另计。

排除：段子、复述、当天情绪、纯框架无方向。公开组合默认不是实盘，净值禁止进入预测加权；组合报告见 `cubes`。

horizon 用作者自己写的期限。「五年」= 60 个月。

`structure`：产业/制度/周期，通常 ≥6 个月。  
`tactical`：买卖点、现在可以买/卖、点位。

## 计分

| 方向 | 对 | 平 | 偏错 | 错 |
| --- | --- | --- | --- | --- |
| 多 | 窗口 > +5% | −5%～+5% | −10%～−5% | < −10% |
| 空 | 窗口 < −8% | −8%～+5% | +5%～+10% | > +10% |

三套分数分开写：方向、价位、照做。照做 = `side × 窗口涨跌`，等权，不自动对冲沪深300。客户稿先报中位。窗口对但至今为负、或高点回撤 ≥50%，必须写「窗口对 ≠ 拿到现在」。

薄样本（RSS / thin / 用户只贴了几条）必须在标题或结论标明，不能装成生涯审计。

## 客户报告

浅色、系统黑体、单栏。结论和跟单口径最前。交付 PDF 用浅色。禁止 v1/v2、米色宋体、红绿灯。`report` 以 HTML 为完成标准；Chrome 僵死不算失败。

## 组合量化

对公开组合净值做区间对比，版式对齐「累计 / 年化 / 相对基准超额 / 财富倍数」：

- 累计 = 区间首末净值之比 − 1。年化仅当观察期 ≥365 天：`(1+ret)^(365.25/days)-1`。
- 超额（百分点）= 组合累计% − 基准累计%。财富倍数 = `(1+组合)/(1+基准)`。每一列基准必须和组合落在同一重叠窗口；科创50 这类晚上市的要标明同窗起算日。
- A 股默认沪深300 / 中证500 / 科创50；美股 QQQ / SPY；港股恒指 / 恒生科技。净值接口自带的基准优先用。
- 必须写停更日期、作者「与实盘不重合 / 不建议跟票」。短窗口先写「不足以证明长期能力」。
- 禁止把组合净值写进预测命中或照做加权。

## 硬规则

- 禁止把组合净值当实盘战绩，也禁止并进预测命中。
- 禁止把方向命中和照做盈亏混成一句「准」。
- 禁止对客户提内部版本号。
- 禁止在聊天、日志、仓库里出现 cookie / `xq_a_token`。
- 禁止输出买卖指令。结尾写「不是投资建议」。
- 禁止为了取数去打站点防护。

触发语：「审计这个雪球账号准不准」「帮我看药神能不能跟」「出一份预测审计给客户」「量化这个大 V 的组合超额」。
