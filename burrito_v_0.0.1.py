# -*- coding: utf-8 -*-
"""
Bake UI (single-list design) with Unicode icons (no Qt, no XPM).

- Uses emoji/symbols directly in button labels (Set B): ? ? ?? ?? ? ? ? ?? ??
- Restores section labels: 'Selection:', 'Marking:', 'Scene:'; status line left-aligned.
- Cameras list auto-populates with only *_ANIM transforms.
- No double-click toggle on the list (per request).
- Safe close: temporary hotkeys restored; window closes cleanly.
- 'Select in Scene' ONLY selects nodes — no camera framing to avoid crashes.

Shortcuts:
    Enter  ? toggle last selected in the list
    Ctrl+M ? mark selected
    Ctrl+U ? unmark selected
"""

from maya import cmds
import maya.mel as mel
import re

# ---- Your original imports used by your functions ----
import art.maya.app.scene_actors.scene_actors as scene_actors
from art.maya.app.retarget import core as retarget
from art.maya.app.context.camera import list_scene_cameras
import art.maya.app.context.camera2 as ctxCamera

# ============================================================
# YOUR ORIGINAL FUNCTIONS (UNCHANGED)
# ============================================================

def bake_all_actors_in_range(start_frame, end_frame):
    all_actors = scene_actors.get_all_actors_in_scene()
    print(all_actors)
    print(f'Baking all actors in range {start_frame} to {end_frame}...')
    if not all_actors:
        print('No actors found in the scene.')
        return
    for actor in all_actors:
        _ns = actor['namespace']
        if cmds.getAttr(f'{_ns}:M_GlobalSwitch01_CTRL.bind_skeleton_driver') == 1:
            print(f'Skipping baking actor {_ns} because bind_skeleton_driver is 1 so it is assumed to be already baked')
            continue
        _actor = actor['actor_name']
        print(f'baking {_ns}')
        _retarget = retarget.Retarget('Mocap_MCP_To_Control_Rig.py', source_namespace=_ns, target_namespace=_ns)
        _retarget.batch(start_frame, end_frame, force=True)
    print('Finished baking all actors.\n')


def _read_cam_attr_int(cam, attr):
    """
    Read an int attribute from a camera (show standard: on the TRANSFORM).
    Robust fallback order: ctxCamera → transform.getAttr → shape.getAttr.
    Returns an int or raises RuntimeError.
    """
    # ctxCamera
    try:
        v = ctxCamera.anim_camera_get_attr(cam, attribute_name=attr)
        if v is not None:
            return int(round(float(v)))
    except Exception:
        pass
    # transform
    try:
        plug = f'{cam}.{attr}'
        if cmds.objExists(plug):
            return int(round(float(cmds.getAttr(plug))))
    except Exception:
        pass
    # shape
    try:
        shp = (cmds.listRelatives(cam, shapes=True) or [None])[0]
        if shp and cmds.objExists(f'{shp}.{attr}'):
            return int(round(float(cmds.getAttr(f'{shp}.{attr}'))))
    except Exception:
        pass
    raise RuntimeError(f"Missing '{attr}' on {cam}")

def get_start_frame_from_layout_cam(camera='', pre_frame=True):
    start = _read_cam_attr_int(camera, 'headIn')
    return int(start) - int(pre_frame)


def get_end_frame_from_layout_cam(camera='', post_frame=True):
    end = _read_cam_attr_int(camera, 'tailOut')
    return int(end) + int(post_frame)


# ============================================================
# VISUAL CONSTANTS
# ============================================================

BTN_H = 32  # compact and consistent

# For the textScrollList, per-row images are not supported; use safe circle marks.
LIST_MARK_ON  = "🟢"   # marked to bake
LIST_MARK_OFF = "⚪"   # not marked
# ============================================================
# UTILITIES: cameras, rigs, expressions, time editor
# ============================================================

def _as_camera_transform(node):
    """Return camera transform from a transform/shape name; otherwise None."""
    if not node or not cmds.objExists(node):
        return None
    nt = cmds.nodeType(node)
    if nt == 'transform':
        shapes = cmds.listRelatives(node, shapes=True) or []
        if any(cmds.nodeType(s) == 'camera' for s in shapes):
            return node
        return None
    if nt == 'camera':
        parents = cmds.listRelatives(node, parent=True, type='transform') or []
        return parents[0] if parents else None
    return None


def list_all_camera_transforms():
    """Collect all camera transforms (layout utility + camera shapes) as a unique, stable-ordered list."""
    cams = []
    for c in (list_scene_cameras() or []):
        t = _as_camera_transform(c);  t and cams.append(t)
    for s in (cmds.ls(type='camera') or []):
        t = _as_camera_transform(s);  t and cams.append(t)
    return list(dict.fromkeys(cams))


def list_cameras_with_head_tail():
    """Return [{'cam': <transform>, 'headIn': int, 'tailOut': int}, ...]"""
    out = []
    for cam in list_all_camera_transforms():
        try:
            h = _read_cam_attr_int(cam, 'headIn')
            t = _read_cam_attr_int(cam, 'tailOut')
            out.append({'cam': cam, 'headIn': h, 'tailOut': t})
        except Exception:
            continue
    return out


def _read_bind_driver(ns):
    """Return bind_skeleton_driver value for the given namespace, or None if missing (ON when value==1)."""
    switch = f'{ns}:M_GlobalSwitch01_CTRL'
    attr = f'{switch}.bind_skeleton_driver'
    try:
        if cmds.objExists(switch) and cmds.attributeQuery('bind_skeleton_driver', n=switch, exists=True):
            return cmds.getAttr(attr)
    except Exception:
        pass
    return None


def list_scene_rigs():
    """Return rigs as [{'namespace': str, 'actor_name': str, 'bind_skeleton_driver': 0/1/None}, ...]."""
    rigs = []
    try:
        all_actors = scene_actors.get_all_actors_in_scene() or []
    except Exception:
        all_actors = []
    for a in all_actors:
        ns = a.get('namespace')
        nm = a.get('actor_name', '')
        bsd = _read_bind_driver(ns) if ns else None
        rigs.append({'namespace': ns, 'actor_name': nm, 'bind_skeleton_driver': bsd})
    return rigs

