# calls.json

入选是判断，脚本只校验字段和打分。不要把 `draft` 候选直接 `score`。

## 根对象

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `calls` | 是 | 样本数组 |
| `title` | 否 | 报告标题 |
| `account` | 否 | 账号名 |
| `uid` | 否 | 雪球 UID |
| `home` | 否 | 主页 URL |
| `asof` | 否 | 价格截止日，`YYYY-MM-DD` |
| `corpus` | 否 | 语料规模，薄样本必须写 |
| `coverage` | 否 | `thin` / `scored` |
| `price_basis` | 否 | 默认「前复权收盘」 |

根上不要写 `"draft": true`。那是 `draft` 命令的标记。

## 每条 call

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `date` | 是 | 首次说清楚的日期，`YYYY-MM-DD` |
| `side` | 是 | `1` 多，`-1` 空 |
| `symbol` | 是 | 可交易标的或指数，如 `SZ000002` `SH000300` `QQQ` `MNSO` |
| `horizon_m` | 是 | 窗口月数。作者写「五年」= `60` |
| `kind` | 是 | `structure` 或 `tactical` |
| `id` | 否 | 稳定短名，便于对表 |
| `theme` | 否 | 一句话判断 |
| `cat` | 否 | 主题桶，如 `白酒/抱团` |
| `note` | 否 | 代理关系、组合非实盘、窗口注意 |
| `price_target` | 否 | 数字价位，另判，不替代方向 |

`symbol` 是代理时必须在 `note` 写明，例如「一线房价 → 万科」。

## price_target

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `symbol` | 有 `lo` 时必填 | 价位对应标的，可与 call 不同 |
| `lo` / `hi` | 股价目标时必填 | 区间 |
| `window_days` | 否 | 默认 10 |
| `label` | 否 | 「见底时茅台 1350–1400」 |
| `verdict` / `hit` / `note` | 否 | 批价等无法用股价打的，手写结论 |

## 最小例子

```json
{
  "title": "最小打分样例",
  "account": "demo",
  "asof": "2026-08-24",
  "calls": [
    {
      "id": "csi-202402",
      "date": "2024-02-05",
      "theme": "看多沪深300",
      "side": 1,
      "symbol": "SH000300",
      "horizon_m": 12,
      "kind": "tactical",
      "cat": "点位/指数"
    }
  ]
}
```

完整药神样本：[`examples/metalslime_calls.json`](../examples/metalslime_calls.json)。  
入选批注：[`examples/inclusion.md`](../examples/inclusion.md)。
