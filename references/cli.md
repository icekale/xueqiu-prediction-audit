# CLI

脚本在 `scripts/xueqiu_audit.py`。agent **执行它**，不要重写一套爬虫。

```bash
cd /path/to/xueqiu-prediction-audit
python3 scripts/xueqiu_audit.py doctor
python3 scripts/xueqiu_audit.py example          # 零配置：预测样例 + 组合样例
python3 scripts/xueqiu_audit.py cubes --example  # 只出组合量化
```

## 审计新账号（按顺序试）

1. **自备语料**（最稳）

```bash
python3 scripts/xueqiu_audit.py import-posts posts.json --out work/UID
```

`posts.json` 可以是数组，或带 `posts` / `statuses` / `items` 的对象。字段至少有日期和正文。V Push 导出的雪球时间线（`statuses`）可直接喂，不必再爬。

2. **V Push / waf-bot 已在跑**（优先于再爬一次）

只读 sidecar 写出的 cookie，不要在本仓库启动 `waf-bot/solver.js`。

```bash
export WAF_COOKIE_FILE=/path/to/waf_cookies.json   # 生产默认 /data/waf_cookies.json
# 或把文件拷过来：
python3 scripts/xueqiu_audit.py cookie --from-file waf_cookies.json
python3 scripts/xueqiu_audit.py fetch 2292705444          # 默认 deep
python3 scripts/xueqiu_audit.py fetch https://xueqiu.com/u/2292705444
python3 scripts/xueqiu_audit.py fetch 2292705444 --comment-posts 150 --comment-pages 8
```

`doctor` 会报 `waf_sidecar` 的年龄和 seed 是否对得上，不打印 cookie。sidecar 的 `seed_sha256` 对不上当前登录串时，沿用登录串，不覆盖。

3. **本机已经登录雪球**

```bash
pip install -r requirements.txt
python3 scripts/xueqiu_audit.py cookie            # 从 Chrome/Safari 读自己的登录态
python3 scripts/xueqiu_audit.py fetch UID         # 默认 deep：全部+问答+评论
python3 scripts/xueqiu_audit.py fetch UID --mode full   # 全原创，不拉评论
python3 scripts/xueqiu_audit.py fetch UID --mode thin   # 长文+热门+近页
```

Cookie 写到 `~/.config/xueqiu-prediction-audit/cookie`（600）。也可用 `XUEQIU_COOKIE` 或 `XUEQIU_COOKIE_FILE`。禁止打印。

4. **没有登录态**

`fetch` 会尝试公开 RSS。历史短，只够薄样本。失败就退回 1–3。遇到防护页就停，不要解挑战。

## 打分和报告

先扫四元组候选（股票 / 方向 / 价格 / 时间），碎片评论可先 LLM 补候选，再人工入选（字段见 [calls.md](calls.md)，批注见 [inclusion.md](../examples/inclusion.md)）：

```bash
python3 scripts/xueqiu_audit.py draft work/UID/posts.json --out work/UID/candidates.json
# 改成 calls.json 后
python3 scripts/xueqiu_audit.py score work/UID/calls.json --out work/UID/scorecard.json
python3 scripts/xueqiu_audit.py report work/UID/scorecard.json --out work/UID/report
```

`score` 会拒绝 `"draft": true`。不要把候选文件当终稿。`draft` 只认作者本句的多空，价格必须挨着「目标 / 见底 / 过」，`@用户名` 不当数字。

`score` 会读 `calls.json` 旁边的 `posts.json` 和 `profile.json`，用来写行为画像 / MBTI 和「注册年 · 可证伪判断跨年」。缺行情时自动拉：A 股走东财 / 腾讯 / 新浪前复权，美股港股走 Yahoo（429 会重试）；仍失败就标未打分，不要为了取数去打防护。有 cookie 时才用雪球日 K。

打完分后按入选表手写 `scorecard.conclusion` / `playbook`（或写在 `calls.json` 根上）。自动跟单口径只是兜底。语料若是帖子全量、没有 `comments.json`，有 sidecar 再跑默认 `fetch --mode deep`。

Chrome 在的话会尝试 PDF/PNG。headless 有时僵死，**HTML 才是完成标准**。

## 组合量化

`fetch` 有登录态时会把每个组合的日净值存成 `cube_ZH*_nav.json`。之后：

```bash
python3 scripts/xueqiu_audit.py cubes UID --from-dir work/UID --out work/UID/cubes
python3 scripts/xueqiu_audit.py cubes --symbol ZH2001629
python3 scripts/xueqiu_audit.py cubes --symbol ZH2001629 --from 2019-07-11 --to 2020-11-06
python3 scripts/xueqiu_audit.py cubes UID          # 需 cookie，现拉列表和净值
```

`--from` / `--to` 是观察期，不是组合全寿命。基准仍按同一重叠窗口对齐。

缺的基准行情走东财/Yahoo，不绑雪球。输出 `cubes_scorecard.json` + `cubes.html`。组合净值不进 `score`。

## 覆盖缺口

| 模式 | 语料 | 能下的结论 |
| --- | --- | --- |
| example | 自带药神 39 条 | 演示方法 |
| import / RSS / thin | 长文+热门+近页 | 近端与框架，须标明薄样本 |
| full + cookie | 全原创时间线 | 生涯主帖，缺评论里的翻案/价位 |
| deep + cookie | 全部时间线 + 问答 + 评论线程 | 主帖和作者自己的评论一起入选 |

`deep` 默认对评论最多的 80 条帖各翻 5 页（每页 20 条）。只保留大 V 自己的评论进 `posts.json` / `draft`；粉丝评论留在 `comments.json` 当上下文。遇到防护页就停评论，帖子仍保留。

组合列表和净值可以拉，只进 `cubes` 报告，不进预测加权。