def _status_init_from_bind(ns, bind_driver):
    """Initialize status based on Bind Driver; do not overwrite existing status."""
    if ns not in _STATUS:
        _STATUS[ns] = 'baked' if bind_driver == 1 else 'not'

def _status_set(ns, state):
    """Set status and ignore unknown namespaces defensively."""
    if ns:
        _STATUS[ns] = state

def _status_get(ns):
    return _STATUS.get(ns, 'not')

# ---- Selective bake helper ----

def bake_selected_actors_in_range(namespaces, start_frame, end_frame, skip_if_baked=True):
    """
    Selective baking by namespace list. Respects bind_skeleton_driver if skip_if_baked=True.
    """
    if not namespaces:
        print('[INFO] No selected rigs; nothing to bake.')
        return
    print(f'Baking selected rigs: {namespaces} in range {start_frame}..{end_frame}')
    for ns in namespaces:
        bsd = _read_bind_driver(ns)
        if skip_if_baked and bsd == 1:
            print(f'Skipping {ns} (bind_skeleton_driver==1)')
            continue
        try:
            print(f'Baking {ns}...')
            retarget.Retarget('Mocap_MCP_To_Control_Rig.py', source_namespace=ns, target_namespace=ns) \
                .batch(int(start_frame), int(end_frame), force=True)
        except Exception as e:
            cmds.warning(f'[WARN] Failed baking {ns}: {e}')
    print('Finished selective bake.\n')

def bake_selected_actors_in_range_with_progress(namespaces, start_frame, end_frame):
    """
    Per-namespace bake with cancelable progress and status updates.
    - Marks 'skipped' if Bind Driver ON (respecting UI's Skip checkbox decision already applied).
    - Marks 'baked' on success, 'failed' on exception, 'canceled' when user cancels mid-loop.
    """
    total = len(namespaces)
    if total == 0:
        print('[INFO] No rigs to bake.')
        return

    try:
        cmds.progressWindow(
            title='BURRITO — Baking',
            status='Starting...',
            isInterruptable=True,
            maxValue=total,
            progress=0
        )

        for i, ns in enumerate(namespaces, 1):
            # show progress before starting this rig
            try:
                cmds.progressWindow(e=True, progress=i-1)
                import maya.utils; maya.utils.processIdleEvents()
            except Exception:
                pass
            if cmds.progressWindow(q=True, isCancelled=True):
                # Mark remaining (including current if not started) as canceled
                for rest in namespaces[i-1:]:
                    _status_set(rest, 'canceled')
                print('[INFO] Bake canceled by user.')
                break

            cmds.progressWindow(e=True, status=f'Baking: {ns} ({i}/{total})')

            try:
                bsd = _read_bind_driver(ns)
            except Exception:
                bsd = 0

            try:
                if bsd == 1:
                    _status_set(ns, 'skipped')
                    print(f'[INFO] Skipped {ns} (Bind Driver ON).')
                else:
                    # Actual bake call (same thing used by selective bake)
                    retarget.Retarget('Mocap_MCP_To_Control_Rig.py', source_namespace=ns, target_namespace=ns) \
                        .batch(int(start_frame), int(end_frame), force=True)
                    _status_set(ns, 'baked')
            except Exception as e:
                _status_set(ns, 'failed')
                cmds.warning(f'[WARN] Failed baking {ns}: {e}')
            finally:
                try:
                    cmds.progressWindow(e=True, progress=i)
                    import maya.utils
                    maya.utils.processIdleEvents()
                except Exception:
                    pass
    finally:
        try:
            cmds.progressWindow(endProgress=1)
        except Exception:
            pass

    # Refresh UI to reflect new statuses
    _populate_list()


def _list_broken_expressions():
    """Return list of (exprNode, [missingNodeNames]) for expression nodes that reference missing objects."""
    broken = []
    for ex in cmds.ls(type='expression') or []:
        try:
            txt = cmds.getAttr(ex + '.expression') or ''
        except Exception:
            continue
        nodes = set(m.group(1) for m in re.finditer(r'([A-Za-z0-9_:|]+)\.[A-Za-z0-9_]+', txt))
        missing = [n for n in nodes if not cmds.objExists(n)]
        if missing:
            broken.append((ex, missing))
    return broken


def mute_broken_expressions():
    """Temporarily clear broken expressions (empty script), keeping a backup to restore later."""
    global _muted_expr_backup
    _muted_expr_backup = {}
    broken = _list_broken_expressions()
    for ex, _missing in broken:
        try:
            txt = cmds.getAttr(ex + '.expression') or ''
            _muted_expr_backup[ex] = txt
            cmds.expression(ex, e=True, s='')
            print(f'[INFO] Muted broken expression: {ex}')
        except Exception as e:
            cmds.warning(f'[WARN] Could not mute expression {ex}: {e}')
    return broken


def restore_muted_expressions():
    """Restore the original text for expressions muted by mute_broken_expressions()."""
    for ex, txt in _muted_expr_backup.items():
        if cmds.objExists(ex):
            try:
                cmds.expression(ex, e=True, s=txt)
                print(f'[INFO] Restored expression: {ex}')
            except Exception as e:
                cmds.warning(f'[WARN] Could not restore expression {ex}: {e}')
    _muted_expr_backup.clear()


# ---- Time Editor wipe ----
_TIME_EDITOR_TYPES = [
    'timeEditor', 'timeEditorTracks', 'timeEditorClip', 'timeEditorSubTrack',
    'timeEditorAnimTrack', 'timeEditorAnimSource', 'timeEditorClipBlend'
]

def delete_time_editor_content():
    """Delete ALL Time Editor nodes (clips, sources, tracks). Destructive!"""
    deleted = []
    for t in _TIME_EDITOR_TYPES:
        nodes = cmds.ls(type=t) or []
        if nodes:
            try:
                cmds.delete(nodes)
                deleted.extend(nodes)
            except Exception as e:
                cmds.warning(f'[WARN] Failed to delete {t}: {e}')
    print(f'[INFO] Deleted Time Editor nodes: {deleted}')
    return deleted


# ============================================================
# UI STATE (single-list design) + helpers
# ============================================================

_UI = {}
_RIGS_CACHE = []           # [{namespace, actor_name, bind_skeleton_driver}, ...]
_MARKED_NS = set()         # namespaces marked as To Bake
_LIST_LABELS = []          # current visible labels in the list (after filters)
_LABEL2NS = {}             # label -> namespace (stable for current population)

