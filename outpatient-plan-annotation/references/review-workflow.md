# 核对工具流程

## 文件位置

正式样本仍只有原图、annotation.json、标注.svg、标注说明.md。固定编号、用户确认、推断依据及检查状态保存到该图的外部 `临时核对/<项目>/review_state.json`；图像和旧MD归档也放外部。

首次接续已有图时，助手先读取当前MD、JSON和旧固定映射，把原号及原话导入记录，再运行工具。工具不会自动理解“同意”或将候选当成用户答案。遇到旧映射但没有review_state时停止生成，先完成导入；无需用户重新填表。

## 模式记录

默认 `annotation_mode: full_names` 保留严格房名要求。区域级图纸采用 `annotation_mode: regional_coarse`，并保存 `regions` 及 `checks.individual_outline_reviewed`、`checks.region_outline_reviewed`；字段与示例见[区域模式](regional-coarse.md)。不会因为出现unknown就自动放宽，也不能只改完成标志绕过实际检查。

## 最小记录

```json
{
  "name_items": [
    {"review_id": "01", "space_id": "S025", "status": "pending"}
  ],
  "connectivity_items": [
    {"review_id": "K01", "source": "S025", "target": "S015", "status": "pending"}
  ],
  "checks": {"connectivity_reviewed": false, "visual_reviewed": false}
}
```

工具自动绑定原图文件名、尺寸及SHA256；不能把记录用于另一张图。字段允许增加简短依据等外部过程信息，核心JSON字段不变。

- 房名状态：pending、confirmed、resolved。confirmed保存用户answer原话和confirmed_name；resolved只表示JSON名称已明确，不声称用户确认。工具用applied区分确认待同步与已同步：确认尚未应用时保留confirmed_name约束；只有曾应用的确认或resolved项重新变unknown时才沿用原号重开，旧答案进入history。
- 连通状态：pending、confirmed_connected、confirmed_disconnected、not_required。后者只表示退出本轮细究，不是用户确认不相通；在reason说明原因。
- 只有明确填写、有效选项或聊天中的确认才能记为confirmed。新项续号，既有题号不复用、不重排；合并／删除空间后同步外部引用。
- 所有名称、关系需由助手先同步到JSON。记录命令只记确认，不自动改写空间或增删关系，不定位虚构门位。
- 布局推断、门位距离和无需细究项记在外部记录；MD只保留影响用户当前判断的简短依据。

## 常用命令

以下 `state` 和 `review` 必须位于正式样本目录之外。

```bash
uv run --with pymupdf --with shapely python <skill>/scripts/annotation_tools.py questions <json> --out-dir <review> --review-state <state>
uv run --with pymupdf --with shapely python <skill>/scripts/annotation_tools.py confirm <json> --review-state <state> --review-id K01 --status confirmed_disconnected --answer '用户原话'
uv run --with pymupdf --with shapely python <skill>/scripts/annotation_tools.py summary <json> --output <md> --review-state <state>
uv run --with pymupdf --with shapely python <skill>/scripts/annotation_tools.py summary <json> --output <md> --review-state <state> --compact
uv run --with pymupdf --with shapely python <skill>/scripts/annotation_tools.py validate <json> --review-state <state> --check-dir
```

房名确认使用 `--status confirmed`；JSON尚未同步房名时还需 `--confirmed-name '实际名称'`。确认命令保存answer原话。JSON与确认结果冲突会阻止完成；明确不连接不会强制补边以使图全连通。

## 数字局部图

`questions`输出局部SVG与PNG，按外部记录读取固定号。房名题把核对号画进目标空间；连通题在两端空间内画稳定S编号的数字部分，K号写标题；邻接空间有位置时补数字。数字徽标须完整位于真实多边形内部，避开已记录开口；过窄时停止该题，助手放大或拆图，不把数字挪到房外。每图仍需目检原文字、疑似门洞和MD阅读尺寸。

该工具不是完整原图边栏总图生成器；总图仍按review-guide.md编排。原图不改动。

## 简洁MD与保护填写

自动概览由以下标记包围。普通summary仅替换这一区域，其余答案、图片链接和文字原样保留；标记损坏则停止更新。

```markdown
<!-- annotation:summary:start -->
自动状态、总数和文件链接
<!-- annotation:summary:end -->
```

首次处理没有标记的旧MD时，默认完整保留原文；助手先读取并同步所有答案，再执行 `--compact`。compact先把完整旧MD存入state旁的archive目录，然后保留待确认问题。无法识别的旧问题格式保守保留，由助手核对后精简，不得丢失尚未同步的填写。

日常MD最多保留：简短状态、三个总数、文件链接、待确认题及局部图。已同步题、候选历史、处理日志和类型明细只留外部，不再展示。全部完成后MD仅剩简短概览。

## 完成判断

validate分别返回valid、names_complete、connectivity_complete、visual_complete和complete。

- valid检查字段、几何、关系及SVG红点对应。
- names_complete始终检查当前JSON名称和unknown；区域模式不改变此事实。
- connectivity_complete要求主要连通已复核、关键问题已处理且确认结果已同步JSON。
- visual_complete来自实际全图／密集局部／核对图目检后的记录。
- full_names的complete要求全部满足。regional_coarse的complete／scope_complete要求结构有效、独立空间与区域外轮廓复核、主要连通、目检及必要问题均完成，允许资料缺名；full_semantics_complete仍要求房名齐全。
- 两种模式下，MD仍有待填写确认栏都会阻止完成，但该状态不当作核心JSON结构错误；确认与JSON冲突仍报错。区域模式不将归档的缺名项列入pending_review_ids，不丢弃原固定号。

checks由助手在完成相应实查后填写true，不能因工具运行成功自动置true。没有外部记录时，validate只证明结构和名称结果，complete为false；它不替代语义判读，也不要求每个节点都连入主网络。
