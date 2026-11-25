# 认款与是否支持 AI 鉴定

禁止打开 `catalog.json`。

看图先写出品类、品牌、系列，然后只跑这一次：

```bash
python "<SCRIPTS>/catalog_query.py" resolve --kind 鞋靴 --brand 耐克 --series "Zoom Pegasus"
python "<SCRIPTS>/catalog_query.py" resolve --kind 服饰 --brand 阿迪达斯
python "<SCRIPTS>/catalog_query.py" resolve --kind 腕表 --brand 劳力士
python "<SCRIPTS>/catalog_query.py" resolve --kind 美妆 --brand 肌肤之钥 --series 长管隔离
```

`SCRIPTS` 是 `<SKILL_DIR>/scripts`。一次 `resolve` 拿 `kind_id`、`brand_id`、`series_id`、`single_key`、`hierarchy`、`query_id`、`points`、`must_points`、`point_count`。不要再拆成 `kinds`、`brands`、`series`、`points` 四次查询。后四个命令只在 `resolve` 失败、需要核对名字时用。

用户或开发要刷新目录时才跑 `python "<SCRIPTS>/export_catalog.py"`，日常鉴定不要跑。

## 用户说法 → 目录品类

先按图和用户的话选定下面的目录品类名，再拿这个名字去查。不要用口语名直接当 `kind_id`。

| 用户可能这么说 | 目录里的品类名 |
| --- | --- |
| 包袋 | 包袋 |
| 鞋靴 | 鞋靴 |
| 腕表 | 腕表 |
| 美妆 | 美妆；只拍盒子/瓶子且对不上产品系列时再看美妆无损 |
| 名酒、酒 | 酒水 |
| 配饰 | 配饰 |
| 首饰 | 首饰 |
| 珠宝、玉石、翡翠、和田玉 | 玉石 |
| 木作、手串 | 手串（品牌常是「全木作」） |
| 邮票 | 邮票 |
| 瓷器 | 瓷器 |
| 钱币 | 钱币 |
| 卡牌 | 卡牌 |
| 服饰、衣服 | 服饰 |

目录里还有药材、大闸蟹等。图和用户的话对得上、并且 `resolve` 有鉴定点，就同样支持。没有开通 AI 鉴定的品类不在这张表里，按不支持处理，例如潮玩、古玩文玩、3C无损、宠物、日化。品类「其他」不支持。

## 怎么判断支持

支持 = `resolve` 成功，且 `point_count > 0`。这表示款式已经对上，可以开始鉴定。

不支持 = 品类没有、品牌没有，或系列对不上并且款式识别也帮不上。系列名没对上、`series_id` 为 0 或 `point_count` 为 0 时，先按下面的「款式识别」再试一次，不要立刻说不支持，也不要立刻改走通用盒、通用瓶。算法多图识别也要先有款式，认不出款时不要调用 `appraise_nopointer` 或 `appraise_images`。

`point_count > 0` 之后怎么鉴，看张数和品类。一张图走逐点引导。多张图时，下面这些品类走算法多图识别，不要自己对点：

包袋、鞋靴、服饰、美妆、腕表、配饰、酒水。

围巾是配饰，在这份名单里。服饰、腕表、配饰对到品牌即可，美妆、包袋、鞋靴、酒水还要对上系列。名单以外的品类（卡牌、瓷器、钱币、手串、首饰、邮票等）多图由 Agent 看图对点。潮玩、古玩文玩、3C无损、宠物、日化不支持。

有多个品牌或多个系列对得上、你分不清：把候选项问用户。不要挑一个最像的硬鉴。

## 服饰、腕表

这两类对到品牌就可以。不要追问系列，也不要因为有「全系列（皮表带 / 钢表带）」停住。

```bash
python "<SCRIPTS>/catalog_query.py" resolve --kind 服饰 --brand 阿迪达斯
python "<SCRIPTS>/catalog_query.py" resolve --kind 腕表 --brand 劳力士
```

`point_count` 为 0 时，再跑一次不带 `--series` 的 `points`。仍是 0，就是不支持。

## 美妆

先看图，再选系列。`resolve` 不带 `--series` 时不会再默认成「全品牌通用瓶」。返回「这个品牌有多个系列」时，按图片从列表里选产品系列再 resolve。不要没看系列就落进「全品牌通用瓶」或「瓶子无损」。

1. 图里有产品本体（管、瓶、膏体、盖、编码等），并且该品牌有对得上的产品系列：用那个系列。例如肌肤之钥钻光隔离霜对「长管隔离」。
2. 先列出系列再选：

```bash
python "<SCRIPTS>/catalog_query.py" series --kind 美妆 --brand 肌肤之钥
python "<SCRIPTS>/catalog_query.py" resolve --kind 美妆 --brand 肌肤之钥 --series 长管隔离
```

3. 系列名里的「通用瓶」「瓶子无损」「通用盒」「盒子无损」「无损」不是产品系列。品牌还有对得上的产品系列时，用那个产品系列，不要改走瓶无损或盒无损。
4. 看图选的系列名对不上，或 `resolve` 的 `series_id` 为 0、`point_count` 为 0：品类是美妆、鞋靴、腕表、包袋、酒水、服饰时，先把图送去款式识别，再决定系列。其他品类不要调用。只运行一次 `scripts/recognize_style.py`，不要自己签发、上传或调用 `recognize_style`。一张图只传 `--images`。两张及以上把外观图放在第一张，并用 `--waiguan` 写同一张。最多 9 张。stdout 里的 `brand`、`series`、`series_id` 只作参考。和图片、目录系列对得上，就用那个系列名再 `resolve`。对不上、识别失败，或图里明显不是那个系列，不要硬用。
5. 款式识别之后，图基本都是盒子，产品系列仍对不上，但有盒子/无损系列且 `point_count > 0`：款式按无损系列算，支持 AI。多图仍先对上这个系列；卡牌和无损的多图不走算法多图识别，按 [multi-image.md](multi-image.md) 里的 Agent 看图对点。
6. 产品系列、款式识别和无损系列都对不上，或点位数是 0：不支持。不要拿 `appraise_nopointer` 或 `appraise_images` 去猜款。

## 卡牌、美妆无损

这两类支持 AI。单图仍逐点引导。多图在款式对上之后，由 Agent 看图把每张图对到点位上，再跑 `appraise_batch.py`。算法多图识别不覆盖这两类。3C无损不支持。目录没有品牌或没有点，才是不支持。

## 对点名称

单图先在这一款的全部点位里认这张是哪一个。当前点对不上、但能严格对上另一个还没成功的点时，就按那个点单鉴。卡牌、无损的多图仍按画面内容对点位名。对不上任何一个点就跳过，禁止硬套：

- 合格证 ≠ 鞋盒钢印 / 鞋盒侧标
- 鞋垫正面 ≠ 鞋垫背胶
- 鞋标 ≠ 走线
- 外观 ≠ 鞋标
- 点名语义差得远的，不要凑合