# Hotkey bookkeeping
_HK = {
    'mark': {'key': 'm', 'ctl': True, 'alt': False, 'sht': False, 'prev': None, 'nc': 'BakeUI_Mark_cmd'},
    'unmark': {'key': 'u', 'ctl': True, 'alt': False, 'sht': False, 'prev': None, 'nc': 'BakeUI_Unmark_cmd'},
    'toggle': {'key': None, 'ctl': False, 'alt': False, 'sht': False, 'prev': None,
               'nc': 'BakeUI_Toggle_cmd', 'enter_keytrials': ['Enter', 'Return', 'KP_Enter']},
}

# ---- Bake status tracking (per-namespace) ----
# Values: 'not', 'baked', 'skipped', 'failed', 'canceled'
_STATUS = {}

_STATUS_ICON = {
    'not':      '⚪',
    'baked':    '🟢',
    'skipped':  '🟡',
    'failed':   '🔴',
    'canceled': '🔵',
}

_STATUS_LABEL = {
    'not':      'Not baked',
    'baked':    'Baked',
    'skipped':  'Skipped',
    'failed':   'Failed',
    'canceled': 'Canceled',
}

# Colors to match the circle legend (R,G,B 0..1)
_STATUS_COLOR = {
    'baked':    (0.40, 0.80, 0.40),  # green
    'skipped':  (0.95, 0.85, 0.35),  # yellow
    'failed':   (0.90, 0.40, 0.40),  # red
    'canceled': (0.40, 0.55, 0.95),  # blue
    'not':      (0.75, 0.75, 0.75),  # gray
}

def _rig_pretty_label(ns, actor_name='', bind_driver=None):
    """
    Row label:
      <mark-dot> <namespace — actor>    <status-dot> <status-label>
    Mark dot: 🟢 when marked to bake, ⚪ when not marked (prefix).
    Status dot at the end uses the baked/skipped/failed/canceled/not state.
    """
    mark = LIST_MARK_ON if ns in _MARKED_NS else LIST_MARK_OFF
    s = _status_get(ns)
    icon = _STATUS_ICON.get(s, '⚪')
    tag  = _STATUS_LABEL.get(s, 'Not baked')
    core = f'{ns} — {actor_name}' if actor_name else ns
    return f'{mark} {core}    {icon} {tag}'

def _refresh_rigs_cache():
    global _RIGS_CACHE, _MARKED_NS
    prev_marks = set(_MARKED_NS)
    _RIGS_CACHE = list_scene_rigs()
    scene_namespaces = {r['namespace'] for r in _RIGS_CACHE if r.get('namespace')}
    _MARKED_NS = prev_marks.intersection(scene_namespaces)
    # Seed per-ns status from BindDriver if unseen
    for r in _RIGS_CACHE:
        ns = r.get('namespace')
        if ns:
            _status_init_from_bind(ns, r.get('bind_skeleton_driver'))

def _collect_filter_flags():
    txt = cmds.textField(_UI['tf_filter'], q=True, tx=True) or ''
    hide_baked = cmds.checkBox(_UI['cb_hide_baked'], q=True, v=True)
    show_only_marked = cmds.checkBox(_UI['cb_show_only_marked'], q=True, v=False)
    return txt, hide_baked, show_only_marked

def _populate_list():
    """Rebuild the list from cache + filters; keep selection by namespace when possible."""
    global _LIST_LABELS, _LABEL2NS

    sel_labels = cmds.textScrollList(_UI['tsl_rigs'], q=True, si=True) or []
    sel_ns_keep = set(_LABEL2NS.get(l) for l in sel_labels if l in _LABEL2NS)

    _LIST_LABELS, _LABEL2NS = [], {}
    txt, hide_baked, show_only_marked = _collect_filter_flags()
    cmds.textScrollList(_UI['tsl_rigs'], e=True, ra=True)

    for rig in _RIGS_CACHE:
        ns = rig.get('namespace')
        if not ns:
            continue
        bsd = rig.get('bind_skeleton_driver')
        if hide_baked and bsd == 1:
            continue
        if show_only_marked and ns not in _MARKED_NS:
            continue
        label = _rig_pretty_label(ns, rig.get('actor_name', ''), bsd)
        if txt and txt.lower() not in label.lower():
            continue
        _LIST_LABELS.append(label)
        _LABEL2NS[label] = ns

    for l in _LIST_LABELS:
        cmds.textScrollList(_UI['tsl_rigs'], e=True, a=l)

    if sel_ns_keep:
        indices = []
        for i, l in enumerate(_LIST_LABELS, start=1):
            if _LABEL2NS[l] in sel_ns_keep:
                indices.append(i)
        if indices:
            cmds.textScrollList(_UI['tsl_rigs'], e=True, sii=indices)

    _update_counts()

def _update_counts(*args):
    selected = len(cmds.textScrollList(_UI['tsl_rigs'], q=True, si=True) or [])
    marked = len(_MARKED_NS)
    total = len(_RIGS_CACHE)

    # Count per status
    s_counts = {'baked': 0, 'skipped': 0, 'failed': 0, 'canceled': 0, 'not': 0}
    for rig in _RIGS_CACHE:
        ns = rig.get('namespace')
        if not ns:
            continue
        s = _status_get(ns)
        s_counts[s] = s_counts.get(s, 0) + 1

    # Update left summary (no icons, no colors)
    if 'status_base' in _UI:
        base = f"Status:   Selected: {selected}    To Bake: {marked}    Total: {total}"
        cmds.text(_UI['status_base'], e=True, l=base)

    # Update numeric counts on the right
    if 'cnt_baked' in _UI:
        cmds.text(_UI['cnt_baked'],    e=True, l=str(s_counts['baked']))
        cmds.text(_UI['cnt_skipped'],  e=True, l=str(s_counts['skipped']))
        cmds.text(_UI['cnt_failed'],   e=True, l=str(s_counts['failed']))
        cmds.text(_UI['cnt_canceled'], e=True, l=str(s_counts['canceled']))
        cmds.text(_UI['cnt_not'],      e=True, l=str(s_counts['not']))

