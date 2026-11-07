from pathlib import Path
import copy, json, subprocess, sys, tempfile, unittest, xml.etree.ElementTree as ET
import pymupdf

TOOL=Path(__file__).resolve().parents[1]/'scripts/annotation_tools.py'

class AnnotationContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name)
        pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,200,100),False);pix.clear_with(255);pix.save(self.p/'source.png')
        self.data={'image':{'file':'source.png','width':200,'height':100},'spaces':[
            {'id':'S001','name':'大厅','type':'hall','level':'open_space','scope':'public_service','polygon_px':[[0,0],[100,0],[100,100],[0,100]],'area_ratio':0.55555556},
            {'id':'S002','name':'诊室','type':'consultation','level':'room','scope':'ordinary_outpatient','polygon_px':[[100,0],[180,0],[180,100],[100,100]],'area_ratio':0.44444444},
            {'id':'S003','name':'绿化','type':'landscape','level':'landscape','scope':'nonclinical','polygon_px':[[180,0],[200,0],[200,100],[180,100]]}],
            'relations':[{'id':'R001','source':'S001','target':'S002','type':'access'},
                         {'id':'E001','source':None,'target':'S001','type':'entrance','name':'门诊入口','entrance_type':'outpatient_main','position_px':[0,50]}]}
        self.f=self.p/'annotation.json';self.write()
    def tearDown(self):self.tmp.cleanup()
    def write(self):self.f.write_text(json.dumps(self.data,ensure_ascii=False))
    def run_tool(self,*args):
        if not TOOL.exists():self.skipTest('helper not implemented yet')
        return subprocess.run([sys.executable,str(TOOL),*args],capture_output=True,text=True)
    def test_00_tool_exists(self):self.assertTrue(TOOL.is_file(),'Reusable annotation helper is missing')
    def test_incomplete_name_blocks_completion(self):
        self.data['spaces'][1]['name']='名称未确认';self.data['spaces'][1]['type']='unknown';self.write()
        r=self.run_tool('validate',str(self.f));self.assertEqual(r.returncode,2);self.assertIn('S002',r.stdout+r.stderr)
    def test_known_type_does_not_hide_unconfirmed_name(self):
        self.data['spaces'][1]['name']='超声室（候选）';self.write()
        self.assertEqual(self.run_tool('validate',str(self.f)).returncode,2)
    def test_partial_render_shows_names_and_unresolved_notice(self):
        self.data['spaces'][1]['name']='未确认';self.data['spaces'][1]['type']='unknown';self.write()
        out=self.p/'标注.svg';r=self.run_tool('render',str(self.f),'--output',str(out));self.assertEqual(r.returncode,0,r.stderr)
        root=ET.parse(out).getroot();visible=''.join(t.text or '' for t in root.iter() if t.tag.endswith('}text'))
        self.assertIn('大厅',visible);self.assertIn('未确认',visible)
    def test_render_preserves_specific_name_and_shows_shared_function_category(self):
        self.data['spaces'][0]['name']='门诊大厅';self.write()
        out=self.p/'标注.svg';r=self.run_tool('render',str(self.f),'--output',str(out))
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        groups={e.get('id'):e for e in ET.parse(out).iter() if e.get('data-space-name') is not None}
        hall=groups['S001'];visible=''.join(e.text or '' for e in hall.iter() if e.tag.endswith('}text'))
        self.assertEqual(hall.get('data-space-name'),'门诊大厅')
        self.assertEqual(hall.get('data-space-type'),'hall')
        self.assertEqual(hall.get('data-space-type-name'),'大厅')
        self.assertIn('门诊大厅',visible);self.assertIn('〔大厅〕',visible)
        self.assertEqual(json.loads(self.f.read_text())['spaces'][0]['name'],'门诊大厅')
    def test_normalize_uses_indoor_ratio_and_removes_absolute_areas(self):
        for s in self.data['spaces']:s['area_m2']=12;s['area_px2']=300;s.pop('area_ratio',None)
        self.write();r=self.run_tool('normalize',str(self.f));self.assertEqual(r.returncode,0,r.stderr)
        d=json.loads(self.f.read_text());self.assertAlmostEqual(sum(s.get('area_ratio',0) for s in d['spaces']),1,places=6)
        self.assertNotIn('area_ratio',d['spaces'][2]);self.assertTrue(all('area_m2' not in s and 'area_px2' not in s for s in d['spaces']))
    def test_render_summary_and_final_validation_keep_four_files(self):
        svg=self.p/'标注.svg';md=self.p/'标注说明.md'
        for args in [('render',str(self.f),'--output',str(svg)),('summary',str(self.f),'--output',str(md)),('validate',str(self.f),'--check-dir')]:
            r=self.run_tool(*args);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(len(list(self.p.iterdir())),4)
        root=ET.parse(svg).getroot();visible=''.join(t.text or '' for t in root.iter() if t.tag.endswith('}text'))
        self.assertIn('诊室',visible);self.assertIn('门诊入口',visible)
    def test_bad_relation_endpoint_rejected(self):
        self.data['relations'][0]['target']='S999';self.write()
        self.assertNotEqual(self.run_tool('validate',str(self.f)).returncode,0)
    def test_construction_fields_rejected(self):
        self.data['relations'][0]['door_width']=900;self.write()
        self.assertNotEqual(self.run_tool('validate',str(self.f)).returncode,0)
    def add_openings(self):
        self.data['relations'][0]['openings']=[
            {'id':'P001','kind':'door','position_px':[100,25]},
            {'id':'P002','kind':'open_passage','position_px':[100,75]}]
        self.write()
    def test_multiple_openings_render_as_red_points_with_relation_endpoints(self):
        self.add_openings();out=self.p/'标注.svg'
        r=self.run_tool('render',str(self.f),'--output',str(out))
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        points=[e for e in ET.parse(out).iter() if e.get('data-opening-id')]
        self.assertEqual({e.get('data-opening-id') for e in points},{'P001','P002'})
        for e in points:
            self.assertEqual(e.get('data-relation-id'),'R001')
            self.assertEqual(e.get('data-source'),'S001');self.assertEqual(e.get('data-target'),'S002')
            circles=[c for c in e if c.tag.endswith('}circle')]
            self.assertEqual(len(circles),1);self.assertEqual(circles[0].get('fill'),'#dc2626')
        self.assertEqual(len(self.data['relations']),2)
    def test_invalid_opening_positions_are_rejected(self):
        for q in ([201,25],[100,float('nan')],[100],None):
            with self.subTest(q=q):
                self.add_openings();self.data['relations'][0]['openings'][0]['position_px']=q;self.write()
                r=self.run_tool('validate',str(self.f))
                self.assertNotEqual(r.returncode,0)
                self.assertIn('开口坐标',r.stdout+r.stderr)
    def test_duplicate_opening_ids_are_rejected(self):
        self.add_openings();self.data['relations'][0]['openings'][1]['id']='P001';self.write()
        r=self.run_tool('validate',str(self.f))
        self.assertNotEqual(r.returncode,0);self.assertIn('开口编号',r.stdout+r.stderr)
    def test_service_and_shared_wall_cannot_have_access_openings(self):
        for typ in ('service','shared_wall'):
            with self.subTest(typ=typ):
                self.add_openings();self.data['relations'][0]['type']=typ;self.write()
                r=self.run_tool('validate',str(self.f))
                self.assertNotEqual(r.returncode,0);self.assertIn('人员可达',r.stdout+r.stderr)
    def test_final_check_detects_deleted_svg_red_point(self):
        self.add_openings();svg=self.p/'标注.svg';md=self.p/'标注说明.md'
        for args in [('render',str(self.f),'--output',str(svg)),('summary',str(self.f),'--output',str(md))]:
            r=self.run_tool(*args);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        r=self.run_tool('validate',str(self.f),'--check-dir');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        tree=ET.parse(svg)
        for parent in tree.iter():
            for child in list(parent):
                if child.get('data-opening-id')=='P001':parent.remove(child)
        tree.write(svg,encoding='unicode')
        r=self.run_tool('validate',str(self.f),'--check-dir')
        self.assertNotEqual(r.returncode,0);self.assertIn('SVG',r.stdout+r.stderr)
    def test_final_check_rejects_svg_point_at_wrong_position_or_with_wrong_endpoint(self):
        self.add_openings();svg=self.p/'标注.svg';md=self.p/'标注说明.md'
        r=self.run_tool('summary',str(self.f),'--output',str(md));self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        for change in ('position','endpoint'):
            with self.subTest(change=change):
                r=self.run_tool('render',str(self.f),'--output',str(svg));self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                tree=ET.parse(svg);marker=next(e for e in tree.iter() if e.get('data-opening-id')=='P001')
                if change=='position':next(e for e in marker if e.tag.endswith('}circle')).set('cx','130')
                else:marker.set('data-target','S003')
                tree.write(svg,encoding='unicode')
                r=self.run_tool('validate',str(self.f),'--check-dir')
                self.assertNotEqual(r.returncode,0);self.assertIn('SVG',r.stdout+r.stderr)

if __name__=='__main__':unittest.main(verbosity=2)
