# 授权、工具、上传

包袋、鞋靴、服饰、美妆、腕表、配饰、酒水的多图不要读本文件。那些图只运行 `scripts/appraise_multi.py`。

MCP：`https://chat-flow.turingsenseai.com/api/open/mcp`。

`SCRIPTS` = `<SKILL_DIR>/scripts`。token 写在该 Skill 旁的 `.jianx-auth.json`，所以授权和 `mcp_call.py` 必须用这一套脚本。

## 未授权

不要判断用户在手机还是电脑。始终只跑：

```bash
python "<SCRIPTS>/oauth_login.py"
```

这条会马上结束，并尝试打开系统浏览器。无论 `opened_browser` 是真是假，都把 stdout 里的 `url` 原样发给用户。不要改短链，不要拆参数，不要再包一层页面。不要读取 `.jianx-oauth-pending.json`。不要因为以为浏览器已经打开就省略链接。

把 `user_hint` 原样告诉用户：在页面中弹出的页面复制授权码，复制完成后回到当前对话，粘贴授权码即可完成连接。

同意后，授权页会先尝试把授权送回本机，地址栏不跳走。用户回到对话后先查本机是否已经收下：

```bash
python "<SCRIPTS>/oauth_login.py" --status
```

`ok` 为真再继续。仍在等，且用户发回了授权码，再交给 `--callback`。授权码 5 分钟内有效，只能用一次。本机已经收下时，再贴一次会失败，以 `--status` 为准。不要把授权码或 access_token 念出来。

```bash
python "<SCRIPTS>/oauth_login.py" --callback "<用户发回的授权码>"
```

token 大约 7 天。过期后 `mcp_call` 报 `expired`，重新跑第一条命令。

之后：

```bash
python "<SCRIPTS>/mcp_call.py" tools/list
python "<SCRIPTS>/mcp_call.py" tools/call <工具名> "<json>"
python "<SCRIPTS>/mcp_call.py" --reply tools/call <工具名> "<json>"
```

`--reply` 扁平打印 `outputs.reply`。业务 `status` 不是 success 时退出码非 0。再遇到 401 / expired，重新授权。

入参用 camelCase。`appraise_point`、`appraise_points`、`appraise_images`、`appraise_overall`、`appraise_nopointer`、`recognize_style` 由 `mcp_call.py` 带上 `api_env=pro`。不要自己改这个值。

## 工具

