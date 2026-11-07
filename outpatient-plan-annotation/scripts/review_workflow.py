"""External review records and non-destructive, concise Markdown summaries."""
from pathlib import Path
from collections import Counter
from datetime import datetime
import hashlib
import html
import json
import re
from scope_policy import annotation_mode, names_required

START = '<!-- annotation:summary:start -->'
END = '<!-- annotation:summary:end -->'
NAME_PENDING = re.compile(r'待核|未确认|未分类|未知|候选|待定|unknown|tbd|\?', re.I)


def pending_name(space):
    return space['type'] == 'unknown' or not space['name'].strip() or bool(NAME_PENDING.search(space['name']))


def external_path(path, sample):
    path = Path(path).resolve()
    if path.is_relative_to(Path(sample).resolve().parent):
        raise ValueError('核对记录和图像必须放在正式样本目录之外。')
    return path


def load_state(path, data, sample):
    path = external_path(path, sample)
    state = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(state, dict):
        raise ValueError('核对记录必须是JSON对象。')
    image = data['image']
    signature = {'file': image['file'], 'width': image['width'], 'height': image['height'],
                 'sha256': hashlib.sha256((Path(sample).parent / image['file']).read_bytes()).hexdigest()}
    if state.get('image_signature', signature) != signature:
        raise ValueError('核对记录与本张原图不一致，不可跨图使用。')
    state['image_signature'] = signature
    state.setdefault('name_items', [])
    state.setdefault('connectivity_items', [])
    state.setdefault('checks', {})
    annotation_mode(state)
    if not isinstance(state['checks'], dict):
        raise ValueError('checks必须是对象。')
    spaces = {s['id']: s for s in data['spaces']}
    used = set()
    for key in ('name_items', 'connectivity_items'):
        if not isinstance(state[key], list):
            raise ValueError(f'{key}必须是列表。')
        for item in state[key]:
            if not isinstance(item, dict):
                raise ValueError('每条核对记录必须是对象。')
            rid = item.get('review_id', '')
            pattern = r'\d{2,}' if key == 'name_items' else r'K\d{2,}'
            if not isinstance(rid, str) or not re.fullmatch(pattern, rid) or rid in used:
                raise ValueError(f'核对号无效或重复：{rid}')
            used.add(rid)
            ids = [item.get('space_id')] if key == 'name_items' else [item.get('source'), item.get('target')]
            if any(sid not in spaces for sid in ids) or (len(ids) == 2 and ids[0] == ids[1]):
                raise ValueError(f'{rid}：空间编号无效；删除或合并空间后须同步核对记录。')
            allowed = {'pending', 'confirmed', 'resolved'} if key == 'name_items' else {
                'pending', 'confirmed_connected', 'confirmed_disconnected', 'not_required'}
            if item.get('status', 'pending') not in allowed:
                raise ValueError(f'{rid}：确认状态无效。')
    name_ids = [i['space_id'] for i in state['name_items']]
    if len(name_ids) != len(set(name_ids)):
        raise ValueError('同一空间不能对应多个房名核对号。')
    return state


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def sync_names(state, data):
    spaces = {s['id']: s for s in data['spaces']}
    items = {i['space_id']: i for i in state['name_items']}
    number = max((int(i['review_id']) for i in items.values()), default=0)
    for space in data['spaces']:
        item = items.get(space['id'])
        if pending_name(space) and item is None:
            number += 1
            item = {'review_id': f'{number:02}', 'space_id': space['id'], 'status': 'pending'}
            state['name_items'].append(item)
            items[space['id']] = item
        elif item and pending_name(space) and (item.get('status') == 'resolved'
                or (item.get('status') == 'confirmed' and item.get('applied') is True)):
            previous = {k: item[k] for k in ('status', 'answer', 'confirmed_name') if k in item}
            item.setdefault('history', []).append(previous)
            item.pop('answer', None)
            item.pop('confirmed_name', None)
            item.pop('applied', None)
            item['status'] = 'pending'
        elif item and not pending_name(space) and item.get('status', 'pending') == 'pending':
            # A resolved JSON name is not automatically a user confirmation.
            item.update(status='resolved', confirmed_name=space['name'])
        elif item and item.get('status') == 'confirmed' and not pending_name(space):
            item['applied'] = space['name'] == item.get('confirmed_name')
    return state


