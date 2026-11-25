# 目录数据

`catalog.json` 是正式环境目录，来源 `https://jianx-shechao.turingsenseai.com`。测试环境的编号、名称和停用状态不是同一套，不能拿来替换这份文件。

只收录正式库里启用且开通 AI 鉴定的品类、品牌、系列。日常鉴定不要重导。要刷新时才运行 `scripts/export_catalog.py`。

**Agent 禁止直接打开本文件**，请用：

```bash
python scripts/catalog_query.py resolve --kind 包袋 --brand 汤丽 --series Eleanor
```
