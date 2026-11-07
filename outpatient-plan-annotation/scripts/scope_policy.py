"""Explicit review scope; source-missing names are never converted to facts."""
MODES = {'full_names', 'regional_coarse'}


def annotation_mode(state):
    mode = (state or {}).get('annotation_mode', 'full_names')
    if mode not in MODES:
        raise ValueError('annotation_mode仅允许full_names或regional_coarse。')
    return mode


def names_required(state):
    return annotation_mode(state) == 'full_names'
