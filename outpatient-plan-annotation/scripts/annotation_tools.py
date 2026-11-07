#!/usr/bin/env python3
"""Render/check conceptual annotations. No automatic room recognition or training."""
from pathlib import Path
from collections import Counter
import argparse,base64,json,math,re,sys,xml.etree.ElementTree as ET

TYPE_NAMES=json.loads((Path(__file__).resolve().parent.parent/'references/space-types.json').read_text())
LEVELS={'room','open_space','semi_open_space','core_block','functional_block','landscape'}
LEVEL_NAMES={'room':'房间级单元','open_space':'开放空间','semi_open_space':'半墙服务区','core_block':'交通核','functional_block':'功能块','landscape':'绿化'}
SPACE_KEYS={'id','name','type','level','scope','polygon_px','area_ratio'}
REL_KEYS={'id','source','target','type','name','entrance_type','position_px','openings'}
REL_NAMES={'access':'人员可达','service':'服务联系','shared_wall':'共墙','entrance':'入口连接'}
PENDING=re.compile(r'待核|未确认|未分类|未知|候选|待定|unknown|tbd|\?',re.I)
NS='http://www.w3.org/2000/svg';XL='http://www.w3.org/1999/xlink'
ET.register_namespace('',NS);ET.register_namespace('xlink',XL)

def load(path):
    return json.loads(Path(path).read_text())

def polygon_area(points):
    return abs(sum(points[i][0]*points[(i+1)%len(points)][1]-points[(i+1)%len(points)][0]*points[i][1] for i in range(len(points)))/2)

def indoor(s):
    return s['type'] not in {'landscape','courtyard'} and s['level']!='landscape'

def unresolved(data):
    return [s for s in data['spaces'] if not str(s.get('name','')).strip() or PENDING.search(str(s.get('name',''))) or s.get('type')=='unknown']

