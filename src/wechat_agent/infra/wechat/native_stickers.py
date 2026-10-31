"""Native sticker UI adapter. All coordinates are derived from live controls."""
import time
import win32gui
import win32process
import uiautomation as auto
import json
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[4] / 'data' / 'native-stickers.json'

def load_registry():
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text('utf-8'))
    return {'count':0,'items':{}}

def dismiss_collection_error(gui):
    engine = gui._get_uia()
    if not engine.ensure_window():
        return False
    for hwnd, _, _ in visible_owned_windows(engine._win.NativeWindowHandle):
        controls = list(walk(auto.ControlFromHandle(hwnd)))
        if not any('表情添加失败' in (c.Name or '') for c in controls):
            continue
        button = next((c for c in controls if c.Name == '我知道了' and c.ControlTypeName == 'ButtonControl'), None)
        if button:
            button.Click()
            return True
    return False

def save_latest(gui, target, sticker_id):
    try:
        return _save_latest(gui, target, sticker_id)
    finally:
        dismiss_collection_error(gui)

def _save_latest(gui, target, sticker_id):
    state = load_registry()
    if sticker_id in state['items']:
        return True
    engine = gui._get_uia()
    if engine.current_chat() != target and not engine.open_chat(target):
        raise RuntimeError('Recipient unavailable')
    if not engine._right_click_latest_row(target):
        raise RuntimeError('Latest sticker unavailable')
    item = engine._uia_find_menu_item('添加到表情')
    if item is None or not engine._uia_click_menu_item(item):
        raise RuntimeError('Add-to-stickers menu unavailable')
    time.sleep(.6)
    if dismiss_collection_error(gui):
        raise RuntimeError('WeChat rejected this sticker collection')
    panel = open_favorites(gui)
    count = sum(c.ClassName == 'mmui::FavEmoticonItemView' for c in walk(panel))
    if count != state['count']+1:
        raise RuntimeError('Collection count did not increase; index unchanged')
    state['items']={name:index+1 for name,index in state['items'].items()}
    state['items'][sticker_id]=0
    state['count']=count
    REGISTRY.parent.mkdir(parents=True,exist_ok=True)
    REGISTRY.write_text(json.dumps(state),encoding='utf-8')
    auto.SendKeys('{Esc}')
    return True

def send_registered(gui,target,sticker_id):
    state=load_registry()
    if sticker_id not in state['items']:
        raise RuntimeError('Native sticker is not registered')
    return send_favorite(gui,target,state['items'][sticker_id],state['count'])


def visible_owned_windows(hwnd):
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    found = []
    def visit(h, _):
        if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] == pid:
            found.append((h, win32gui.GetClassName(h), win32gui.GetWindowRect(h)))
    win32gui.EnumWindows(visit, None)
    return found


def walk(control, depth=0):
    if depth > 40:
        return
    yield control
    for child in control.GetChildren():
        yield from walk(child, depth+1)


def inspect_panel(gui):
    engine = gui._get_uia()
    if not engine.ensure_window():
        raise RuntimeError('WeChat window unavailable')
    button = next((c for c in walk(engine._win) if c.Name == '发送表情(Alt+E)'), None)
    if button is None:
        raise RuntimeError('Sticker button unavailable')
    button.Click()
    time.sleep(.5)
    return visible_owned_windows(engine._win.NativeWindowHandle)


def open_favorites(gui):
    engine = gui._get_uia()
    if not engine.ensure_window():
        raise RuntimeError('WeChat unavailable')
    def popup():
        for hwnd, cls, _ in visible_owned_windows(engine._win.NativeWindowHandle):
            if 'ToolSaveBits' in cls:
                control = auto.ControlFromHandle(hwnd)
                if control.ClassName == 'mmui::EmoticonPopover':
                    return control
    panel = popup()
    if panel is None:
        inspect_panel(gui)
        panel = popup()
    if panel is None:
        raise RuntimeError('Native sticker panel unavailable')
    tab = next((c for c in walk(panel) if c.ControlTypeName == 'TabItemControl' and c.Name == '自定义表情'), None)
    if tab is None:
        raise RuntimeError('Favorites tab unavailable')
    tab.Click()
    time.sleep(.3)
    return panel


def send_favorite(gui, target, index, expected_count):
    engine = gui._get_uia()
    if engine.current_chat() != target and not engine.open_chat(target):
        raise RuntimeError('Wrong recipient')
    panel = open_favorites(gui)
    items = [c for c in walk(panel) if c.ClassName == 'mmui::FavEmoticonItemView']
    if len(items) != expected_count or not 0 <= index < len(items):
        raise RuntimeError('Sticker library changed; reindex required')
    item = items[index]
    button = next((c for c in walk(item) if c.ControlTypeName == 'ButtonControl'), None)
    if button is None:
        raise RuntimeError('Sticker item has no button')
    button.Click()
    return True