def record_confirmation(path, data, sample, review_id, status, answer, confirmed_name=None):
    state = load_state(path, data, sample)
    matches = [(kind, item) for kind in ('name_items', 'connectivity_items')
               for item in state[kind] if item['review_id'] == review_id]
    if len(matches) != 1 or not answer.strip() or answer.strip() in {'待确认', '待填写', '待定'}:
        raise ValueError('请提供已有核对号及明确的用户原话。')
    kind, item = matches[0]
    allowed = {'confirmed'} if kind == 'name_items' else {'confirmed_connected', 'confirmed_disconnected', 'not_required'}
    if status not in allowed:
        raise ValueError('该核对项不接受此确认状态。')
    item.update(status=status, answer=answer)
    if kind == 'name_items':
        space = next(s for s in data['spaces'] if s['id'] == item['space_id'])
        name = confirmed_name or (space['name'] if not pending_name(space) else None)
        if not name:
            raise ValueError('确认房名时须提供--confirmed-name，或先同步JSON中的名称。')
        item['confirmed_name'] = name
        item['applied'] = not pending_name(space) and space['name'] == name
    save_state(path, state)
    return item


def completion(data, sample, state_path=None):
    result = {'connectivity_complete': None, 'visual_complete': None, 'pending_review_ids': [],
              'review_errors': [], 'complete': False, 'scope_complete': False,
              'full_semantics_complete': False, 'pending_md_review': False}
    if not state_path:
        return result
    state = load_state(state_path, data, sample)
    sync_names(state, data)
    mode = annotation_mode(state)
    require_names = names_required(state)
    result.update(annotation_mode=mode, names_required=require_names)
    spaces = {s['id']: s for s in data['spaces']}
    access = {frozenset((r['source'], r['target'])) for r in data['relations'] if r['type'] == 'access'}
    for item in state['name_items']:
        if (item.get('status') == 'confirmed' and item.get('confirmed_name')
                and spaces[item['space_id']]['name'] != item['confirmed_name']):
            result['review_errors'].append(f'{item["review_id"]}：已确认房名尚未同步JSON。')
        if pending_name(spaces[item['space_id']]):
            if require_names:
                result['pending_review_ids'].append(item['review_id'])
        elif item.get('confirmed_name') and spaces[item['space_id']]['name'] != item['confirmed_name']:
            result['review_errors'].append(f'{item["review_id"]}：已确认房名尚未同步JSON。')
    pending_connections = []
    connection_errors = []
    for item in state['connectivity_items']:
        status = item.get('status', 'pending')
        rid = item['review_id']
        pair = frozenset((item['source'], item['target']))
        if status == 'pending':
            pending_connections.append(rid)
        elif status == 'confirmed_connected' and pair not in access:
            connection_errors.append(f'{rid}：已确认连接尚未同步JSON。')
        elif status == 'confirmed_disconnected' and pair in access:
            connection_errors.append(f'{rid}：已确认不连接，但JSON仍有access。')
    result['review_errors'] += connection_errors
    result['pending_review_ids'] += pending_connections
    result['connectivity_complete'] = (state['checks'].get('connectivity_reviewed') is True
                                       and not pending_connections and not connection_errors)
    result['visual_complete'] = state['checks'].get('visual_reviewed') is True
    md = Path(sample).parent / '标注说明.md'
    if md.exists() and re.search(r'^\*\*我的确认[／/]备注：(?:\*\*)?\s*(待填写|待确认|待定)',
                                 html.unescape(md.read_text()), re.M):
        result['pending_md_review'] = True
    regional = mode == 'regional_coarse'
    outlines_ok = True
    if regional:
        from region_overlay import validate_regions
        result['review_errors'] += validate_regions(state, data)
        outlines_ok = (state['checks'].get('individual_outline_reviewed') is True
                       and state['checks'].get('region_outline_reviewed') is True)
    result['outline_complete'] = outlines_ok if regional else None
    reviewed = (not result['review_errors'] and not result['pending_md_review']
                and result['connectivity_complete'] and result['visual_complete']
                and outlines_ok)
    all_names = not any(pending_name(s) for s in data['spaces'])
    result['full_semantics_complete'] = bool(reviewed and all_names)
    result['scope_complete'] = bool(reviewed and not result['pending_review_ids']
                                    and (all_names or not require_names))
    result['complete'] = result['scope_complete']
    return result