def check(data,path,allow_incomplete=False,check_dir=False):
    from shapely.geometry import Polygon
    errors=[]
    if set(data)!={'image','spaces','relations'}:errors.append('顶层仅允许 image、spaces、relations。')
    image=data.get('image',{});w=image.get('width',0);h=image.get('height',0)
    if not isinstance(w,(int,float)) or not isinstance(h,(int,float)) or w<=0 or h<=0:errors.append('图像宽高无效。')
    source=Path(path).parent/str(image.get('file',''))
    if not source.is_file():errors.append('原图文件不存在。')
    spaces=data.get('spaces',[]);ids=[s.get('id') for s in spaces]
    if not spaces or len(ids)!=len(set(ids)) or any(not isinstance(i,str) or not i for i in ids):errors.append('空间编号为空或重复。')
    polygons=[]
    for s in spaces:
        sid=s.get('id','?')
        required=SPACE_KEYS-{'area_ratio'}
        if not required<=set(s) or not set(s)<=SPACE_KEYS:errors.append(f'{sid}: 空间字段不符约定。');continue
        if s['type'] not in TYPE_NAMES:errors.append(f'{sid}: 新类型 {s["type"]} 尚未加入共享类型表。')
        if s['level'] not in LEVELS:errors.append(f'{sid}: 空间层级无效。')
        pts=s['polygon_px']
        try:
            assert len(pts)>=3
            assert all(len(p)==2 and all(isinstance(v,(int,float)) and math.isfinite(v) for v in p) for p in pts)
            assert all(0<=x<=w and 0<=y<=h for x,y in pts)
            poly=Polygon(pts);assert poly.is_valid and poly.area>0;polygons.append((sid,poly))
        except (AssertionError,TypeError,ValueError):errors.append(f'{sid}: 多边形无效或超出原图。')
        if indoor(s) and not (isinstance(s.get('area_ratio'),(int,float)) and 0<s['area_ratio']<=1):errors.append(f'{sid}: 缺少有效的相对面积。')
        if not indoor(s) and 'area_ratio' in s:errors.append(f'{sid}: 非室内区域不参与室内面积归一化。')
    for i,(a,pa) in enumerate(polygons):
        for b,pb in polygons[i+1:]:
            if pa.intersection(pb).area>1:errors.append(f'{a}/{b}: 标注区域重叠，会重复计面积。')
    total=sum(s.get('area_ratio',0) for s in spaces if isinstance(s.get('area_ratio',0),(int,float)))
    if not math.isclose(total,1,abs_tol=1e-6):errors.append('室内相对面积占比合计须为 1。')
    rels=data.get('relations',[]);rids=[r.get('id') for r in rels]
    if len(rids)!=len(set(rids)) or any(not i for i in rids):errors.append('关系编号为空或重复。')
    keys=set();opening_ids=set();expected_openings={}
    for r in rels:
        rid=r.get('id','?');typ=r.get('type');a=r.get('source');b=r.get('target')
        if not {'id','source','target','type'}<=set(r) or not set(r)<=REL_KEYS:errors.append(f'{rid}: 关系字段不符约定。')
        if typ not in REL_NAMES or b not in ids:errors.append(f'{rid}: 关系类型或目标空间无效。')
        if typ=='entrance':
            if a is not None or not {'name','entrance_type','position_px'}<=set(r):errors.append(f'{rid}: 入口关系无效。')
            if not str(r.get('name','')).strip() or PENDING.search(str(r.get('name',''))):errors.append(f'{rid}: 入口名称未确认。')
            q=r.get('position_px',[])
            if len(q)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in q) or not(0<=q[0]<=w and 0<=q[1]<=h):errors.append(f'{rid}: 入口坐标无效。')
            key=(rid,a,b,typ)
        else:
            if a not in ids or a==b:errors.append(f'{rid}: 起点无效或自连接。')
            if any(k in r for k in ['position_px','entrance_type']):errors.append(f'{rid}: 普通连接的位置应保存在 openings 中；入口属性仅用于入口。')
            key=tuple(sorted([str(a),str(b)]))+(str(typ),)
        if 'openings' in r:
            if typ!='access':errors.append(f'{rid}: openings 仅用于人员可达，服务窗口和共墙不能标为通行开口。')
            openings=r['openings']
            if not isinstance(openings,list) or not openings:
                errors.append(f'{rid}: openings 须为非空列表；概念连通无定位点时省略此字段。');openings=[]
            for opening in openings:
                if not isinstance(opening,dict) or set(opening)!={'id','kind','position_px'}:
                    errors.append(f'{rid}: 开口字段须为 id、kind、position_px。');continue
                oid=opening['id'];q=opening['position_px']
                if not isinstance(oid,str) or not re.fullmatch(r'P\d{3,}',oid) or oid in opening_ids:
                    errors.append(f'{rid}: 开口编号无效或重复。')
                else:opening_ids.add(oid)
                if opening['kind'] not in ('door','open_passage'):errors.append(f'{rid}: 开口类型须为 door 或 open_passage。')
                valid_q=isinstance(q,list) and len(q)==2 and all(type(v) in (int,float) and math.isfinite(v) for v in q) and 0<=q[0]<=w and 0<=q[1]<=h
                if not valid_q:errors.append(f'{rid}: 开口坐标无效或超出原图。')
                elif isinstance(oid,str):expected_openings[oid]=(rid,a,b,q)
        if key in keys:errors.append(f'{rid}: 重复空间关系。')
        keys.add(key)
    if check_dir:
        files=[f for f in Path(path).parent.iterdir() if f.is_file()]
        if len(files)!=4 or sum(f.suffix.lower()=='.svg' for f in files)!=1 or sum(f.suffix.lower()=='.md' for f in files)!=1 or sum(f.suffix.lower()=='.json' for f in files)!=1:errors.append('最终图纸目录应只有原图、SVG、JSON、MD 四文件。')
        svgs=[f for f in files if f.suffix.lower()=='.svg']
        if len(svgs)==1:
            try:
                markers=[e for e in ET.parse(svgs[0]).iter() if e.get('data-opening-id') is not None]
                found={e.get('data-opening-id'):e for e in markers}
                if set(found)!=set(expected_openings) or len(found)!=len(markers):errors.append('SVG 红点编号与 JSON openings 不一一对应。')
                for oid,(rid,a,b,q) in expected_openings.items():
                    e=found.get(oid)
                    if e is None:continue
                    circles=[c for c in e if c.tag=='{'+NS+'}circle']
                    if e.get('data-relation-id')!=rid or e.get('data-source')!=a or e.get('data-target')!=b:errors.append(f'{oid}: SVG 红点连接空间与 JSON 不一致。')
                    if len(circles)!=1 or circles[0].get('fill')!='#dc2626' or any(not math.isclose(float(circles[0].get(k,'nan')),v,abs_tol=.01) for k,v in zip(('cx','cy'),q)):
                        errors.append(f'{oid}: SVG 红点坐标／颜色与 JSON 不一致。')
            except (ET.ParseError,ValueError,OSError):errors.append('SVG 无法读取或红点属性无效。')
    pending=[s['id'] for s in unresolved(data)]
    return {'valid':not errors,'names_complete':not pending,'pending_name_ids':pending,'errors':errors,'spaces':len(spaces),'relations':len(rels)}, (1 if errors else 2 if pending and not allow_incomplete else 0)

