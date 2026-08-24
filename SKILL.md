---
name: xueqiu-prediction-audit
description: Use when auditing a Xueqiu or similar public influencer's stock predictions, scoring directional calls, copy-trade P&L, or producing a client prediction-audit PDF/PNG. Triggers include 预测审计, 命中率, 跟单, 雪球 KOL, 药神, metalslime, 公开预测.
---

# 公开预测审计

把大 V 的**公开、可证伪、带日期的方向判断**对行情打分。客户要的是跟单口径，不是粉丝数或组合净值。

**REQUIRED:** 读完本文件再动手。计分见 [references/scoring.md](references/scoring.md)，版式见 [references/report.md](references/report.md)，命令见 [references/cli.md](references/cli.md)。样例见 [examples/metalslime.md](examples/metalslime.md)。

不是投资建议。不要模仿被审计对象的口吻去荐股。

## 先跑脚本，不要手写爬虫

仓库自带 CLI。**执行它**，不要另写取数/打分/导出。

```bash
python3 scripts/xueqiu_audit.py doctor
python3 scripts/xueqiu_audit.py example
```

`example` 零配置，离线出浅色报告。新账号按下面顺序，**卡在取数就降级，不要停死**。

1. 用户已有 `posts.json` → `import-posts`
2. 本机浏览器已登录雪球 → `cookie` 然后 `fetch UID`（默认 thin）
3. 都没有 → `fetch` 走公开 RSS；仍失败就请用户登录或导出，同时用长文/已贴文本做薄样本
4. 根据 `posts.json` 写出 `calls.json`（这步是判断，脚本不做）
5. `score`（缺行情自动拉东财/腾讯/新浪/Yahoo）→ `report`

禁止打 WAF、禁止写绕过、禁止打印 cookie。详情见 [references/cli.md](references/cli.md)。

## 入选

有日期、有明确多空、能对到流动标的。同一论点只记**首次清楚表述**；翻案或新数字价位另计。

排除：段子、复述、当天情绪、纯框架无方向。公开组合默认不是实盘，净值禁止进入加权。

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

## 硬规则

- 禁止把组合净值当战绩。
- 禁止把方向命中和照做盈亏混成一句「准」。
- 禁止对客户提内部版本号。
- 禁止在聊天、日志、仓库里出现 cookie / `xq_a_token`。
- 禁止输出买卖指令。结尾写「不是投资建议」。
- 禁止为了取数去打站点防护。

触发语：「审计这个雪球账号准不准」「帮我看药神能不能跟」「出一份预测审计给客户」。
