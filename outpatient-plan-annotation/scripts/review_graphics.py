"""Draw persistent review numbers inside the actual floor-plan polygons."""
from pathlib import Path
import base64
import json
import re
import xml.etree.ElementTree as ET
import pymupdf
from shapely.geometry import Polygon, box, Point
from review_workflow import external_path, load_state, save_state, sync_names, pending_name

from scope_policy import names_required

NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)


def element(tag, attrs=None, text=None):
    e = ET.Element('{' + NS + '}' + tag, {k: str(v) for k, v in (attrs or {}).items()})
    e.text = text
    return e


def badge_position(poly, label, scale, avoid, detail_score):
    x0, y0, x1, y1 = poly.bounds
    center = poly.representative_point()
    candidates = [(x0 + (x1 - x0) * x, y0 + (y1 - y0) * y)
                  for y in (.14, .82, .3, .65, .5) for x in (.14, .72, .4, .55, .85)]
    candidates.append((center.x, center.y))
    for font in (28, 24, 20, 16, 12):
        width, height = (len(label) * font * .66 + 12) / scale, (font + 12) / scale
        choices = []
        for x, y in candidates:
            rect = box(x, y, x + width, y + height)
            if poly.covers(rect) and not any(rect.intersects(a) for a in avoid):
                choices.append((detail_score(x, y, width, height), x, y))
        if choices:
            score, x, y = min(choices, key=lambda item: item[0])
            if score < .08:
                return x, y, width, height, font
    raise ValueError('空间过窄或开口附近无空位，请放大或拆分局部图后再放数字。')