def normalize(data):
    areas={s['id']:polygon_area(s['polygon_px']) for s in data['spaces'] if indoor(s)}
    total=sum(areas.values())
    if total<=0:raise ValueError('没有可归一化的室内空间。')
    for s in data['spaces']:
        s.pop('area_m2',None);s.pop('area_px2',None);s.pop('area_ratio',None)
        if s['id'] in areas:s['area_ratio']=round(areas[s['id']]/total,8)

def element(tag,attrs=None,text=None):
    e=ET.Element('{'+NS+'}'+tag,{k:str(v) for k,v in (attrs or {}).items()})
    if text is not None:e.text=text
    return e

def txt(x,y,text,size=14,color='#18314c',bold=False):
    return element('text',{'x':x,'y':y,'font-size':size,'font-family':'Arial, PingFang SC, Microsoft YaHei, sans-serif','fill':color,'font-weight':'700' if bold else '400'},text)

def source_data(data,path):
    source=Path(path).parent/data['image']['file'];ext=source.suffix.lower()
    if ext in ['.jpg','.jpeg','.png']:
        mime='image/png' if ext=='.png' else 'image/jpeg';return f'data:{mime};base64,'+base64.b64encode(source.read_bytes()).decode()
    import pymupdf
    doc=pymupdf.open(source)
    if len(doc)!=1:raise ValueError('请先选定 PDF 页，并将每页作为独立图纸样本处理。')
    p=doc[0];w=data['image']['width'];h=data['image']['height']
    pix=p.get_pixmap(matrix=pymupdf.Matrix(w/p.rect.width,h/p.rect.height),alpha=False)
    return 'data:image/png;base64,'+base64.b64encode(pix.tobytes('png')).decode()

def visual_width(text,font):
    return sum(font*(.58 if ord(c)<128 else 1) for c in text)

def wrap(text,font,width):
    rows=['']
    for c in text:
        if rows[-1] and visual_width(rows[-1]+c,font)>width:rows.append('')
        rows[-1]+=c
    return rows

def place_label(poly,s):
    from shapely.geometry import LineString,box
    a=poly.representative_point();x,y=a.x,a.y
    horizontal=poly.intersection(LineString([(poly.bounds[0]-1,y),(poly.bounds[2]+1,y)]))
    vertical=poly.intersection(LineString([(x,poly.bounds[1]-1),(x,poly.bounds[3]+1)]))
    def span(g):
        gs=list(g.geoms) if hasattr(g,'geoms') else [g]
        lines=[q for q in gs if q.geom_type=='LineString' and q.distance(a)<.01]
        return max(lines,key=lambda q:q.length).bounds if lines else poly.bounds
    hb=span(horizontal);vb=span(vertical)
    maxw=max(12,min(220,hb[2]-hb[0]-6));maxh=max(10,vb[3]-vb[1]-6)
    pending=s['type']=='unknown' or PENDING.search(s['name'])
    name='名称未确认' if pending else s['name']
    category='〔'+TYPE_NAMES.get(s['type'],s['type'])+'〕'
    for font in [14,12,11,10,9,8,7]:
        lines=([s['id']+' '+name] if maxh<52 else [s['id']]+wrap(name,font,maxw-8))
        if not pending:lines.append(category)
        lw=max(visual_width(row,font) for row in lines)+8;lh=len(lines)*(font+2)+5
        if lw>maxw or lh>maxh:continue
        r=box(x-lw/2,y-lh/2,x+lw/2,y+lh/2)
        if poly.covers(r):return x-lw/2,y-lh/2,lw,lh,font,lines
    # Compact, fully contained fallback for small rooms and irregular corridors.
    bx0,by0,bx1,by1=poly.bounds
    for font in (7,6,5):
        for width in (min(160,bx1-bx0-2),min(90,bx1-bx0-2),min(45,bx1-bx0-2)):
            if width<10:continue
            lines=[s['id']]+wrap(name,font,width-4)
            if not pending:lines+=wrap(category,font,width-4)
            lw=max(visual_width(row,font) for row in lines)+4
            lh=len(lines)*(font+2)+4
            candidates=[(x,y)]+[(bx0+(bx1-bx0)*xx/12,by0+(by1-by0)*yy/12)
                for yy in range(1,12) for xx in range(1,12)]
            for cx,cy in candidates:
                if poly.covers(box(cx-lw/2,cy-lh/2,cx+lw/2,cy+lh/2)):
                    return cx-lw/2,cy-lh/2,lw,lh,font,lines
    raise ValueError(f'{s["id"]}：标签无法放入空间，请检查伪碎片或单独放大。')

