# 区域级图纸与逐空间粗轮廓

适用于原图用数字图例、科室名说明片区，内部具体房名缺失，用户希望保留布局结构或参照逐空间粗轮廓方法的任务。采用显式 `annotation_mode: regional_coarse`，不会自动放宽其他图纸。

## 轮廓及区域表达

1. 先核对原图片区与图例。每个可辨围合单元逐个保留S号、近似多边形、相对规模；缺名不是合并房间的理由。柜台、床位、卫生间厕位、梯段和电梯井通常保留在所属房间／交通核内。
2. 按原图隔墙、公共带和功能组织画区域外轮廓，能辨细边界时遵循该边界；无法辨清时注明概念近似。不要用巨大矩形覆盖中庭、相邻功能区或图外空白。粗轮廓是范围近似，并非单纯加粗描边。
3. 保留房间层时，把区域成员与外轮廓保存在外部review_state的regions；同一标注.svg显示较粗的区域边线与原房间轮廓。区域外框不进入spaces、不重复计面积，也不代表内部所有房间互通。
4. 纯区域节点适用于用户明确要求整体片区或原图无法辨出内部房间的部分；使用functional_block等现有层级，核心spaces仍保持非重叠。已有房间层不得未经用户要求丢弃或合并。

## 缺失名称与已知区域

- 具体名称无证据时保留unknown。图例“放射影像室”说明所属片区，不能把每个内部小间都标成特定检查室。
- 已知区域名称、图例号、scope和区域成员单独保存；清楚的大厅、卫生间、交通核等沿用已知名称。区域归属还不清楚时，同样记录不确定性。
- 原图资料缺名在MD汇总为功能信息缺失，不要求用户逐项猜名。旧固定核对号、候选和填写归档保留，未来有新证据可继续按原号更新。
- 未来补名模型的输出属于预测。它不能覆盖为原图已知真值；训练中如何使用缺失标签由具体实验定义，本技能不启动训练。

## 外部记录

```json
{
  "annotation_mode": "regional_coarse",
  "regions": [
    {"id": "Z01", "name": "放射影像区", "legend_number": 7,
     "member_space_ids": ["S001", "S002"],
     "polygon_px": [[100,100],[500,100],[500,400],[100,400]]}
  ],
  "checks": {
    "individual_outline_reviewed": true,
    "region_outline_reviewed": true,
    "connectivity_reviewed": true,
    "visual_reviewed": true
  }
}
```

检查值只有相应实查完成后才写true。regions可用polygons_px保存多个相互分开的轮廓片段；不能为连成一个多边形而跨过不属于该区域的空间。可选label_px为不遮挡房名及开口的区域文字位置。现有name_items继续保留原号和原状态，不假装已确认。

## 连通与完成

先按清晰门洞、无门开口、连续公共区及有依据的完整交通组织补主要关系。隐藏门只有充分布局依据才记概念access，不能由共墙或孤立节点自动补边。可靠点位仍保存P号；不确定点位不编造。

独立外门已明确、未知内部连接不影响主要通行组织的疑点，可记not_required并说明；原图缺失的辅助小间入口可以保持未知，不是用户确认不相通。会改变主要片区唯一通路且仍有不同解释的疑点保持pending。

完成前检查：独立单元无明显漏框、区域范围有实际外轮廓、核心几何和面积有效、主要连接复核、已知确认均同步、实际全图和密集局部目检。满足后可写“本轮区域级粗轮廓标注完成”；名称缺失仍如实列出。结构检查成功不能直接代表这些人工检查完成。

```bash
uv run --with pymupdf --with shapely python <skill>/scripts/annotation_tools.py render <json> --output <svg> --review-state <state>
uv run --with pymupdf --with shapely python <skill>/scripts/annotation_tools.py summary <json> --output <md> --review-state <state>
uv run --with pymupdf --with shapely python <skill>/scripts/annotation_tools.py validate <json> --check-dir --review-state <state>
```

返回字段：valid为结构及确认一致性；names_complete为实际房名完整性；outline_complete、connectivity_complete、visual_complete为各实查；scope_complete／complete为显式模式下的本轮范围完成；full_semantics_complete额外要求全部房名确定。必要待答问题保留pending_review_ids。区域模式检查成功可为complete=true而names_complete=false，必须同时说明这两个结果。
