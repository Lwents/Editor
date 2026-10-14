"""Translate using a user-controlled Gemini tab in a separate local Chrome profile."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.request import urlopen
from urllib.parse import urlsplit

from app.core import settings


class GeminiWebError(RuntimeError):
    pass


class GeminiWebRefusal(GeminiWebError):
    """A completed refusal must not be retried as a formatting error."""


def is_refusal(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).casefold()
    return any(phrase in normalized for phrase in (
        "ngoài mục đích mà tôi được tạo ra", "không được thiết kế để",
        "tôi không thể trợ giúp", "tôi không thể giúp",
        "nên không thể trợ giúp", "nên không thể hỗ trợ",
        "không được lập trình", "i am not programmed",
        "tôi không thể hỗ trợ", "i cannot help with", "i can’t help with",
        "not designed to", "outside my purpose",
        "chỉ có thể xử lý văn bản", "không thể đọc ảnh",
        "cannot view images", "unable to process images",
        "i cannot assist with", "i can’t assist with", "i can't assist with",
    ))


class GeminiWebTimeout(GeminiWebError):
    """Do not replay an expensive request automatically after its deadline."""


class GeminiWebVerificationRequired(GeminiWebError):
    """Google requires a person to verify the browser session."""


_conversation_scope = ContextVar('gemini_conversation_scope', default=None)


def profile_directory() -> Path:
    data_root = Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_DATA_HOME') or (Path.home() / '.local/share'))
    return data_root / 'LwentEditor' / 'GeminiWeb'


@contextmanager
def session_lock():
    """Serialize sends even if web and desktop backends share this session."""
    profile = profile_directory()
    profile.mkdir(parents=True, exist_ok=True)
    with (profile / 'translation.lock').open('a+b') as stream:
        stream.write(b'0')
        stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise GeminiWebError('Phiên Gemini đang dịch cho tác vụ khác. Đợi hoàn tất rồi thử lại.') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def parse_reply(text: str, request_id: str) -> dict | None:
    """Only accept a complete JSON reply for this exact request, never an old chat."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{', text):
        try:
            data, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('request_id') == request_id and isinstance(data.get('items'), list):
            return data
    return None