def render(data,path,output,state_path=None):
    from shapely.geometry import Polygon
    w=data['image']['width'];h=data['image']['height'];head=max(65,min(125,w*.05));foot=max(20,min(35,w*.015))
    root=element('svg',{'width':w,'height':h+head+foot,'viewBox':f'0 0 {w} {h+head+foot}'})
    root.append(element('rect',{'width':w,'height':h+head+foot,'fill':'white'}))
    pending=unresolved(data);fs=max(10,min(30,w/70))
    state=None
    if state_path:
        from review_workflow import load_state
        state=load_state(state_path,data,path)
    root.append(txt(12,fs+10,'门诊平面结构化标注',fs,bold=True))
    status=f'未完成：{len(pending)} 个空间名称未确认' if pending else f'{len(data["spaces"])} 个具名空间／区域；名称已齐'
    if state and state.get('annotation_mode')=='regional_coarse':
        status=f'逐空间粗轮廓＋区域概览；{len(pending)}项原图缺名保持unknown；完成状态见标注说明'
    root.append(txt(12,fs*2+17,status,max(8,fs*.65),'#b45309' if pending else '#166534'))
    g=element('g',{'id':'plan','transform':f'translate(0 {head})'});root.append(g)
    g.append(element('image',{'width':w,'height':h,'{'+XL+'}href':source_data(data,path)}))
    palette=['#2563eb','#0f766e','#7c3aed','#15803d','#b45309','#0369a1','#a21caf','#475569']
    polygons={s['id']:Polygon(s['polygon_px']) for s in data['spaces']}
    for s in data['spaces']:
        p=polygons[s['id']];is_pending=s in pending
        import hashlib
        color='#d97706' if is_pending else palette[int(hashlib.sha256(s['type'].encode()).hexdigest()[:4],16)%len(palette)]
        if s['level']=='semi_open_space':color='#059669'
        sg=element('g',{'id':s['id'],'data-space-name':s['name'],'data-space-type':s['type'],'data-space-type-name':TYPE_NAMES.get(s['type'],s['type'])});g.append(sg)
        sg.append(element('title',text=f'{s["id"]} | {s["name"]} | {TYPE_NAMES.get(s["type"],s["type"])}'))
        attrs={'points':' '.join(f'{x},{y}' for x,y in s['polygon_px']),'fill':color,'fill-opacity':'.07','stroke':color,'stroke-width':max(1,w/1400)}
        if is_pending or s['level']=='semi_open_space':attrs['stroke-dasharray']='6 3'
        sg.append(element('polygon',attrs))
        x,y,lw,lh,font,lines=place_label(p,s)
        sg.append(element('rect',{'x':x,'y':y,'width':lw,'height':lh,'rx':2,'fill':'#fff7df' if is_pending else 'white','fill-opacity':'.94','stroke':color,'stroke-width':'.8'}))
        for i,line in enumerate(lines):sg.append(txt(x+4,y+font+2+i*(font+2),line,font,color,bold=True))
    if state and state.get('regions'):
        from region_overlay import add_regions
        add_regions(g,state,data)
    for r in data['relations']:
        for opening in r.get('openings',[]):
            x,y=opening['position_px'];oid=opening['id']
            og=element('g',{'id':oid,'data-opening-id':oid,'data-relation-id':r['id'],'data-source':r['source'],'data-target':r['target'],'data-kind':opening['kind']});g.append(og)
            og.append(element('title',text=f'{oid} | {r["id"]} | {r["source"]} ↔ {r["target"]} | '+('门洞' if opening['kind']=='door' else '无门开口')))
            og.append(element('circle',{'cx':x,'cy':y,'r':max(3,w/650),'fill':'#dc2626','stroke':'white','stroke-width':max(1,w/1800)}))
            label=txt(x+max(4,w/500),y-max(4,w/500),oid,max(6,min(9,w/310)),'#b91c1c',True)
            font=float(label.get('font-size'));lx=float(label.get('x'));ly=float(label.get('y'))
            og.append(element('rect',{'x':lx-1,'y':ly-font,'width':visual_width(oid,font)+2,'height':font+2,'fill':'white','fill-opacity':'.9'}))
            og.append(label)
    for r in data['relations']:
        if r['type']!='entrance':continue
        x,y=r['position_px'];p=polygons[r['target']].representative_point();dx=x-p.x;dy=y-p.y;length=math.hypot(dx,dy) or 1
        font=max(8,min(15,w/180));label=r['id']+' '+r['name'];lw=visual_width(label,font)+14;lh=font+10
        bx=max(2,min(w-lw-2,x+dx/length*46-lw/2));by=max(2,min(h-lh-2,y+dy/length*46-lh/2))
        eg=element('g',{'id':r['id']});g.append(eg)
        eg.append(element('line',{'x1':x,'y1':y,'x2':bx+lw/2,'y2':by+lh/2,'stroke':'#be185d','stroke-width':'2'}))
        eg.append(element('circle',{'cx':x,'cy':y,'r':max(3,w/400),'fill':'white','stroke':'#be185d','stroke-width':'2'}))
        eg.append(element('rect',{'x':bx,'y':by,'width':lw,'height':lh,'rx':3,'fill':'white','stroke':'#be185d'}))
        eg.append(txt(bx+7,by+font+3,label,font,'#9d174d',True))
    root.append(txt(12,h+head+foot-7,'标签：空间编号＋具体名称＋〔标准功能类别〕；红点 P：门洞／无门开口，与 JSON 连通关系对应；粉圈 E：建筑入口；黄色：名称未确认。',max(7,min(14,w/150)),'#52677b'))
    ET.ElementTree(root).write(output,encoding='unicode')

