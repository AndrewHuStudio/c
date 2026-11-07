"""Regression coverage for evidence-limited regional annotations."""
import copy
import io
import contextlib
from unittest.mock import patch
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import review_workflow as workflow
import annotation_tools as annotation


class RegionalScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sample = self.root/'sample'
        self.sample.mkdir()
        (self.sample/'plan.jpg').write_bytes(b'fixture for source identity')
        self.path = self.sample/'annotation.json'
        self.state_path = self.root/'review_state.json'
        self.data = {'image': {'file': 'plan.jpg', 'width': 100, 'height': 100},
            'spaces': [dict(id='S001', name='名称未确认', type='unknown', level='room',
                scope='radiology', polygon_px=[[0,0],[40,0],[40,80],[0,80]], area_ratio=.5),
                dict(id='S002', name='影像通道', type='corridor', level='open_space',
                scope='radiology', polygon_px=[[40,0],[80,0],[80,80],[40,80]], area_ratio=.5)],
            'relations': [dict(id='R001', source='S001', target='S002', type='access')]}
        self.path.write_text(json.dumps(self.data))
        self.state = {'annotation_mode': 'regional_coarse',
            'name_items': [dict(review_id='01', space_id='S001', status='pending')],
            'connectivity_items': [],
            'checks': dict(individual_outline_reviewed=True, region_outline_reviewed=True,
                connectivity_reviewed=True, visual_reviewed=True),
            'regions': [dict(id='Z01', name='放射影像区', legend_number=7,
                member_space_ids=['S001','S002'], polygon_px=[[0,0],[80,0],[80,80],[0,80]])]}

    def result(self):
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False))
        return workflow.completion(self.data, self.path, self.state_path)

    def test_regional_missing_names_do_not_block_reviewed_scope(self):
        result = self.result()
        self.assertTrue(result['complete'])
        self.assertFalse(result['full_semantics_complete'])
        self.assertEqual(result['pending_review_ids'], [])
        self.assertEqual(self.data['spaces'][0]['type'], 'unknown')

    def test_default_still_requires_names(self):
        self.state.pop('annotation_mode')
        result = self.result()
        self.assertFalse(result['complete'])
        self.assertIn('01', result['pending_review_ids'])

    def test_pending_connectivity_still_blocks_regional_scope(self):
        self.state['connectivity_items'] = [dict(review_id='K01', source='S001',
            target='S002', status='pending')]
        result = self.result()
        self.assertFalse(result['complete'])
        self.assertEqual(result['pending_review_ids'], ['K01'])

    def test_unreviewed_individual_outlines_still_block(self):
        self.state['checks']['individual_outline_reviewed'] = False
        self.assertFalse(self.result()['complete'])

    def test_unreviewed_region_outlines_still_block(self):
        self.state['checks']['region_outline_reviewed'] = False
        self.assertFalse(self.result()['complete'])

    def test_confirmed_disconnection_conflict_still_blocks(self):
        self.state['connectivity_items'] = [dict(review_id='K01', source='S001',
            target='S002', status='confirmed_disconnected')]
        result = self.result()
        self.assertFalse(result['complete'])
        self.assertTrue(result['review_errors'])

    def test_region_members_must_exist(self):
        self.state['regions'][0]['member_space_ids'].append('S404')
        result = self.result()
        self.assertFalse(result['complete'])
        self.assertTrue(result['review_errors'])

    def test_confirmed_name_must_be_applied_even_in_regional_mode(self):
        self.state['name_items'][0].update(status='confirmed', confirmed_name='设备间', applied=False)
        result = self.result()
        self.assertFalse(result['complete'])
        self.assertTrue(result['review_errors'])

    def test_summary_preserves_answers_and_distinguishes_missing_names(self):
        self.result()
        md = self.sample/'标注说明.md'
        md.write_text('既有填写：K00=相通\n')
        annotation.render(self.data, self.path, self.sample/'标注.svg', self.state_path)
        workflow.summary(self.data, self.path, md, self.state_path)
        text = md.read_text()
        self.assertIn('既有填写：K00=相通', text)
        self.assertIn('本轮完成', text)
        self.assertNotIn('房名待确认1项', text)

    def cli(self, *args):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['annotation_tools.py', *map(str, args)]):
            with contextlib.redirect_stdout(output):
                code = annotation.main()
        return code, json.loads(output.getvalue())

    def test_cli_regional_success_and_default_name_failure(self):
        self.result()
        self.cli('render', self.path, '--output', self.sample/'标注.svg',
                 '--review-state', self.state_path)
        self.cli('summary', self.path, '--output', self.sample/'标注说明.md',
                 '--review-state', self.state_path)
        code, result = self.cli('validate', self.path, '--check-dir',
                                '--review-state', self.state_path)
        self.assertEqual(code, 0)
        self.assertTrue(result['complete'])
        self.assertFalse(result['names_complete'])
        self.assertEqual(self.cli('validate', self.path, '--check-dir')[0], 2)

    def test_cli_structural_error_cannot_leave_any_complete_field_true(self):
        self.result()
        self.data['spaces'][0]['area_ratio'] = .1
        self.path.write_text(json.dumps(self.data))
        code, result = self.cli('validate', self.path, '--review-state', self.state_path)
        self.assertEqual(code, 1)
        self.assertFalse(result['scope_complete'])
        self.assertFalse(result['complete'])
        self.assertFalse(result['full_semantics_complete'])

    def test_cli_missing_svg_region_is_not_complete(self):
        self.result()
        self.cli('render', self.path, '--output', self.sample/'标注.svg')
        self.cli('summary', self.path, '--output', self.sample/'标注说明.md',
                 '--review-state', self.state_path)
        code, result = self.cli('validate', self.path, '--check-dir',
                                '--review-state', self.state_path)
        self.assertEqual(code, 1)
        self.assertFalse(result['complete'])

    def test_small_unknown_label_stays_inside_room(self):
        from shapely.geometry import Polygon, box
        poly = Polygon([[0,0],[36,0],[36,30],[0,30]])
        x,y,w,h,font,lines = annotation.place_label(poly, self.data['spaces'][0])
        self.assertTrue(poly.covers(box(x,y,x+w,y+h)))

    def test_summary_cannot_announce_completion_without_svg(self):
        self.result()
        md = self.sample/'标注说明.md'
        md.write_text('既有填写保留')
        workflow.summary(self.data, self.path, md, self.state_path)
        self.assertNotIn('本轮完成', md.read_text())

    def test_region_overlay_does_not_become_duplicate_space(self):
        from region_overlay import add_regions
        import xml.etree.ElementTree as ET
        root = ET.Element('{http://www.w3.org/2000/svg}g')
        add_regions(root, self.state, self.data)
        self.assertEqual(len(self.data['spaces']), 2)
        self.assertEqual(sum(s['area_ratio'] for s in self.data['spaces']), 1)
        self.assertEqual(len([e for e in root.iter() if e.get('data-region-id')=='Z01']), 1)


if __name__ == '__main__':
    unittest.main()