- `list_brands`：`kindId`（number）。只在本地目录脚本找不到品类时兜底。
- `list_series`：`brandId`（number）。只在脚本找不到品牌时兜底。
- `recognize_style`：只支持美妆、鞋靴、腕表、包袋、酒水、服饰。不要直接调用。系列名对不上时只运行一次 `scripts/recognize_style.py`，脚本完成签发、上传和识款。其他品类不要运行。一张图：`--images` 一张。两张及以上：`--images` 全部，`--waiguan` 写其中的整件外观图。最多 9 张。不要传 `url`，不要传 `0`、`1`、`2`、`4`，不要改图。stdout 的 `brand`、`series`、`series_id` 只作参考。和图片、目录对得上再拿去 `resolve`。对不上不要硬用，也不要把它当成鉴定结论。
- `list_points`：`queryId` + `hierarchy`。`hierarchy` 必须是字符串 `"1"` 或 `"2"`。单图引导时必须调。拍摄说明只用返回的 `description`，示例只用 `example_img`，必拍看 `must`，优先顺序再看 `important`。发给用户的示例图必须原样使用 `example_img`、`stick_figure_url`、`big_stick_figure_url`，写成 `![点位名](原地址)`。不要改成 `previewMaterialImageV2` 或其他预览接口。本地目录没有说明和示例图，不要自己编。
- `create_uploads`：`items: [{contentType}]`。多张一起签发。
- `create_upload`：`contentType`。单张，或批量签发失败时逐张用。
- `appraise_point`：单图对上一个点。整鉴已经成功后再补图，原图和新图都要重新上传、重新单鉴，拿到新的 `said`。`file_id` + `point_id`。不要传 `oaid`，不要复用旧 `file_id` 或旧 `said`。钱币、邮票、瓷器、玉石不要直接调它，走 `appraise_category.py`。
- `appraise_nopointer`：算法多图识别。只用于包袋、鞋靴、服饰、美妆、腕表、配饰、酒水，且款式已经对上、这一轮有多张图。围巾属于配饰。入参 `file_ids`、`brand_id`、`series_id`、`kind_id`、`single_key`、`brand_name`、`series_name`。算法判断每张图是哪个鉴定点，`outputs.reply` 里带各点得分和整鉴订单。不要再调 `get_order`。单图不要用。认不出款式时不要用。
- `appraise_images`：只在 `appraise_nopointer` 返回未知工具时，作为多图识别的回退。入参同样要带已对上的 `file_ids`、`brand_id`、`series_id`、`single_key`、`brand_name`、`series_name`。回退之后还要 `appraise_overall`。整鉴 `reply` 已包含订单详情，不要再 `get_order`。
- `appraise_points`：只用于卡牌、美妆无损、盒/瓶无损的多图。`items: [{file_id, point_id}]`。工作流只保留前 10 条。3C无损不支持。这些多图优先跑 `appraise_batch.py`，不要逐张 `appraise_point`。
- `appraise_overall`：`saids` 逗号分隔；`must_count`、`done_count`、`kind_id`、`brand_id`、`series_id`（没有就 0）、`category_name`、`brand_name`、`brand_en_name`、`series_name`、`series_en_name`。逐点流程里，用户要结论，或点位清单没有下一个还没成功的点时，用已记下的成功 `said` 调用。`must_count` 和 `done_count` 都等于有效单鉴数。一个成功 `said` 都没有时不要调用。还没开始逐点、这一轮一次给齐的多图，由 `appraise_multi.py` 整鉴，不要在那条路径上再自己调这个工具。还没有整鉴单时，`saids` 只用本会话里还没进过订单的成功单鉴号。已经整鉴成功后再补图，禁止带上旧 `said`；先把上一单的本地原图和新图全部重新单鉴，只用这次新的 `said`。不要传 `oaid`。成功时 `outputs.reply` 里已经有 `genuine_text`、得分、`singles` 和 `order`，不要再调 `get_order`。钱币、邮票、瓷器、玉石不要自己拼这个工具，走 `appraise_category.py`。
- `get_order`：只在上面两份整鉴回复里没有 `genuine_text` 和 `singles` 时才用。`orderId` 用整鉴 `oaid`，`orderType` 用 `"overall"`。正常路径不要调用。

流程态可以看，不能当结论：

- `appraise_nopointer`：`status=success` 且 `genuine_text` 有值，按 wording 直接说。这份 `reply` 已经包含订单里的真假、得分和各点结果。没有 `oaid` 或 `status=failed` 就复述 `message`。

- `appraise_points`：`done_count > 0` 就整鉴。
- `appraise_overall`：`status=success` 且 `genuine_text` 有值，按 wording 直接说。不要再 `get_order`。没有 `oaid` 就复述失败原因，不要编真假。
- 单鉴成功要有非空 `said`。
- `reject`：复述 `message`，请用户换角度重拍。不要裁图、调亮、放大或重绘后再鉴定。多图可以跳过该点。
- `fail` / `timeout`：复述 `message`。多图可以跳过该点。
- `insufficient_material`：把 `must_count` 设成 `done_count` 再整鉴，用新的 `reply` 说结论。

## 上传

`contentType` 用 `image/jpeg`、`image/png`、`image/webp`、`image/gif`。裸的 `jpeg` / `jpg` / `png` 也可以。

单张：`create_upload`，然后：

```bash
python "<SCRIPTS>/put_appraisal_image.py" "<upload_url>" "<本地路径>" "<content_type>"
```

多张：`create_uploads` 的 `items` 与本地图一一对应。批量签发失败就循环 `create_upload`。把每张的 `upload_url`、`path`、`content_type`、`file_id` 写成一个 JSON 数组，再：

```bash
python "<SCRIPTS>/put_appraisal_images.py" "<清单.json绝对路径>"
```

`ok` 为真之后才用 `file_id`。PUT 脚本自己处理代理和签名，不要改 URL。图能压到大约 100KB 更好，不是门槛。

禁止：给 PUT 加 Authorization；改签名查询参数；multipart 或 base64；把本地路径传给 `appraise_*`。