def summary(data,output,path=None,state_path=None,compact=False):
    from review_workflow import summary as write_summary
    write_summary(data,path or Path(output).parent/'annotation.json',output,state_path,compact)

def questions(data,path,out_dir,state_path=None):
    from review_graphics import questions as draw_questions
    draw_questions(data,path,out_dir,state_path)

def main():
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='command',required=True)
    for command in ['validate','normalize','render','summary','questions','confirm']:
        p=subs.add_parser(command);p.add_argument('json_path')
        p.add_argument('--review-state')
        if command=='validate':p.add_argument('--allow-incomplete',action='store_true');p.add_argument('--check-dir',action='store_true')
        if command in ['render','summary']:p.add_argument('--output',required=True)
        if command=='questions':p.add_argument('--out-dir',required=True)
        if command=='summary':p.add_argument('--compact',action='store_true')
        if command=='confirm':
            p.add_argument('--review-id',required=True);p.add_argument('--status',required=True)
            p.add_argument('--answer',required=True);p.add_argument('--confirmed-name')
    args=parser.parse_args();path=Path(args.json_path);data=load(path)
    if args.command=='confirm':
        from review_workflow import record_confirmation
        if not args.review_state:raise ValueError('confirm须指定--review-state。')
        item=record_confirmation(args.review_state,data,path,args.review_id,args.status,args.answer,args.confirmed_name)
        print(json.dumps(item,ensure_ascii=False,indent=2));return 0
    if args.command=='normalize':
        normalize(data);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');print('Relative area normalized; absolute-area fields removed.');return 0
    result,code=check(data,path,allow_incomplete=getattr(args,'allow_incomplete',False),check_dir=getattr(args,'check_dir',False))
    if args.command=='validate':
        from review_workflow import completion
        review=completion(data,path,args.review_state);result.update(review)
        result['errors']+=review['review_errors']
        if review.get('annotation_mode')=='regional_coarse' and args.check_dir:
            from region_overlay import validate_svg_regions
            state=load(args.review_state)
            result['errors']+=validate_svg_regions(path.parent/'标注.svg',state,data)
        result['valid']=not result['errors']
        for key in ('complete','scope_complete','full_semantics_complete'):
            result[key]=bool(result.get(key) and result['valid'])
        if review.get('annotation_mode')=='regional_coarse':
            code=1 if result['errors'] else 0 if result['complete'] else 2
        else:
            code=1 if result['errors'] else 2 if code==2 or (args.review_state and not result['complete']) else 0
        print(json.dumps(result,ensure_ascii=False,indent=2));return code
    if result['errors']:print(json.dumps(result,ensure_ascii=False,indent=2));return 1
    if args.command=='render':render(data,path,args.output,args.review_state)
    elif args.command=='summary':summary(data,args.output,path,args.review_state,args.compact)
    else:questions(data,path,args.out_dir,args.review_state)
    print(json.dumps({'written':getattr(args,'output',getattr(args,'out_dir',None)),'pending_name_ids':result['pending_name_ids']},ensure_ascii=False));return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,KeyError,TypeError,OSError,ImportError) as e:print(f'ERROR: {e}',file=sys.stderr);sys.exit(1)