def _select_all(*args):
    if _LIST_LABELS:
        cmds.textScrollList(_UI['tsl_rigs'], e=True, sii=list(range(1, len(_LIST_LABELS)+1)))
    _update_counts()

def _select_none(*args):
    cmds.textScrollList(_UI['tsl_rigs'], e=True, da=True)
    _update_counts()

def _select_invert(*args):
    tsl = _UI['tsl_rigs']
    current = set(cmds.textScrollList(tsl, q=True, sii=True) or [])
    all_idx = set(range(1, len(_LIST_LABELS)+1))
    new_sel = sorted(list(all_idx - current))
    cmds.textScrollList(tsl, e=True, sii=new_sel)
    _update_counts()

def _select_from_maya(*args):
    """Select rows whose namespaces are found in Maya's current selection (viewport/outliner)."""
    sel = cmds.ls(sl=True, long=True) or []
    ns_set = set()
    for node in sel:
        if ':' in node:
            ns_set.add(node.split(':', 1)[0])
    if not ns_set:
        cmds.warning('No namespaces found in current Maya selection.')
        return
    indices = []
    for i, label in enumerate(_LIST_LABELS, start=1):
        ns = _LABEL2NS.get(label)
        if ns in ns_set:
            indices.append(i)
    if indices:
        cmds.textScrollList(_UI['tsl_rigs'], e=True, sii=indices)
        _update_counts()
    else:
        cmds.warning('No visible rigs matched the namespaces from Maya selection.')

def _selected_namespaces():
    labels = cmds.textScrollList(_UI['tsl_rigs'], q=True, si=True) or []
    return [_LABEL2NS[l] for l in labels if l in _LABEL2NS]

def _mark_selected(*args):
    ns_list = _selected_namespaces()
    if not ns_list:
        return
    for ns in ns_list:
        _MARKED_NS.add(ns)
    _populate_list()

def _unmark_selected(*args):
    ns_list = _selected_namespaces()
    if not ns_list:
        return
    for ns in ns_list:
        _MARKED_NS.discard(ns)
    _populate_list()

def _toggle_selected(*args):
    ns_list = _selected_namespaces()
    if not ns_list:
        return
    for ns in ns_list:
        if ns in _MARKED_NS: _MARKED_NS.discard(ns)
        else:                _MARKED_NS.add(ns)
    _populate_list()

def _clear_all_marks(*args):
    if not _MARKED_NS:
        return
    _MARKED_NS.clear()
    _populate_list()

def _select_in_scene(*args):
    """Select nodes in the scene for selected list entries (no framing to avoid crashes)."""
    ns_list = _selected_namespaces()
    if not ns_list:
        return
    nodes = []
    for ns in ns_list:
        nodes.extend(cmds.ls(ns + ':*') or [])
    if nodes:
        cmds.select(nodes, r=True)
    else:
        cmds.warning('No scene nodes found for the selected namespaces.')


# ============================================================
# Hotkeys (temporary)
# ============================================================

def _ensure_name_command(name, pyfunc_call):
    """Create/update a name/runTime command that calls the given python snippet."""
    if cmds.runTimeCommand(name, exists=True):
        try:
            cmds.runTimeCommand(name, e=True, c=pyfunc_call, ann=name)
        except Exception:
            pass
        return name
    try:
        cmds.runTimeCommand(name, c=pyfunc_call, ann=name)
    except Exception:
        if cmds.nameCommand(name, exists=True):
            cmds.nameCommand(name, e=True, ann=name, c=pyfunc_call)
        else:
            cmds.nameCommand(name, ann=name, c=pyfunc_call)
    return name

def _register_hotkeys():
    """Bind Ctrl+M, Ctrl+U, and Enter/Return/KP_Enter while the window is open; store previous mappings."""
    # Mark selected (Ctrl+M)
    _ensure_name_command(_HK['mark']['nc'], 'python("_hotkey_mark_selected()")')
    prev = cmds.hotkey(q=True, keyShortcut=_HK['mark']['key'], ctl=True, alt=False, sht=False, name=True)
    _HK['mark']['prev'] = prev
    cmds.hotkey(keyShortcut=_HK['mark']['key'], ctl=True, alt=False, sht=False, name=_HK['mark']['nc'])

    # Unmark selected (Ctrl+U)
    _ensure_name_command(_HK['unmark']['nc'], 'python("_hotkey_unmark_selected()")')
    prev = cmds.hotkey(q=True, keyShortcut=_HK['unmark']['key'], ctl=True, alt=False, sht=False, name=True)
    _HK['unmark']['prev'] = prev
    cmds.hotkey(keyShortcut=_HK['unmark']['key'], ctl=True, alt=False, sht=False, name=_HK['unmark']['nc'])

    # Toggle last selected (Enter variants)
    _ensure_name_command(_HK['toggle']['nc'], 'python("_hotkey_toggle_last_selected()")')
    _HK['toggle']['prev'] = None
    _HK['toggle']['bound'] = None
    for keyname in _HK['toggle']['enter_keytrials']:
        try:
            prev = cmds.hotkey(q=True, keyShortcut=keyname, ctl=False, alt=False, sht=False, name=True)
            _HK['toggle']['prev'] = (keyname, prev)
            cmds.hotkey(keyShortcut=keyname, ctl=False, alt=False, sht=False, name=_HK['toggle']['nc'])
            _HK['toggle']['bound'] = keyname
            break
        except Exception:
            continue
    if not _HK['toggle'].get('bound'):
        cmds.warning('[WARN] Could not bind Enter key (Enter/Return/KP_Enter).')

def _unregister_hotkeys():
    """Restore previous hotkeys (do not delete commands to avoid instability)."""
    # Mark
    prev = _HK['mark']['prev']
    try:
        cmds.hotkey(keyShortcut=_HK['mark']['key'], ctl=True, alt=False, sht=False, name=(prev or ''))
    except Exception:
        pass
    # Unmark
    prev = _HK['unmark']['prev']
    try:
        cmds.hotkey(keyShortcut=_HK['unmark']['key'], ctl=True, alt=False, sht=False, name=(prev or ''))
    except Exception:
        pass
    # Toggle
    prev_pair = _HK['toggle']['prev']
    bound = _HK['toggle'].get('bound')
    if bound:
        try:
            cmds.hotkey(keyShortcut=bound, ctl=False, alt=False, sht=False,
                        name=(prev_pair[1] if (prev_pair and prev_pair[0] == bound and prev_pair[1]) else ''))
        except Exception:
            pass

