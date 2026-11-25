# 不在多图名单里的多图

钱币、邮票、瓷器、玉石不要读本文件。回到 SKILL.md，每张图跑 `scripts/appraise_category.py`，不要对鉴定点。

只在品类不是包袋、鞋靴、服饰、美妆、腕表、配饰、酒水时读这份。那些品类即使你已经看到了鉴定点，也回到 SKILL.md，只跑 `appraise_multi.py`。

1. 看图写出品类、品牌、系列，只跑一次：

```bash
python "<SKILL_DIR>/scripts/catalog_query.py" resolve --kind 卡牌 --brand 某品牌 --series 某系列
```

2. `point_count` 为 0：按 [wording.md](wording.md) 说这一款暂时不支持 AI 鉴定。
3. 有鉴定点，且这一轮不少于 2 张：看图把每张配到一个点位名，然后只跑 `scripts/appraise_batch.py`。它会签发、上传、批量单鉴并整鉴。不要逐张 `appraise_point`，不要 `appraise_multi.py`，不要自己调 `create_uploads`。

卡牌不走算法多图。`hierarchy` 是 `1`、`series_id` 是 0：鉴定点在品牌上，没有系列。`--series-id` 传 `0`，`--kind-id` 和 `--brand-id` 用这次返回的编号。不要把 `brand_id` 填进 `--series-id`。点位名只用返回的点位名。

```bash
python "<SKILL_DIR>/scripts/appraise_batch.py" --kind-id <品类编号> --brand-id <品牌编号> --series-id 0 --item 卡牌-正面=<正面图> --item 卡牌-背面=<背面图>
```

`hierarchy` 是 `2`：`--series-id` 用返回的 `series_id`，不要传 `0`。

4. 只有 1 张图时才逐张单鉴，见 [single-image.md](single-image.md)。
5. 用脚本 stdout 的 `genuine_text` 说结论，不要再 `get_order`。
6. 已经整鉴成功后用户再补图：不要只鉴新图，也不要把旧 `said` 拼进新的整鉴。按 SKILL.md「整鉴之后补图」，原图和新图按点位重新配好，再跑一次 `appraise_batch.py`。
