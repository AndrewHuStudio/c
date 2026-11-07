from pathlib import Path
import json, subprocess, sys, tempfile, unittest, xml.etree.ElementTree as ET
import pymupdf
from shapely.geometry import Polygon, box

TOOL = Path(__file__).resolve().parents[1] / 'scripts/annotation_tools.py'


class ReviewWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plan = self.root / 'sample'
        self.plan.mkdir()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 200), False)
        pix.clear_with(255)
        pix.save(self.plan / 'source.png')
        self.data = {'image': {'file': 'source.png', 'width': 400, 'height': 200},
                     'spaces': [self.space('S001', '大厅', 'hall', 0, 200),
                                self.space('S002', '名称未确认', 'unknown', 200, 400)],
                     'relations': [{'id': 'R001', 'source': 'S001', 'target': 'S002', 'type': 'access'}]}
        self.file = self.plan / 'annotation.json'
        self.md = self.plan / '标注说明.md'
        self.work = self.root / 'review'
        self.work.mkdir()
        self.state = self.work / 'review_state.json'
        self.write()

    def tearDown(self):
        self.tmp.cleanup()

    def space(self, sid, name, typ, left, right):
        return {'id': sid, 'name': name, 'type': typ, 'level': 'room', 'scope': 'public_service',
                'polygon_px': [[left, 0], [right, 0], [right, 200], [left, 200]], 'area_ratio': .5}

    def write(self):
        self.file.write_text(json.dumps(self.data, ensure_ascii=False))

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True)

    def state_data(self, **kwargs):
        d = {'name_items': [], 'connectivity_items': [], 'checks': {}}
        d.update(kwargs)
        self.state.write_text(json.dumps(d, ensure_ascii=False))

    def known(self):
        self.data['spaces'][1].update(name='诊室', type='consultation')
        self.write()

    def test_questions_put_number_inside_actual_room(self):
        r = self.run_tool('questions', self.file, '--out-dir', self.work)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        svg = self.work / '01_S002.svg'
        self.assertTrue(svg.exists(), 'Review SVG should expose auditable room badges')
        root = ET.parse(svg).getroot()
        badge = next(e for e in root.iter() if e.get('data-room-number') == '01')
        self.assertEqual(badge.get('data-space-id'), 'S002')
        rect = next(e for e in badge if e.tag.endswith('}rect'))
        scale = float(root.get('data-source-scale'))
        ox, oy = json.loads(root.get('data-source-offset'))
        x0, y0, _, _ = json.loads(root.get('data-source-crop'))
        x = (float(rect.get('x')) - ox) / scale + x0
        y = (float(rect.get('y')) - oy) / scale + y0
        shape = box(x, y, x + float(rect.get('width')) / scale,
                    y + float(rect.get('height')) / scale)
        self.assertTrue(Polygon(self.data['spaces'][1]['polygon_px']).covers(shape))
        visible = ''.join(e.text or '' for e in badge.iter() if e.tag.endswith('}text'))
        self.assertEqual(visible, '01')

    def test_partial_confirmation_does_not_renumber(self):
        args = ('questions', self.file, '--out-dir', self.work, '--review-state', self.state)
        self.assertEqual(self.run_tool(*args).returncode, 0)
        self.known()
        self.data['spaces'][0].update(name='名称未确认', type='unknown')
        self.data['spaces'].reverse()
        self.write()
        self.assertEqual(self.run_tool(*args).returncode, 0)
        state = json.loads(self.state.read_text())
        ids = {i['space_id']: i['review_id'] for i in state['name_items']}
        self.assertEqual(ids, {'S002': '01', 'S001': '02'})

    def test_number_badge_avoids_dark_original_text_region(self):
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=200)
        page.draw_rect(pymupdf.Rect(215, 15, 280, 70), color=(0, 0, 0), fill=(0, 0, 0))
        page.get_pixmap().save(self.plan / 'source.png')
        r = self.run_tool('questions', self.file, '--out-dir', self.work)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        root = ET.parse(self.work / '01_S002.svg').getroot()
        badge = next(e for e in root.iter() if e.get('data-room-number') == '01')
        rect = next(e for e in badge if e.tag.endswith('}rect'))
        scale = float(root.get('data-source-scale'))
        ox, oy = json.loads(root.get('data-source-offset'))
        x0, y0, _, _ = json.loads(root.get('data-source-crop'))
        x = (float(rect.get('x')) - ox) / scale + x0
        y = (float(rect.get('y')) - oy) / scale + y0
        badge_box = box(x, y, x + float(rect.get('width')) / scale, y + float(rect.get('height')) / scale)
        self.assertFalse(badge_box.intersects(box(215, 15, 280, 70)))

    def test_connectivity_figure_numbers_both_endpoint_rooms(self):
        self.known()
        self.state_data(connectivity_items=[{'review_id': 'K01', 'source': 'S001',
                                            'target': 'S002', 'status': 'pending'}])
        r = self.run_tool('questions', self.file, '--out-dir', self.work, '--review-state', self.state)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        root = ET.parse(self.work / 'K01_连通核对.svg').getroot()
        labels = {e.get('data-space-id'): e.get('data-room-number')
                  for e in root.iter() if e.get('data-room-number')}
        self.assertEqual(labels, {'S001': '001', 'S002': '002'})
        self.assertGreater(json.loads(root.get('data-source-crop'))[0], 0,
                           'Focus on the room and adjoining hall, not the whole hall')

    def test_review_state_rejects_another_source_image(self):
        args = ('questions', self.file, '--out-dir', self.work, '--review-state', self.state)
        self.assertEqual(self.run_tool(*args).returncode, 0)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 200), False)
        pix.clear_with(100)
        pix.save(self.plan / 'source.png')
        r = self.run_tool(*args)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('原图', r.stdout + r.stderr)

    def test_legacy_mapping_cannot_be_silently_renumbered(self):
        (self.work / '编号对应.txt').write_text('07: S002\n')
        r = self.run_tool('questions', self.file, '--out-dir', self.work)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.work / '编号对应.txt').read_text(), '07: S002\n')

    def test_summary_preserves_existing_answers_and_links(self):
        body = '## 待确认\n\n### K01｜连接\n![局部](../review/local.png)\n\n**我的确认／备注：**不相通\n'
        self.md.write_text('# 旧概览\n\n旧统计。\n\n' + body)
        for _ in range(2):
            r = self.run_tool('summary', self.file, '--output', self.md)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(body, self.md.read_text())
        self.assertEqual(self.md.read_text().count('annotation:summary:start'), 1)

    def test_legacy_free_form_notes_are_preserved(self):
        original = '# 旧说明\n\n01=治疗室；02请保留这个原名。\n\n## 核对\n原有内容。\n'
        self.md.write_text(original)
        r = self.run_tool('summary', self.file, '--output', self.md)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(original, self.md.read_text())

    def test_compact_archives_answers_and_keeps_only_pending_questions(self):
        self.known()
        old = '# 旧概览\n\n## 连通核对\n\n### K01｜已回答\n**我的确认／备注：**不相通\n\n### K02｜未回答\n![局部](local.png)\n**我的确认／备注：**待填写\n'
        self.md.write_text(old)
        self.state_data(connectivity_items=[
            {'review_id': 'K01', 'source': 'S001', 'target': 'S002', 'status': 'confirmed_disconnected', 'answer': '不相通'},
            {'review_id': 'K02', 'source': 'S001', 'target': 'S002', 'status': 'pending'}])
        r = self.run_tool('summary', self.file, '--output', self.md, '--review-state', self.state, '--compact')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('### K02', self.md.read_text())
        self.assertNotIn('### K01', self.md.read_text())
        self.assertTrue(any(f.read_text() == old for f in (self.work / 'archive').glob('*.md')))

    def test_known_names_alone_do_not_claim_complete(self):
        self.known()
        r = self.run_tool('validate', self.file)
        self.assertEqual(r.returncode, 0)
        result = json.loads(r.stdout)
        self.assertFalse(result.get('complete', True))
        self.assertIsNone(result.get('connectivity_complete'))

    def test_pending_connectivity_blocks_completion(self):
        self.known()
        self.state_data(connectivity_items=[{'review_id': 'K01', 'source': 'S001', 'target': 'S002', 'status': 'pending'}],
                        checks={'connectivity_reviewed': True, 'visual_reviewed': True})
        r = self.run_tool('validate', self.file, '--review-state', self.state)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(r.stdout.strip(), r.stderr)
        self.assertFalse(json.loads(r.stdout)['complete'])

    def test_confirmed_disconnection_does_not_require_connected_graph(self):
        self.known()
        self.data['relations'] = []
        self.write()
        self.state_data(connectivity_items=[{'review_id': 'K01', 'source': 'S001', 'target': 'S002',
                                            'status': 'confirmed_disconnected', 'answer': '不相通'}],
                        checks={'connectivity_reviewed': True, 'visual_reviewed': True})
        r = self.run_tool('validate', self.file, '--review-state', self.state)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)['complete'])

    def test_confirmed_connection_must_exist_in_json(self):
        self.known()
        self.data['relations'] = []
        self.write()
        self.state_data(connectivity_items=[{'review_id': 'K01', 'source': 'S001', 'target': 'S002',
                                            'status': 'confirmed_connected', 'answer': '相通'}],
                        checks={'connectivity_reviewed': True, 'visual_reviewed': True})
        r = self.run_tool('validate', self.file, '--review-state', self.state)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('K01', r.stdout)
        self.assertFalse(json.loads(r.stdout)['connectivity_complete'])

    def test_reopened_unknown_keeps_question_and_confirmation_history(self):
        self.md.write_text('# 旧说明\n\n### 01｜S002\n**我的确认／备注：**诊室\n')
        self.state_data(name_items=[{'review_id': '01', 'space_id': 'S002', 'status': 'confirmed',
                                    'confirmed_name': '诊室', 'answer': '诊室', 'applied': True}])
        r = self.run_tool('summary', self.file, '--output', self.md, '--review-state', self.state, '--compact')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('### 01', self.md.read_text())
        item = json.loads(self.state.read_text())['name_items'][0]
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(item['history'][0]['answer'], '诊室')

    def test_confirmation_before_json_sync_keeps_expected_name_constraint(self):
        self.state_data(name_items=[{'review_id': '01', 'space_id': 'S002', 'status': 'pending'}],
                        checks={'connectivity_reviewed': True, 'visual_reviewed': True})
        r = self.run_tool('confirm', self.file, '--review-state', self.state, '--review-id', '01',
                          '--status', 'confirmed', '--answer', '这是药房', '--confirmed-name', '药房')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.run_tool('summary', self.file, '--output', self.md,
                                      '--review-state', self.state).returncode, 0)
        self.known()  # Deliberately wrong: JSON says 诊室, while the user confirmed 药房.
        self.assertEqual(self.run_tool('summary', self.file, '--output', self.md,
                                      '--review-state', self.state).returncode, 0)
        item = json.loads(self.state.read_text())['name_items'][0]
        self.assertEqual(item['confirmed_name'], '药房')
        r = self.run_tool('validate', self.file, '--review-state', self.state)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)['complete'])

    def test_confirmation_command_preserves_original_answer(self):
        self.state_data(connectivity_items=[{'review_id': 'K01', 'source': 'S001', 'target': 'S002', 'status': 'pending'}])
        r = self.run_tool('confirm', self.file, '--review-state', self.state, '--review-id', 'K01',
                          '--status', 'confirmed_disconnected', '--answer', '不链接，026连接右侧走道')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        item = json.loads(self.state.read_text())['connectivity_items'][0]
        self.assertEqual(item['status'], 'confirmed_disconnected')
        self.assertEqual(item['answer'], '不链接，026连接右侧走道')

    def test_fresh_summary_is_short_and_has_no_process_log(self):
        self.known()
        r = self.run_tool('summary', self.file, '--output', self.md)
        self.assertEqual(r.returncode, 0)
        text = self.md.read_text()
        self.assertLessEqual(len(text.splitlines()), 13)
        self.assertNotIn('类型与数量', text)
        self.assertNotIn('施工', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