# Hotkey targets (must be global names)
def _hotkey_mark_selected():
    if cmds.window('BakeSeqRangeUI', exists=True):
        _mark_selected()

def _hotkey_unmark_selected():
    if cmds.window('BakeSeqRangeUI', exists=True):
        _unmark_selected()

def _hotkey_toggle_last_selected():
    if not cmds.window('BakeSeqRangeUI', exists=True):
        return
    labels = cmds.textScrollList(_UI['tsl_rigs'], q=True, si=True) or []
    if not labels:
        return
    last = labels[-1]
    ns = _LABEL2NS.get(last)
    if not ns:
        return
    if ns in _MARKED_NS: _MARKED_NS.discard(ns)
    else:                _MARKED_NS.add(ns)
    _populate_list()


# ============================================================
# Range helpers (preview + validation)
# ============================================================

def _on_toggle_prepost(*args):
    enabled = cmds.checkBox(_UI['cb_prepost_enable'], q=True, v=True)
    cmds.intField(_UI['if_pre'], e=True, en=enabled)
    cmds.intField(_UI['if_post'], e=True, en=enabled)
    _update_preview_label()

def _on_method_changed(*args):
    method = cmds.radioButtonGrp(_UI['rbg_method'], q=True, sl=True)  # 1=cameras, 2=timeline, 3=custom
    cmds.optionMenu(_UI['om_first'], e=True, en=(method == 1))
    cmds.optionMenu(_UI['om_last'],  e=True, en=(method == 1))
    custom_en = (method == 3)
    cmds.intField(_UI['if_start'], e=True, en=custom_en)
    cmds.intField(_UI['if_end'],   e=True, en=custom_en)
    _update_preview_label()

def _select_option_value(om, value, allow_suffix=False):
    try:
        cmds.optionMenu(om, e=True, v=value);  return
    except RuntimeError:
        if allow_suffix:
            suff = f'{value}  (HT)'
            try: cmds.optionMenu(om, e=True, v=suff);  return
            except RuntimeError: pass

def _ui_refresh_cameras(*args):
    """
    Rebuild camera menus; only include transforms ending with '_ANIM'.
    Mark items with (HT) if headIn/tailOut are readable via ctxCamera.
    Auto-pick min headIn / max tailOut among the filtered set.
    """
    all_transforms = list_all_camera_transforms()
    cams_all = [c for c in all_transforms if c.endswith('_ANIM')]

    cams_ht_all = list_cameras_with_head_tail()
    cams_ht = [d for d in cams_ht_all if d['cam'] in cams_all]
    cams_ht_names = [d['cam'] for d in cams_ht]

    for key in ('om_first', 'om_last'):
        om = _UI[key]
        for mi in cmds.optionMenu(om, q=True, ill=True) or []:
            cmds.deleteUI(mi)
        for c in cams_all:
            label = c if c not in cams_ht_names else f'{c}  (HT)'
            cmds.menuItem(label=label, parent=om)

    if cams_ht:
        first = min(cams_ht, key=lambda d: d['headIn'])['cam']
        last  = max(cams_ht, key=lambda d: d['tailOut'])['cam']
        _select_option_value(_UI['om_first'], first, allow_suffix=True)
        _select_option_value(_UI['om_last'],  last,  allow_suffix=True)

    _on_method_changed(); _update_preview_label()

def _compute_preview_range():
    method = cmds.radioButtonGrp(_UI['rbg_method'], q=True, sl=True)
    prepost_enabled = cmds.checkBox(_UI['cb_prepost_enable'], q=True, v=True)
    pre  = cmds.intField(_UI['if_pre'],  q=True, v=True) if prepost_enabled else 0
    post = cmds.intField(_UI['if_post'], q=True, v=True) if prepost_enabled else 0
    if method == 1:  # From Cameras
        def normalize(label): return label.replace('  (HT)', '')
        first_label = cmds.optionMenu(_UI['om_first'], q=True, v=True)
        last_label  = cmds.optionMenu(_UI['om_last'],  q=True, v=True)
        sf = int(get_start_frame_from_layout_cam(camera=normalize(first_label), pre_frame=pre))
        ef = int(get_end_frame_from_layout_cam(camera=normalize(last_label),  post_frame=post))
        return sf, ef
    elif method == 2:  # Timeline
        min_t = int(cmds.playbackOptions(q=True, min=True))
        max_t = int(cmds.playbackOptions(q=True, max=True))
        return min_t - int(pre), max_t + int(post)
    else:  # Custom
        start = int(cmds.intField(_UI['if_start'], q=True, v=True))
        end   = int(cmds.intField(_UI['if_end'],   q=True, v=True))
        return start - int(pre), end + int(post)

def _update_preview_label(*args):
    try:
        s, e = _compute_preview_range()
        cmds.text(_UI['txt_preview'], e=True, l=f'Preview range: {int(s)} .. {int(e)}')
    except Exception:
        cmds.text(_UI['txt_preview'], e=True, l='Preview: set valid cameras or adjust range options')

def _validate_range(start_frame, end_frame):
    s = int(start_frame); e = int(end_frame)
    if s > e:
        cmds.warning('[WARN] Start frame is greater than end frame. Swapping values.')
    return min(s, e), max(s, e)


# ============================================================
# Run bake
# ============================================================