def archive_markdown(output, state_path, content):
    folder = Path(state_path).parent / 'archive'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    archive = folder / f'{stamp}_{Path(output).name}'
    archive.write_text(content)
    return archive


def pending_body(body, state):
    kinds = ('name_items', 'connectivity_items') if names_required(state) else ('connectivity_items',)
    pending = [i for kind in kinds for i in state[kind]
               if i.get('status', 'pending') == 'pending']
    if not pending:
        return ''
    chunks = re.split(r'(?=^###\s)', body, flags=re.M)
    kept = []
    covered = set()
    for chunk in chunks:
        if not chunk.startswith('### '):
            continue
        heading = chunk.splitlines()[0]
        for item in pending:
            rid = item['review_id']
            match = re.search(r'(?<![\w])' + re.escape(rid) + r'(?!\d)', heading)
            if match or (item.get('space_id') and item['space_id'] in chunk):
                covered.add(rid)
                if chunk not in kept:
                    kept.append(chunk)
    # Unknown legacy layouts are retained rather than silently dropping a question.
    return '\n'.join(kept).strip() if len(covered) == len(pending) else body.strip()


def summary(data, sample, output, state_path=None, compact=False):
    output = Path(output)
    old = output.read_text() if output.exists() else ''
    state = load_state(state_path, data, sample) if state_path else None
    if state is not None:
        sync_names(state, data)
        save_state(state_path, state)
    if compact and not state_path:
        raise ValueError('--compact须指定样本目录外的--review-state，以归档已有填写。')
    status = completion(data, sample, state_path)
    artifact_errors = []
    if status['complete']:
        # Completion language must use the same artifact gate as formal validate.
        output.touch(exist_ok=True)
        from annotation_tools import check
        checked, _ = check(data, sample, allow_incomplete=True, check_dir=True)
        artifact_errors += checked['errors']
        if state and not names_required(state):
            from region_overlay import validate_svg_regions
            artifact_errors += validate_svg_regions(Path(sample).parent/'标注.svg', state, data)
        if artifact_errors:
            status['complete'] = False
    names = sum(pending_name(s) for s in data['spaces'])
    connections = len([i for i in (state or {}).get('connectivity_items', []) if i.get('status', 'pending') == 'pending'])
    regional = not names_required(state)
    if regional:
        label = ('区域级粗轮廓：本轮完成。' if status['complete'] else
                 f'区域级粗轮廓：本轮未完成；连通待确认{connections}项。')
        label += f'原图缺失具体房名{names}项，保留unknown，不要求逐项补名。'
        if not status.get('outline_complete'):
            label += '独立空间或区域粗轮廓尚未完成复核。'
    elif status['complete']:
        label = '已完成；待确认0项。'
    else:
        label = f'草稿：房名待确认{names}项；连通待确认{connections}项。'
    if not status['connectivity_complete']:
        label += '连通尚未完成复核。'
    if not status['visual_complete']:
        label += '图面尚未完成目检。'
    if artifact_errors:
        label += '正式四文件／SVG校验尚未通过。'
    openings = sum(len(r.get('openings', [])) for r in data['relations'])
    generated = '\n'.join([START, f'**状态：**{label}', '',
        f'**规模：**{len(data["spaces"])}个空间，{len(data["relations"])}条关系，{openings}个门洞／开口。', '',
        f'[原图]({data["image"]["file"]}) · [标注图](标注.svg)', END])
    if START in old or END in old:
        if old.count(START) != 1 or old.count(END) != 1 or old.index(START) > old.index(END):
            raise ValueError('自动概览标记不完整；为保护填写，停止更新。')
        before, tail = old.split(START)
        _, after = tail.split(END)
        content = before + generated + after
    else:
        # Legacy text may contain free-form answers anywhere. Only --compact
        # may remove it, after archiving the original verbatim.
        body = old
        content = '# 标注说明\n\n' + generated + ('\n\n' + body.strip() if body.strip() else '') + '\n'
    if compact:
        if old:
            archive_markdown(output, state_path, old)
        body = content.split(END, 1)[1]
        kept = pending_body(body, state)
        content = '# 标注说明\n\n' + generated + ('\n\n' + kept if kept else '') + '\n'
    output.write_text(content)