def draw_question(data, sample, doc, items, focus, number, title, output):
    spaces = {s['id']: s for s in data['spaces']}
    polygons = {sid: Polygon(s['polygon_px']) for sid, s in spaces.items()}
    bounds = [polygons[sid].bounds for sid in focus]
    margin = 65
    if len(focus) == 2 and polygons[focus[0]].distance(polygons[focus[1]]) < 100:
        def priority(sid):
            s = spaces[sid]
            if s['type'] in {'hall', 'corridor', 'courtyard'}:
                return 3
            return {'room': 0, 'core_block': 1, 'functional_block': 2}.get(s['level'], 3)
        selected = min(focus, key=lambda sid: (priority(sid), polygons[sid].area))
        if priority(focus[0]) != priority(focus[1]):
            bounds = [polygons[selected].bounds]
            margin = 130
    w, h = data['image']['width'], data['image']['height']
    x0 = max(0, min(b[0] for b in bounds) - margin)
    y0 = max(0, min(b[1] for b in bounds) - margin)
    x1 = min(w, max(b[2] for b in bounds) + margin)
    y1 = min(h, max(b[3] for b in bounds) + margin)
    scale = min(1120 / (x1 - x0), 620 / (y1 - y0))
    ox, oy = 40 + (1120 - (x1 - x0) * scale) / 2, 95 + (620 - (y1 - y0) * scale) / 2
    root = element('svg', {'width': 1200, 'height': 800, 'viewBox': '0 0 1200 800',
        'data-source-scale': scale, 'data-source-offset': json.dumps([ox, oy]),
        'data-source-crop': json.dumps([x0, y0, x1, y1])})
    root.append(element('rect', {'width': 1200, 'height': 800, 'fill': 'white'}))
    root.append(element('text', {'x': 25, 'y': 40, 'font-size': 24,
        'font-family': 'Arial, PingFang SC, sans-serif', 'fill': '#18314c'}, title))
    page = doc[0]
    clip = pymupdf.Rect(x0 / w * page.rect.width, y0 / h * page.rect.height,
                       x1 / w * page.rect.width, y1 / h * page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False)
    def detail_score(x, y, width, height):
        dark = 0
        for row in range(7):
            for col in range(11):
                px = int((x + width * (col + .5) / 11 - x0) / (x1 - x0) * pix.width)
                py = int((y + height * (row + .5) / 7 - y0) / (y1 - y0) * pix.height)
                rgb = pix.pixel(max(0, min(px, pix.width-1)), max(0, min(py, pix.height-1)))
                dark += sum(rgb[:3]) < 270
        return dark / 77
    root.append(element('image', {'x': ox, 'y': oy, 'width': (x1 - x0) * scale,
        'height': (y1 - y0) * scale, 'href': 'data:image/png;base64,' + base64.b64encode(pix.tobytes('png')).decode()}))
    crop = box(x0, y0, x1, y1)
    avoid = [Point(o['position_px']).buffer(10) for r in data['relations'] for o in r.get('openings', [])]
    labels = {i['space_id']: i['review_id'] for i in items if 'space_id' in i}
    order = focus + [sid for sid, s in spaces.items() if sid not in focus and s['type'] != 'landscape']
    for sid in order:
        poly = polygons[sid].intersection(crop)
        if poly.is_empty or (sid not in focus and poly.area * scale * scale < 1600):
            continue
        color = '#2563eb' if sid in focus else '#0f766e'
        label = number if len(focus) == 1 and sid == focus[0] and not number.startswith('K') else sid[1:]
        if sid not in focus and sid in labels:
            label = labels[sid]
        try:
            x, y, bw, bh, font = badge_position(poly, label, scale, avoid, detail_score)
        except ValueError:
            if sid in focus:
                raise
            continue
        if sid in focus:
            parts = list(poly.geoms) if hasattr(poly, 'geoms') else [poly]
            for part in parts:
                if part.geom_type != 'Polygon':
                    continue
                points = ' '.join(f'{ox+(px-x0)*scale},{oy+(py-y0)*scale}' for px, py in part.exterior.coords)
                root.append(element('polygon', {'points': points, 'fill': 'none', 'stroke': color, 'stroke-width': 2}))
        xx, yy = ox + (x - x0) * scale, oy + (y - y0) * scale
        g = element('g', {'data-room-number': label, 'data-space-id': sid})
        g.append(element('rect', {'x': xx, 'y': yy, 'width': bw * scale, 'height': bh * scale,
            'rx': 4, 'fill': 'white', 'stroke': color, 'stroke-width': 2}))
        g.append(element('text', {'x': xx + 6, 'y': yy + font + 2, 'font-size': font,
            'font-family': 'Arial, sans-serif', 'font-weight': 'bold', 'fill': color}, label))
        root.append(g)
        avoid.append(box(x, y, x + bw, y + bh))
    raw = ET.tostring(root, encoding='unicode')
    Path(output).with_suffix('.svg').write_text(raw)
    with pymupdf.open(stream=raw.encode(), filetype='svg') as svg:
        svg[0].get_pixmap(alpha=False).save(Path(output).with_suffix('.png'))


def questions(data, sample, out_dir, state_path=None):
    out = external_path(out_dir, sample)
    state_path = external_path(state_path or out / 'review_state.json', sample)
    legacy = ['编号对应.txt', '固定核对映射.json', 'review-map.json', 'review_mapping.json']
    if not state_path.exists() and any((out / name).exists() for name in legacy):
        raise ValueError('已有固定核对映射；请先将旧编号导入--review-state，不能重新编号。')
    state = load_state(state_path, data, sample)
    sync_names(state, data)
    out.mkdir(parents=True, exist_ok=True)
    save_state(state_path, state)
    spaces = {s['id']: s for s in data['spaces']}
    with pymupdf.open(Path(sample).parent / data['image']['file']) as doc:
        for item in (state['name_items'] if names_required(state) else []):
            sid, number = item['space_id'], item['review_id']
            if not pending_name(spaces[sid]):
                continue
            draw_question(data, sample, doc, state['name_items'], [sid], number,
                          f'{number} | {sid}', out / f'{number}_{sid}')
        for item in state['connectivity_items']:
            if item.get('status', 'pending') != 'pending':
                continue
            focus = [item['source'], item['target']]
            number = item['review_id']
            draw_question(data, sample, doc, [], focus, number,
                          number + ' | ' + ' / '.join(focus), out / f'{number}_连通核对')
    mapping = '\n'.join(f'{i["review_id"]}: {i["space_id"]}' for i in state['name_items'])
    (out / '编号对应.txt').write_text(mapping + '\n')