def _on_run_bake(*args):
    # Options
    prepost_enabled = cmds.checkBox(_UI['cb_prepost_enable'], q=True, v=True)
    pre  = cmds.intField(_UI['if_pre'],  q=True, v=True) if prepost_enabled else 0
    post = cmds.intField(_UI['if_post'], q=True, v=True) if prepost_enabled else 0
    method = cmds.radioButtonGrp(_UI['rbg_method'], q=True, sl=True)
    allow_fallback = cmds.checkBox(_UI['cb_fallback'], q=True, v=True)
    mute_expr = cmds.checkBox(_UI['cb_mute'], q=True, v=True)
    restore_expr = cmds.checkBox(_UI['cb_restore'], q=True, v=True)
    wipe_te = cmds.checkBox(_UI['cb_timeeditor'], q=True, v=True)
    skip_baked = cmds.checkBox(_UI['cb_skip_baked'], q=True, v=True)

    # Decide rigs to bake
    bake_namespaces = sorted(list(_MARKED_NS))
    if not bake_namespaces:
        res = cmds.confirmDialog(
            title='Bake All Rigs?',
            message=('No rigs are marked to bake.\n'
                     'Do you want to bake ALL rigs in the scene instead?'),
            button=['Yes, bake all', 'Cancel'],
            defaultButton='Yes, bake all', cancelButton='Cancel', dismissString='Cancel'
        )
        if res != 'Yes, bake all':
            cmds.warning('Bake canceled (no rigs marked).')
            return

    # Confirm destructive Time Editor wipe
    if wipe_te:
        res = cmds.confirmDialog(
            title='Delete Time Editor?',
            message='This will delete ALL Time Editor clips/sources/tracks.\nProceed?',
            button=['Yes, delete', 'Cancel'],
            defaultButton='Yes, delete', cancelButton='Cancel', dismissString='Cancel'
        )
        if res != 'Yes, delete':
            wipe_te = False

    # Compute range
    try:
        if method == 1:  # From Cameras
            def normalize(l): return l.replace('  (HT)', '')
            first_label = cmds.optionMenu(_UI['om_first'], q=True, v=True)
            last_label  = cmds.optionMenu(_UI['om_last'],  q=True, v=True)
            try:
                start_frame = int(get_start_frame_from_layout_cam(camera=normalize(first_label), pre_frame=pre))
                end_frame   = int(get_end_frame_from_layout_cam(camera=normalize(last_label),  post_frame=post))
            except Exception as e:
                if not allow_fallback:
                    cmds.warning(f'Failed to read headIn/tailOut: {e}')
                    return
                min_t = int(cmds.playbackOptions(q=True, min=True))
                max_t = int(cmds.playbackOptions(q=True, max=True))
                start_frame = min_t - int(pre); end_frame = max_t + int(post)
                cmds.warning(f'[WARN] No cameras with headIn/tailOut; using timeline {start_frame}..{end_frame}')
        elif method == 2:  # Timeline
            min_t = int(cmds.playbackOptions(q=True, min=True))
            max_t = int(cmds.playbackOptions(q=True, max=True))
            start_frame = min_t - int(pre); end_frame = max_t + int(post)
        else:  # Custom
            start_frame = int(cmds.intField(_UI['if_start'], q=True, v=True)) - int(pre)
            end_frame   = int(cmds.intField(_UI['if_end'],   q=True, v=True)) + int(post)
    except Exception as e:
        cmds.warning(f'Could not compute range: {e}')
        return

    start_frame, end_frame = _validate_range(start_frame, end_frame)

    # Optional destructive cleanup
    if wipe_te:
        delete_time_editor_content()

    # Mute broken expressions
    muted = []
    if mute_expr:
        muted = mute_broken_expressions()

    # Set playback and bake
    cmds.currentTime(start_frame)
    cmds.playbackOptions(minTime=start_frame, maxTime=end_frame,
                         animationStartTime=start_frame, animationEndTime=end_frame)

    if bake_namespaces:
        # Mark immediately any removed due to Skip-Baked option
        if skip_baked:
            pre = list(bake_namespaces)
            bake_namespaces = [ns for ns in bake_namespaces if _read_bind_driver(ns) != 1]
            for ns in pre:
                if ns not in bake_namespaces:
                    _status_set(ns, 'skipped')
        bake_selected_actors_in_range_with_progress(bake_namespaces, start_frame, end_frame)
    else:
        all_actors = scene_actors.get_all_actors_in_scene() or []
        ns_list = [a['namespace'] for a in all_actors if a.get('namespace')]
        bake_selected_actors_in_range_with_progress(ns_list, start_frame, end_frame)

    if restore_expr and muted:
        restore_muted_expressions()

    cmds.inViewMessage(amg=f'<hl>Bake completed</hl>: {start_frame}..{end_frame}', pos='midCenter', fade=True)

    _populate_list()  # ensure final statuses show
    _update_counts()

# ============================================================
# UI build / teardown
# ============================================================


def _make_btn(label, c, icon_patterns=None, icon_filename=None, **kwargs):
    """
    Create an iconTextButton.
    - If icon_filename is provided: use it (strict). If missing -> raise RuntimeError.
    - Else if icon_patterns provided: try to resolve via resourceManager; if found use iconTextButton; else fall back to plain button.
    """
    if icon_filename:
        try:
            found = cmds.resourceManager(nameFilter=icon_filename) or []
        except Exception:
            found = []
        # Prefer exact filename match
        exact = [h for h in found if h.endswith(icon_filename)]
        if exact:
            icon = exact[0]
            return cmds.iconTextButton(style='iconAndTextHorizontal', image1=icon, label=label, c=c, **kwargs)
        # If not directly resolvable, try to use the filename path as-is (for bundled icons)
        if os.path.isabs(icon_filename) or os.path.exists(icon_filename):
            return cmds.iconTextButton(style='iconAndTextHorizontal', image1=icon_filename, label=label, c=c, **kwargs)
        raise RuntimeError(f"Icon not found: {icon_filename} for button '{label}'")
    # Best-effort (legacy) pattern mode
    icon = None
    if icon_patterns:
        for patt in icon_patterns:
            try:
                found = cmds.resourceManager(nameFilter=patt) or []
            except Exception:
                found = []
            if found:
                icon = found[0]
                break
    if icon:
        return cmds.iconTextButton(style='iconAndTextHorizontal', image1=icon, label=label, c=c, **kwargs)
    else:
        return cmds.button(label=label, c=c, **kwargs)