class GeminiBrowser:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='gemini-web')
        self._gate = asyncio.Lock()
        self._playwright = None
        self._browser = None
        self._page = None
        self._process = None
        self._hidden = False
        self._window_bounds = None
        self._active_conversation = None

    @contextmanager
    def conversation(self, scope=None):
        token = _conversation_scope.set(hashlib.sha256(str(scope or uuid.uuid4()).encode()).hexdigest())
        try:
            yield
        finally:
            _conversation_scope.reset(token)

    @staticmethod
    def _conversation_urls():
        try:
            data = json.loads((profile_directory() / 'conversations.json').read_text(encoding='utf-8'))
            return {key: url for key, url in data.items() if isinstance(key, str) and isinstance(url, str)
                    and urlsplit(url).scheme == 'https' and urlsplit(url).netloc == 'gemini.google.com'
                    and re.fullmatch(r'/app/[A-Za-z0-9_-]+', urlsplit(url).path)}
        except (OSError, ValueError, AttributeError):
            return {}

    def _remember_conversation(self, page, scope):
        if not scope or not re.fullmatch(r'https://gemini\.google\.com/app/[A-Za-z0-9_-]+', page.url):
            return
        urls = self._conversation_urls()
        if urls.get(scope) == page.url:
            return
        urls.pop(scope, None)
        urls[scope] = page.url
        urls = dict(list(urls.items())[-200:])
        path = profile_directory() / 'conversations.json'
        pending = path.with_suffix('.pending.json')
        pending.write_text(json.dumps(urls), encoding='utf-8')
        pending.replace(path)

    def _forget_failed_conversation(self, scope):
        """Resume a refused job in a fresh chat, retaining its disk checkpoints."""
        self._active_conversation = None
        if not scope:
            return
        urls = self._conversation_urls()
        if scope not in urls:
            return
        urls.pop(scope)
        path = profile_directory() / 'conversations.json'
        pending = path.with_suffix('.pending.json')
        pending.write_text(json.dumps(urls), encoding='utf-8')
        pending.replace(path)

    def _set_visible(self, visible):
        """Hide only native windows owned by this dedicated CDP browser."""
        if os.name != 'nt':
            if not visible:
                raise GeminiWebError('Ẩn cửa sổ Gemini hiện hỗ trợ Windows.')
            return
        import ctypes
        from ctypes import wintypes
        session = self._browser.new_browser_cdp_session()
        try:
            processes = session.send('SystemInfo.getProcessInfo')['processInfo']
            pid = next(int(p['id']) for p in processes if p['type'] == 'browser')
        finally:
            session.detach()
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        windows = []

        @callback_type
        def visit(handle, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(handle, name, len(name))
            if owner.value == pid and name.value == 'Chrome_WidgetWin_1':
                windows.append(handle)
            return True

        user32.EnumWindows(visit, 0)
        if not windows:
            raise GeminiWebError('Không tìm được cửa sổ Gemini để ẩn/hiện. Bấm Mở Gemini rồi thử lại.')
        # SW_HIDE suspends Chromium painting: Gemini's lazy UI can remain
        # half-loaded forever. Keep this dedicated window rendering outside
        # the entire virtual desktop, without activating it.
        page_session = self._page.context.new_cdp_session(self._page)
        try:
            window = page_session.send('Browser.getWindowForTarget')
            window_id, bounds = window['windowId'], window['bounds']
            if bounds.get('windowState') != 'normal':
                page_session.send('Browser.setWindowBounds', {'windowId': window_id,
                                  'bounds': {'windowState': 'normal'}})
                bounds = page_session.send('Browser.getWindowBounds', {'windowId': window_id})['bounds']
            left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
            width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
            on_screen = (bounds['left'] + bounds['width'] > left and bounds['left'] < left + width
                         and bounds['top'] + bounds['height'] > top and bounds['top'] < top + height)
            if not self._hidden and on_screen:
                self._window_bounds = {key: bounds[key] for key in ('left', 'top', 'width', 'height')}
            restored = self._window_bounds or {'left': left + 100, 'top': top + 100,
                                               'width': 1280, 'height': 900}
            destination = restored if visible else {**restored,
                'left': left - restored['width'] - 100, 'top': top - restored['height'] - 100}
            page_session.send('Browser.setWindowBounds', {'windowId': window_id, 'bounds': destination})
        finally:
            page_session.detach()
        for handle in windows:
            user32.ShowWindow(handle, 8)  # SW_SHOWNA, no focus stealing
        if any(not user32.IsWindowVisible(handle) for handle in windows):
            raise GeminiWebError('Không đổi được trạng thái cửa sổ Gemini. Thử mở lại Gemini.')
        self._hidden = not visible

    def _connect(self):
        from playwright.sync_api import sync_playwright
        if self._browser is not None and self._browser.is_connected():
            if self._page is not None and not self._page.is_closed():
                return self._page
            self._page = self._browser.contexts[0].new_page()
            self._active_conversation = None
            self._page.goto('https://gemini.google.com/app', wait_until='domcontentloaded', timeout=60000)
            return self._page
        candidates = [
            Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
            Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe',
            Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
        ]
        executable = next((str(p) for p in candidates if p.is_file()), None)
        executable = executable or shutil.which('google-chrome') or shutil.which('chromium')
        if not executable:
            raise GeminiWebError('Chưa tìm thấy Chrome hoặc Edge trên máy chạy backend.')
        # Browser cookies must live outside the HTTP-served video storage.
        profile = profile_directory()
        profile.mkdir(parents=True, exist_ok=True)
        # Reconnect after a backend restart without relaunching the same profile.
        port_file = profile / 'connection.json'
        port = None
        try:
            saved_port = int(json.loads(port_file.read_text())['port'])
            if 1024 <= saved_port <= 65535:
                with urlopen(f'http://127.0.0.1:{saved_port}/json/version', timeout=1) as response:
                    info = json.load(response)
                    if 'webSocketDebuggerUrl' in info:
                        port = saved_port
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if port is None:
            # Bind debugging locally and never attach to the default profile.
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            launch_options = {}
            if os.name == 'nt' and settings.gemini_web_background:
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 0
                launch_options['startupinfo'] = startup
            self._process = subprocess.Popen([
                executable, f'--user-data-dir={profile}', f'--remote-debugging-port={port}',
                '--remote-debugging-address=127.0.0.1', '--no-first-run', '--no-default-browser-check',
                '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
                '--disable-backgrounding-occluded-windows',
                '--disable-features=CalculateNativeWinOcclusion',
                'https://gemini.google.com/app',
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **launch_options)
        endpoint = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urlopen(endpoint + '/json/version', timeout=1):
                    break
            except OSError:
                time.sleep(.25)
        else:
            raise GeminiWebError('Không mở được phiên Gemini. Đóng cửa sổ Gemini riêng rồi thử lại.')
        port_file.write_text(json.dumps({'port': port}), encoding='utf-8')
        if self._playwright is not None:
            self._playwright.stop()
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(endpoint, timeout=30000)
        self._active_conversation = None
        context = self._browser.contexts[0]
        self._page = next((p for p in context.pages if 'gemini.google.com' in p.url), None) or context.new_page()
        if 'gemini.google.com' not in self._page.url:
            self._page.goto('https://gemini.google.com/app', wait_until='domcontentloaded', timeout=60000)
        return self._page

    @staticmethod
    def _editor(page):
        candidates = page.locator('rich-textarea [contenteditable="true"], .ql-editor[contenteditable="true"], [role="textbox"][contenteditable="true"], textarea')
        for i in range(candidates.count()):
            editor = candidates.nth(i)
            if editor.is_visible() and editor.is_enabled():
                return editor
        return None

    def _status(self, open_browser=False):
        if open_browser:
            page = self._connect()
            self._set_visible(True)
            page.bring_to_front()
        page = self._page
        if page is None or page.is_closed():
            return {'ok': False, 'ready': False, 'message': 'Chrome đang đóng để tiết kiệm RAM. Khi dịch, app tự mở lại phiên đã lưu. Bấm Mở Gemini nếu cần kiểm tra đăng nhập.'}
        verification = self._verification_message(page)
        if verification:
            return {'ok': False, 'ready': False, 'needs_verification': True, 'message': verification}
        # The anonymous page can also display an editor; check sign-in controls.
        sign_in = page.locator('a[href*="accounts.google.com/ServiceLogin"], a[href*="accounts.google.com/InteractiveLogin"]')
        login_controls = page.get_by_role('link', name=re.compile(r'^(Sign in|Đăng nhập)$', re.I))
        needs_login = ('accounts.google.com' in page.url
                       or any(sign_in.nth(i).is_visible() for i in range(sign_in.count()))
                       or any(login_controls.nth(i).is_visible() for i in range(login_controls.count())))
        ready = not needs_login and self._editor(page) is not None and page.url.startswith('https://gemini.google.com/')
        return {'ok': ready, 'ready': ready, 'needs_login': needs_login,
                'message': 'Gemini sẵn sàng. Model được chọn trực tiếp trong cửa sổ Gemini.' if ready else 'Đăng nhập Google và hoàn tất màn hình chào trong cửa sổ Gemini, rồi bấm Kiểm tra phiên.'}

    @staticmethod
    def _verification_message(page):
        text = page.locator('body').inner_text(timeout=5000)[:3000].lower()
        if ('/sorry/' in page.url or 'our systems have detected unusual traffic' in text
                or 'hệ thống của chúng tôi đã phát hiện lưu lượng truy cập bất thường' in text):
            return ('Google yêu cầu xác minh người dùng (CAPTCHA/lưu lượng bất thường). '
                    'Hoàn tất xác minh trong cửa sổ Gemini rồi chạy tiếp; OCR và checkpoint đã được giữ.')
        return None

    def _wait_ready(self, page, cancelled, seconds=20):
        """A reopened signed-in tab needs time to render its editor."""
        deadline = time.monotonic() + seconds
        while True:
            if cancelled.is_set():
                raise GeminiWebError('Đã hủy chờ Gemini.')
            status = self._status()
            if status.get('needs_verification'):
                self._set_visible(True)
                page.bring_to_front()
                raise GeminiWebVerificationRequired(status['message'])
            if status.get('ready') or status.get('needs_login') or time.monotonic() >= deadline:
                return bool(status.get('ready'))
            page.wait_for_timeout(250)

    def _complete(self, messages, timeout, cancelled, background=None, attachments=None, conversation=None, on_wait=None):
        with session_lock():
            return self._complete_unlocked(messages, timeout, cancelled, background, attachments, conversation, on_wait)

    def _complete_unlocked(self, messages, timeout, cancelled, background=None, attachments=None, conversation=None, on_wait=None):
        page = self._connect()
        self._set_visible(not (settings.gemini_web_background if background is None else background))
        if not self._wait_ready(page, cancelled):
            self._set_visible(True)
            page.bring_to_front()
            raise GeminiWebError('Gemini chưa sẵn sàng. Mở Cấu hình AI → Mở Gemini và đăng nhập trước.')
        destination = self._conversation_urls().get(conversation) if conversation else None
        if (not conversation or self._active_conversation != conversation
                or (destination and page.url != destination)):
            page.goto(destination or 'https://gemini.google.com/app', wait_until='domcontentloaded', timeout=60000)
            self._active_conversation = conversation
        if not self._wait_ready(page, cancelled):
            raise GeminiWebError('Gemini chưa tải xong ô nhập hoặc cần đăng nhập. Mở Gemini rồi thử lại.')
        editor = self._editor(page)
        if editor is None:
            raise GeminiWebError('Không tìm thấy ô nhập Gemini. Kiểm tra đăng nhập hoặc giao diện Gemini.')
        request_id = uuid.uuid4().hex
        prompt = '\n\n'.join(str(message.get('content', '')) for message in messages)
        prompt += ('\n\nChỉ trả JSON, không Markdown. Bổ sung trường request_id chính xác: '
                   + json.dumps(request_id) + '. Mỗi id trong current_items phải có text không rỗng. '
                   'Làm đúng nhiệm vụ đã yêu cầu; không thay items bằng lời giải thích, không gộp hoặc đổi id. Ví dụ cấu trúc: '
                   '{"request_id":"' + request_id + '","items":[{"id":1,"text":"kết quả"}],"memory_updates":{}}')
        upload_started = time.monotonic()
        if attachments:
            self._upload_images(page, attachments)
            print(f'[Gemini Web] Attached {len(attachments)} image(s) in {time.monotonic() - upload_started:.1f}s', flush=True)
        responses = page.locator('model-response')
        editor.fill(prompt, timeout=15000)
        # Enter is the normal submit action, Shift+Enter adds a newline.
        editor.press('Enter')
        self._remember_conversation(page, conversation)
        response_started = time.monotonic()
        deadline = response_started + min(max(timeout, 30), 300)
        previous = None
        stable_since = 0.
        invalid_reply = None
        invalid_since = 0.
        next_verification_check = 0.
        next_wait_update = response_started + 15
        while time.monotonic() < deadline:
            if cancelled.is_set():
                self._stop_generation(page)
                raise GeminiWebError('Đã hủy chờ Gemini.')
            if on_wait and time.monotonic() >= next_wait_update:
                on_wait(int(time.monotonic() - response_started))
                next_wait_update = time.monotonic() + 15
            if page.is_closed():
                raise GeminiWebError('Cửa sổ Gemini đã đóng. Mở lại và chạy tiếp từ checkpoint.')
            if time.monotonic() >= next_verification_check:
                next_verification_check = time.monotonic() + 2
                self._remember_conversation(page, conversation)
                verification = self._verification_message(page)
                if verification:
                    self._set_visible(True)
                    page.bring_to_front()
                    raise GeminiWebVerificationRequired(verification)
            # Gemini can reuse the same response element between chats. The
            # request ID, not a growing DOM count, identifies the new answer.
            if responses.count():
                reply = responses.last.inner_text()
                data = parse_reply(reply, request_id)
                stop = page.locator('button[aria-label*="Stop"], button[aria-label*="Dừng"], button[aria-label*="Ngừng"], button.stop-button')
                generating = any(stop.nth(i).is_visible() for i in range(stop.count()))
                if data is not None:
                    normalized = json.dumps(data, ensure_ascii=False)
                    if normalized != previous:
                        previous, stable_since = normalized, time.monotonic()
                    stable_seconds = time.monotonic() - stable_since
                    if (not generating and stable_seconds >= 2) or stable_seconds >= 5:
                        # A complete request-matched JSON object is usable even
                        # if Gemini keeps generating trailing prose or its busy
                        # indicator persists. Never stop an incomplete JSON.
                        if generating:
                            self._stop_generation(page)
                            final_data = parse_reply(responses.last.inner_text(), request_id)
                            if final_data != data:
                                previous = None
                                continue
                        self._remember_conversation(page, conversation)
                        print(f'[Gemini Web] Valid reply in {time.monotonic() - response_started:.1f}s', flush=True)
                        return normalized
                elif reply.strip() and not generating and responses.last.evaluate(
                    '(element, requestId) => { const queries = document.querySelectorAll("user-query"); '
                    'const query = queries[queries.length - 1]; return !!query '
                    '&& query.textContent.includes(requestId) '
                    '&& !!(query.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING); }',
                    request_id):
                    if reply != invalid_reply:
                        invalid_reply, invalid_since = reply, time.monotonic()
                    elif time.monotonic() - invalid_since >= 3:
                        excerpt = re.sub(r'\s+', ' ', reply).strip()[:240]
                        if is_refusal(reply):
                            self._forget_failed_conversation(conversation)
                            raise GeminiWebRefusal('Gemini từ chối yêu cầu. Đã dừng gửi lại và giữ checkpoint. Phản hồi: ' + excerpt)
                        raise GeminiWebError('Gemini đã trả lời nhưng không đúng JSON: ' + excerpt)
                else:
                    invalid_reply, invalid_since = None, 0.
            page.wait_for_timeout(500)
        # A retry must not leave the previous generation running in the tab.
        self._stop_generation(page)
        self._forget_failed_conversation(conversation)
        raise GeminiWebTimeout(f'Quá thời gian chờ Gemini ({min(max(timeout, 30), 300)} giây). '
                               'Đã dừng gửi lại và giữ checkpoint. Bạn có thể chạy tiếp khi Gemini ổn định.')

    @staticmethod
    def _stop_generation(page):
        stop = page.locator('button[aria-label*="Stop"], button[aria-label*="Dừng"], button[aria-label*="Ngừng"], button.stop-button')
        for i in range(stop.count()):
            if stop.nth(i).is_visible():
                stop.nth(i).click(timeout=5000)
                break

    @staticmethod
    def _upload_images(page, attachments):
        files = [str(Path(path).resolve()) for path in attachments]
        if not files or any(not Path(path).is_file() for path in files):
            raise GeminiWebError('Thiếu ảnh kiểm tra phụ đề để gửi Gemini.')
        previous_previews = page.locator('.file-preview-container img').evaluate_all(
            'images => images.map(image => image.src)')
        uploader = page.get_by_role('button', name=re.compile(r'Upload files|Add files|Nội dung tải lên', re.I))
        # Some Gemini layouts open a transient input/native file chooser;
        # others show an upload menu. Intercept before clicking so a hidden
        # Windows file dialog cannot block a background translation.
        choosers = []
        on_chooser = lambda chooser: choosers.append(chooser)
        page.on('filechooser', on_chooser)
        try:
            inputs = page.locator('input[type="file"][accept*="image"]')
            chosen = bool(inputs.count())
            if chosen:
                inputs.first.set_input_files(files)
            else:
                uploader.click(timeout=15000)
            deadline = time.monotonic() + 20
            menu_clicked = False
            reopened = False
            while not chosen and time.monotonic() < deadline:
                if choosers:
                    choosers[0].set_files(files)
                    chosen = True
                    break
                inputs = page.locator('input[type="file"][accept*="image"]')
                if inputs.count():
                    inputs.first.set_input_files(files)
                    chosen = True
                    break
                menu = page.get_by_role('menuitem', name=re.compile(r'Upload files|Tải tệp|Tải lên', re.I))
                if not menu.count():
                    menu = page.get_by_role('button', name=re.compile(r'^Upload files|^Tải tệp lên', re.I))
                if not menu_clicked and menu.count() and menu.first.is_visible():
                    menu.first.click(timeout=5000)
                    menu_clicked = True
                elif not reopened and time.monotonic() > deadline - 10 and not menu.count():
                    # The initial composer can be replaced while Gemini hydrates.
                    # Reopen once if the first click did not leave an upload menu.
                    uploader.click(timeout=5000)
                    reopened = True
                page.wait_for_timeout(100)
            if not chosen:
                raise GeminiWebError('Không mở được bộ chọn ảnh Gemini. Mở Gemini và thử lại.')
        finally:
            page.remove_listener('filechooser', on_chooser)
        page.keyboard.press('Escape')
        # Gemini only creates filename tooltips on hover; use loaded previews.
        page.wait_for_function("data => Array.from(document.querySelectorAll('.file-preview-container img')).filter(img => img.complete && img.naturalWidth > 0 && !data.previous.includes(img.src)).length >= data.count", arg={'count': len(files), 'previous': previous_previews}, timeout=60000)
        # A local thumbnail can be ready while Gemini is still processing the
        # upload. The send button is already enabled in this state, so neither
        # img.complete nor a fixed delay is sufficient to submit safely.
        page.wait_for_function("() => Array.from(document.querySelectorAll('.file-preview-container')).every(container => !Array.from(container.querySelectorAll('mat-progress-spinner, [role=progressbar]')).some(spinner => spinner.getClientRects().length > 0))", timeout=60000)
        page.wait_for_timeout(1500)

    async def _run(self, method, *args):
        if self._gate.locked():
            raise GeminiWebError('Gemini đang xử lý một yêu cầu khác. Đợi xong rồi thử lại.')
        async with self._gate:
            return await asyncio.get_running_loop().run_in_executor(self._executor, method, *args)

    def _close(self):
        """Exit only the browser connected to our dedicated profile; retain login."""
        with session_lock():
            connected = self._browser is not None and self._browser.is_connected()
            if connected:
                session = self._browser.new_browser_cdp_session()
                try:
                    session.send('Browser.close')
                except Exception:
                    # Chrome can disconnect before acknowledging Browser.close.
                    if self._browser.is_connected():
                        raise
                (profile_directory() / 'connection.json').unlink(missing_ok=True)
            if self._playwright is not None:
                self._playwright.stop()
            self._browser = self._page = self._playwright = None
            self._hidden = False
            self._active_conversation = None
            if self._process is not None:
                self._process.wait(timeout=10)
                self._process = None
            return {'closed': True}

    async def close(self):
        return await self._run(self._close)

    async def open(self):
        return await self._run(self._status, True)

    async def status(self):
        return await self._run(self._status)

    async def complete(self, messages, timeout=180, background=None, attachments=None, on_wait=None):
        cancelled = Event()
        try:
            return await self._run(self._complete, messages, timeout, cancelled, background, attachments,
                                   _conversation_scope.get(), on_wait)
        finally:
            cancelled.set()


browser = GeminiBrowser()


class GeminiWebClient:
    """Reuse the same ID validation, timing, memory and checkpoint pipeline."""
    def __init__(self):
        self.chat = SimpleNamespace(completions=self)

    async def create(self, *, messages, timeout=180, **kwargs):
        content = await browser.complete(messages, timeout)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
