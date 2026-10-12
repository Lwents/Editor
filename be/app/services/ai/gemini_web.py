"""Translate using a user-controlled Gemini tab in a separate local Chrome profile."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.request import urlopen

from app.core import settings


class GeminiWebError(RuntimeError):
    pass


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
        for handle in windows:
            user32.ShowWindow(handle, 8 if visible else 0)  # SW_SHOWNA / SW_HIDE
        if any(bool(user32.IsWindowVisible(handle)) != visible for handle in windows):
            raise GeminiWebError('Không đổi được trạng thái cửa sổ Gemini. Thử mở lại Gemini.')
        self._hidden = not visible

    def _connect(self):
        from playwright.sync_api import sync_playwright
        if self._browser is not None and self._browser.is_connected():
            if self._page is not None and not self._page.is_closed():
                return self._page
            self._page = self._browser.contexts[0].new_page()
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
        # The anonymous page can also display an editor; check sign-in controls.
        sign_in = page.locator('a[href*="accounts.google.com/ServiceLogin"], a[href*="accounts.google.com/InteractiveLogin"]')
        login_controls = page.get_by_role('link', name=re.compile(r'^(Sign in|Đăng nhập)$', re.I))
        needs_login = ('accounts.google.com' in page.url
                       or any(sign_in.nth(i).is_visible() for i in range(sign_in.count()))
                       or any(login_controls.nth(i).is_visible() for i in range(login_controls.count())))
        ready = not needs_login and self._editor(page) is not None and page.url.startswith('https://gemini.google.com/')
        return {'ok': ready, 'ready': ready, 'message': 'Gemini sẵn sàng. Model được chọn trực tiếp trong cửa sổ Gemini.' if ready else 'Đăng nhập Google và hoàn tất màn hình chào trong cửa sổ Gemini, rồi bấm Kiểm tra phiên.'}

    def _complete(self, messages, timeout, cancelled, background=None, attachments=None):
        with session_lock():
            return self._complete_unlocked(messages, timeout, cancelled, background, attachments)

    def _complete_unlocked(self, messages, timeout, cancelled, background=None, attachments=None):
        page = self._connect()
        if not self._status()['ready']:
            self._set_visible(True)
            page.bring_to_front()
            raise GeminiWebError('Gemini chưa sẵn sàng. Mở Cấu hình AI → Mở Gemini và đăng nhập trước.')
        self._set_visible(not (settings.gemini_web_background if background is None else background))
        page.goto('https://gemini.google.com/app', wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(1000)
        page.add_style_tag(content='*, *::before, *::after { animation: none !important; transition: none !important; }')
        editor = self._editor(page)
        if editor is None:
            raise GeminiWebError('Không tìm thấy ô nhập Gemini. Kiểm tra đăng nhập hoặc giao diện Gemini.')
        request_id = uuid.uuid4().hex
        prompt = '\n\n'.join(str(message.get('content', '')) for message in messages)
        prompt += ('\n\nChỉ trả JSON, không Markdown. Bổ sung trường request_id chính xác: '
                   + json.dumps(request_id) + '. Mỗi id trong current_items phải có text không rỗng. '
                   'Không trả bản tóm tắt; không gộp hoặc đổi id. Ví dụ cấu trúc: '
                   '{"request_id":"' + request_id + '","items":[{"id":1,"text":"bản dịch"}],"memory_updates":{}}')
        if attachments:
            self._upload_images(page, attachments)
        responses = page.locator('model-response')
        editor.fill(prompt, timeout=15000)
        # Enter is the normal submit action, Shift+Enter adds a newline.
        editor.press('Enter')
        deadline = time.monotonic() + min(max(timeout, 30), 300)
        previous = None
        stable_since = 0.
        while time.monotonic() < deadline:
            if cancelled.is_set():
                stop = page.locator('button[aria-label*="Stop"], button[aria-label*="Dừng"], button.stop-button')
                for i in range(stop.count()):
                    if stop.nth(i).is_visible():
                        stop.nth(i).click()
                        break
                raise GeminiWebError('Đã hủy chờ Gemini.')
            if page.is_closed():
                raise GeminiWebError('Cửa sổ Gemini đã đóng. Mở lại và chạy tiếp từ checkpoint.')
            # Gemini can reuse the same response element between chats. The
            # request ID, not a growing DOM count, identifies the new answer.
            if responses.count():
                reply = responses.last.inner_text()
                data = parse_reply(reply, request_id)
                if data is not None:
                    normalized = json.dumps(data, ensure_ascii=False)
                    if normalized != previous:
                        previous, stable_since = normalized, time.monotonic()
                    stop = page.locator('button[aria-label*="Stop"], button[aria-label*="Dừng"], button.stop-button')
                    generating = any(stop.nth(i).is_visible() for i in range(stop.count()))
                    if not generating and time.monotonic() - stable_since >= 2:
                        return normalized
            page.wait_for_timeout(500)
        raise GeminiWebError('Quá thời gian chờ hoặc Gemini trả sai định dạng. Kiểm tra cửa sổ Gemini/quota rồi chạy tiếp.')

    @staticmethod
    def _upload_images(page, attachments):
        files = [str(Path(path).resolve()) for path in attachments]
        if not files or any(not Path(path).is_file() for path in files):
            raise GeminiWebError('Thiếu ảnh kiểm tra phụ đề để gửi Gemini.')
        uploader = page.get_by_role('button', name=re.compile(r'Upload files|Add files|Nội dung tải lên', re.I))
        uploader.click(timeout=15000)
        image_input = page.locator('input[type="file"][accept*="image"]')
        image_input.first.wait_for(state='attached', timeout=15000)
        image_input.first.set_input_files(files)
        page.keyboard.press('Escape')
        # Gemini only creates filename tooltips on hover; use loaded previews.
        page.wait_for_function("count => Array.from(document.querySelectorAll('.file-preview-container img')).filter(img => img.complete && img.naturalWidth > 0).length >= count", arg=len(files), timeout=60000)
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

    async def complete(self, messages, timeout=180, background=None, attachments=None):
        cancelled = Event()
        try:
            return await self._run(self._complete, messages, timeout, cancelled, background, attachments)
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