def _build_status_row(parent):
    """
    Single-line status bar:
    Status: Selected X    To Bake Y    Total Z    🟢 Baked: n   🟡 Skipped: n   🔴 Failed: n   🔵 Canceled: n   ⚪ Not baked: n
    """
    row = cmds.rowLayout(nc=16, parent=parent)

    # Left summary block (natural width, so right block starts right after "Total")
    _UI['status_base'] = cmds.text(l='Status:   Selected: —    To Bake: —    Total: —', al='left')

    # compact spacers
    def sp(w=8): return cmds.separator(style='none', w=w)

    sp(12)
    _UI['lab_baked']   = cmds.text(l='🟢 Baked:',   al='right')
    _UI['cnt_baked']   = cmds.text(l='0',          al='left')
    sp()
    _UI['lab_skipped'] = cmds.text(l='🟡 Skipped:', al='right')
    _UI['cnt_skipped'] = cmds.text(l='0',          al='left')
    sp()
    _UI['lab_failed']  = cmds.text(l='🔴 Failed:',  al='right')
    _UI['cnt_failed']  = cmds.text(l='0',          al='left')
    sp()
    _UI['lab_canceled']= cmds.text(l='🔵 Canceled:',al='right')
    _UI['cnt_canceled']= cmds.text(l='0',          al='left')
    sp()
    _UI['lab_not']     = cmds.text(l='⚪ Not baked:', al='right')
    _UI['cnt_not']     = cmds.text(l='0',             al='left')

    cmds.setParent('..')

