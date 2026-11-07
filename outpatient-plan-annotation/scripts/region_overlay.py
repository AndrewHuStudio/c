"""Non-counting regional outlines kept outside the core space partition."""
import math
import xml.etree.ElementTree as ET
from shapely.geometry import Polygon

NS = 'http://www.w3.org/2000/svg'
COLORS = ['#1d4ed8', '#0f766e', '#b45309', '#9333ea', '#be123c', '#0369a1']


def region_polygons(region):
    if 'polygons_px' in region:
        return region['polygons_px']
    return [region['polygon_px']] if 'polygon_px' in region else []


def validate_regions(state, data):
    errors = []
    regions = state.get('regions', [])
    if not isinstance(regions, list) or not regions:
        return ['区域级粗轮廓模式需提供外部regions及可核验的区域轮廓。']
    ids = set()
    spaces = {s['id'] for s in data['spaces']}
    w, h = data['image']['width'], data['image']['height']
    for region in regions:
        rid = region.get('id', '')
        if not rid or rid in ids:
            errors.append('区域编号为空或重复。')
        ids.add(rid)
        if not str(region.get('name', '')).strip():
            errors.append(f'{rid}：区域名称为空。')
        members = region.get('member_space_ids', [])
        if (not members or len(set(members)) != len(members)
                or any(sid not in spaces for sid in members)):
            errors.append(f'{rid}：区域成员为空、重复或引用不存在的S号。')
        parts = region_polygons(region)
        if not parts:
            errors.append(f'{rid}：缺少区域粗轮廓。')
        for points in parts:
            try:
                assert len(points) >= 3
                assert all(len(p) == 2 and all(isinstance(v, (int, float))
                    and math.isfinite(v) for v in p) for p in points)
                assert all(0 <= x <= w and 0 <= y <= h for x, y in points)
                poly = Polygon(points)
                assert poly.is_valid and poly.area > 0
            except (AssertionError, TypeError, ValueError):
                errors.append(f'{rid}：区域粗轮廓无效或超出原图。')
    return errors


def add_regions(parent, state, data):
    if not state or not state.get('regions'):
        return
    errors = validate_regions(state, data)
    if errors:
        raise ValueError('；'.join(errors))
    layer = ET.SubElement(parent, '{'+NS+'}g', {'id': 'regional-outlines',
        'data-area-counted': 'false', 'pointer-events': 'none'})
    for index, region in enumerate(state['regions']):
        color = region.get('color', COLORS[index % len(COLORS)])
        group = ET.SubElement(layer, '{'+NS+'}g', {'data-region-id': region['id'],
            'data-region-name': region['name'], 'data-member-spaces':
            ' '.join(region['member_space_ids'])})
        ET.SubElement(group, '{'+NS+'}title').text = region['id']+' '+region['name']
        for points in region_polygons(region):
            ET.SubElement(group, '{'+NS+'}polygon', {
                'points': ' '.join(f'{x},{y}' for x, y in points), 'fill': 'none',
                'stroke': color, 'stroke-width': str(max(3, data['image']['width']/550)),
                'stroke-opacity': '.8', 'stroke-linejoin': 'round'})
        if 'label_px' in region:
            x, y = region['label_px']
            ET.SubElement(group, '{'+NS+'}text', {'x': str(x), 'y': str(y),
                'font-size': '19', 'font-family': 'Arial, PingFang SC, sans-serif',
                'fill': color, 'font-weight': 'bold'}).text = (
                    region['id']+' '+region['name'])


def validate_svg_regions(svg_path, state, data):
    """Check the actual SVG, not merely the availability of external polygons."""
    errors = validate_regions(state, data)
    if errors:
        return errors
    try:
        groups = [e for e in ET.parse(svg_path).iter() if e.get('data-region-id')]
        found = {g.get('data-region-id'): g for g in groups}
        expected = {r['id']: r for r in state['regions']}
        if len(groups) != len(found) or set(found) != set(expected):
            return ['SVG区域编号与外部regions不一一对应。']
        for rid, region in expected.items():
            group = found[rid]
            if (group.get('data-region-name') != region['name'] or
                    set(group.get('data-member-spaces', '').split()) !=
                    set(region['member_space_ids'])):
                errors.append(f'{rid}：SVG区域名称／成员与外部记录不一致。')
            points = [tuple(tuple(map(float, p.split(','))) for p in child.get('points','').split())
                      for child in group if child.tag == '{'+NS+'}polygon']
            expected_points = [tuple(tuple(map(float, p)) for p in part)
                               for part in region_polygons(region)]
            if sorted(points) != sorted(expected_points):
                errors.append(f'{rid}：SVG区域粗轮廓坐标与外部记录不一致。')
    except (ET.ParseError, ValueError, OSError):
        errors.append('SVG区域叠加层无法读取。')
    return errors