def open_bake_ui():
    """Build and show the UI."""
    # If an old window exists, restore hotkeys and delete it safely.
    if cmds.window('BakeSeqRangeUI', exists=True):
        try:
            _unregister_hotkeys()
        except Exception:
            pass
        try:
            cmds.deleteUI('BakeSeqRangeUI', window=True)
        except Exception:
            pass

    win = cmds.window('BakeSeqRangeUI', title='BURRITO — Baking Uniform Rig Retarget Interface Transfer Oven', sizeable=True,
                      closeCommand=_unregister_hotkeys)
    _UI.clear()

    cl = cmds.columnLayout(adj=True, rs=6, cat=('both', 8), parent=win)

    # --- Range Method ---
    _UI['rbg_method'] = cmds.radioButtonGrp(
        label='Range source:',
        labelArray3=['From Cameras (headIn/tailOut)', 'Timeline', 'Custom'],
        numberOfRadioButtons=3, sl=1, cw4=[100, 230, 110, 110],
        cc=_on_method_changed,
        ann=("Choose how the bake range will be defined:\n"
             "• From Cameras: uses headIn of the FIRST camera and tailOut of the LAST camera you select.\n"
             "• Timeline: uses the current Time Slider range (playbackOptions min/max).\n"
             "• Custom: enter start/end frames manually.")
    )

    # --- Cameras for head/tail ---
    cmds.frameLayout(label='Cameras for head/tail', collapsable=True, collapse=False, mw=6, mh=6,
                     ann=("Pick which camera defines the START (headIn) and which defines the END (tailOut).\n"
                          "Only *_ANIM camera transforms are listed. Items marked with (HT) expose headIn/tailOut."))
    _UI['om_first'] = cmds.optionMenu(label='First (headIn):',
                                      cc=_update_preview_label,
                                      ann=("Camera whose headIn defines the START of the bake.\n"
                                           "Only *_ANIM camera transforms are listed.\n"
                                           "Entries with (HT) have headIn/tailOut attributes available."))
    _UI['om_last']  = cmds.optionMenu(label='Last (tailOut):',
                                      cc=_update_preview_label,
                                      ann=("Camera whose tailOut defines the END of the bake.\n"
                                           "Only *_ANIM camera transforms are listed.\n"
                                           "Entries with (HT) have headIn/tailOut attributes available."))
    cmds.rowLayout(nc=2, cw2=(180, 160))
    _make_btn('Refresh Cameras', _ui_refresh_cameras, icon_patterns=['*refresh*', '*reload*', '*update*'], h=BTN_H,
              ann=("Rebuild the camera lists, scanning ONLY *_ANIM camera transforms. If possible, auto-selects:\n"
                   "• First = camera with the smallest headIn\n"
                   "• Last  = camera with the largest tailOut"))
    cmds.setParent('..'); cmds.setParent('..')  # rowLayout, frameLayout

    # --- Rigs to Bake (single list) ---
    rigs_frame = cmds.frameLayout(label='Rigs to Bake', collapsable=True, collapse=False, mw=6, mh=6,
                                  ann=("Single list of rigs. Mark rows with [x] to include them in the bake."))
    rigs_col = cmds.columnLayout(adj=True, parent=rigs_frame)

    # Filter row 1
    row1 = cmds.rowLayout(nc=2, adjustableColumn=2, cw2=(60, 520), parent=rigs_col)
    cmds.text(label='Filter:', ann="Label for filter field.")
    _UI['tf_filter'] = cmds.textField(tx='', cc=lambda *_: _populate_list(),
                                      ann=("Filter rigs by substring (namespace/actor name). Press Enter to apply."))
    cmds.setParent('..')

    # Filter row 2
    row2 = cmds.rowLayout(nc=2, adjustableColumn=1, cw2=(420, 200), parent=rigs_col)
    _UI['cb_hide_baked'] = cmds.checkBox(
        label="Hide rigs marked as 'Bind Driver ON' (already baked)",
        v=False, cc=lambda *_: _populate_list(),
        ann=("Hide rigs that appear already baked/driven.\n"
             "Detected via <ns>:M_GlobalSwitch01_CTRL.bind_skeleton_driver == 1.")
    )
    _UI['cb_show_only_marked'] = cmds.checkBox(
        label='Show only "To Bake"',
        v=False, cc=lambda *_: _populate_list(),
        ann=("When ON, the list shows only rigs currently marked with [x].")
    )
    cmds.setParent('..')

    # Single list
    _UI['tsl_rigs'] = cmds.textScrollList(
        allowMultiSelection=True, h=280,
        sc=_update_counts,
        ann=("Single list of rigs in the scene. Each row is prefixed by [x] (to bake) or [ ] (not to bake).\n"
             "Use Ctrl/Shift for multi-selection.")
    )

    # Selection row (aligned grid)
    row_sel = cmds.rowLayout(nc=5, cw5=(90, 140, 140, 140, 230), parent=rigs_col)
    cmds.text(l='Selection:', al='left')
    _make_btn('Select All',         _select_all,        icon_patterns=['*selectAll*','*select*'], w=140, h=BTN_H)
    _make_btn('Select None', _select_none, icon_filename='clearAll.png', w=140, h=BTN_H)
    _make_btn('Invert',             _select_invert,     icon_patterns=['*invert*','*swap*'],      w=140, h=BTN_H)
    _make_btn('From Maya Selection',_select_from_maya,  icon_patterns=['*find*','*search*'],      w=230, h=BTN_H)
    cmds.setParent('..')

    # Marking row (aligned grid)
    row_mark = cmds.rowLayout(nc=5, cw5=(90, 140, 140, 140, 230), parent=rigs_col)
    cmds.text(l='Marking:', al='left')
    _make_btn('Mark selected', _mark_selected, icon_filename='checkboxOn.png', w=140, h=BTN_H)
    _make_btn('Unmark selected', _unmark_selected, icon_filename='checkboxOff.png', w=140, h=BTN_H)
    _make_btn('Toggle selected', _toggle_selected, icon_filename='cycle.png', w=140, h=BTN_H)
    _make_btn('Clear all marks', _clear_all_marks, icon_filename='trash.png', w=230, h=BTN_H)
    cmds.setParent('..')

    # Scene row (aligned grid)
    row_scene = cmds.rowLayout(nc=5, cw5=(90, 140, 140, 140, 230), parent=rigs_col)
    cmds.text(l='Scene:', al='left')
    _make_btn('Select in Scene', _select_in_scene, icon_filename='outliner.png', w=140, h=BTN_H)
    cmds.separator(style='none', w=140)  # pad column 3
    cmds.separator(style='none', w=140)  # pad column 4
    cmds.separator(style='none', w=230)  # pad column 5
    cmds.setParent('..')

    # Single-line status bar
    _build_status_row(rigs_col)

    cmds.setParent('..'); cmds.setParent('..')  # rigs_col, frameLayout

    # --- Padding (Pre/Post) ---
    cmds.frameLayout(label='Padding (Pre/Post Frames)', collapsable=True, collapse=False, mw=6, mh=6,
                     ann=("Optional frame padding before/after the range.\n"
                          "Useful for constraints/IK to settle at the start and to avoid truncation at the end."))
    _UI['cb_prepost_enable'] = cmds.checkBox(label='Enable Pre/Post Frames', v=True, cc=_on_toggle_prepost,
                                             ann=("When ON, the bake range will be expanded by the specified\n"
                                                  "Pre and Post frames. When OFF, no padding is applied and the fields are ignored."))
    cmds.rowLayout(nc=4, cw4=(120, 80, 120, 80))
    cmds.text(label='Pre frames:', ann="Frames added BEFORE the start frame (IK/constraints warm-up).")
    _UI['if_pre']  = cmds.intField(value=1, cc=_update_preview_label,
                                   ann="Frames added BEFORE the start frame (typical 1–5).")
    cmds.text(label='Post frames:', ann="Frames added AFTER the end frame (safe tail).")
    _UI['if_post'] = cmds.intField(value=1, cc=_update_preview_label,
                                   ann="Frames added AFTER the end frame (typical 1–5).")
    cmds.setParent('..'); cmds.setParent('..')  # rowLayout, frameLayout

    # --- Custom range ---
    cmds.frameLayout(label='Custom Range', collapsable=True, collapse=False, mw=6, mh=6,
                     ann=("Manual start/end frames. Only used when the Range source is set to Custom."))
    cmds.rowLayout(nc=4, cw4=(120, 100, 120, 100))
    cmds.text(label='Custom start:', ann="Manual start frame (applies only in Custom mode).")
    _UI['if_start'] = cmds.intField(value=1, en=False, cc=_update_preview_label,
                                    ann="Manual start frame (Custom mode).")
    cmds.text(label='Custom end:', ann="Manual end frame (applies only in Custom mode).")
    _UI['if_end']   = cmds.intField(value=120, en=False, cc=_update_preview_label,
                                    ann="Manual end frame (Custom mode).")
    cmds.setParent('..'); cmds.setParent('..')  # rowLayout, frameLayout

    # --- Options ---
    cmds.frameLayout(label='Options', collapsable=True, collapse=False, mw=6, mh=6)
    _UI['cb_mute'] = cmds.checkBox(label='Mute broken expressions during bake', v=True,
                                   ann=("Temporarily clears expression nodes that reference missing objects\n"
                                        "to prevent evaluation errors while moving time and baking.\n"
                                        "If these expressions drive animation you need, leave this OFF."))
    _UI['cb_restore'] = cmds.checkBox(label='Restore expressions after bake', v=False,
                                      ann=("If you muted expressions, restore their original text after the bake.\n"
                                           "Disable if you want to keep broken expressions silent."))
    _UI['cb_fallback'] = cmds.checkBox(label='Allow fallback to Timeline if head/tail missing', v=True,
                                       ann=("If reading headIn/tailOut fails in From Cameras mode, use the current\n"
                                            "Timeline range instead of aborting. Uncheck to force manual fix."))
    _UI['cb_timeeditor'] = cmds.checkBox(label='Delete ALL Time Editor clips/sources before bake', v=False,
                                         ann=("Destructive: deletes ALL Time Editor nodes (clips/sources/tracks)\n"
                                              "before baking. You will be asked to confirm."))
    _UI['cb_skip_baked'] = cmds.checkBox(
        label="Skip rigs marked as 'Bind Driver ON' when baking",
        v=True,
        ann=("When ON, rigs that look already baked (Bind Driver ON) are skipped even if marked.\n"
             "Turn OFF if you want to force-bake them anyway.")
    )
    cmds.setParent('..')  # frameLayout

    # --- Preview + actions ---
    _UI['txt_preview'] = cmds.text(label='Preview range: —',
                                   ann="Shows the bake range computed from your current selections and options.")
    cmds.rowLayout(nc=2, cw2=(160, 120))
    cmds.button(label='Run Bake', bgc=(0.40, 0.80, 0.40), h=BTN_H, c=_on_run_bake,
                ann="Execute the bake across the computed range using your settings.")
    cmds.button(label='Close', h=BTN_H, c=_unregister_hotkeys,
                ann="Close this window and restore temporary hotkeys.")
    cmds.setParent('..')  # rowLayout

    cmds.showWindow(win)

    # Init data & UI
    _refresh_rigs_cache()
    _populate_list()
    _on_toggle_prepost()
    _ui_refresh_cameras()        # auto-populate cameras on open (only *_ANIM)
    _on_method_changed()
    _register_hotkeys()


# Open the UI
open_bake_ui()
