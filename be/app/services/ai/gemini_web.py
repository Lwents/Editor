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
from app.services.ai.gemini_models import active_model_policy, model_policy, select_model, ModelSelectionError


class GeminiWebError(RuntimeError):
    pass


class GeminiSessionBusy(GeminiWebError):
    pass


class WebConversationLimit(GeminiWebError):
    """The provider explicitly rejected this conversation's context length."""


class GeminiWebModelError(GeminiWebError):
    """A missing/mismatched model needs a user action, not prompt retries."""


class GeminiWebMalformedReply(GeminiWebError):
    def __init__(self, message, partial=None):
        super().__init__(message)
        self.partial = partial


def _escape_text_quotes(text: str) -> str:
    """Repair raw quotes only inside text values with an item boundary.

    Never add words, close truncated items, or change identifiers. The result
    still has to decode as JSON and match the exact request ID.
    """
    pattern = re.compile(r'("text"\s*:\s*")(.*?)("\s*}\s*(?=,\s*{\s*"id"\s*:|]\s*[,}]))', re.S)
    def replace(match):
        value, backslashes, result = match[2], 0, []
        for char in value:
            if char == '"' and backslashes % 2 == 0:
                result.append('\\')
            if ord(char) < 32:
                result.append(json.dumps(char)[1:-1])
            else:
                result.append(char)
            backslashes = backslashes + 1 if char == '\\' else 0
        return match[1] + ''.join(result) + match[3]
    return pattern.sub(replace, text)


def parse_partial_reply(text: str, request_id: str) -> dict | None:
    """Recover only fully decoded leading items under an exact request ID."""
    decoder = json.JSONDecoder()
    for start in re.finditer(r'\{', text):
        offset, nonce = start.end(), None
        try:
            while True:
                offset += len(text[offset:]) - len(text[offset:].lstrip())
                key, consumed = decoder.raw_decode(text[offset:])
                offset += consumed
                offset += len(text[offset:]) - len(text[offset:].lstrip())
                if text[offset:offset+1] != ':':
                    break
                offset += 1
                offset += len(text[offset:]) - len(text[offset:].lstrip())
                if key == 'items':
                    if nonce != request_id or text[offset:offset+1] != '[':
                        break
                    offset += 1
                    items = []
                    while True:
                        offset += len(text[offset:]) - len(text[offset:].lstrip())
                        try:
                            item, consumed = decoder.raw_decode(text[offset:])
                        except ValueError:
                            break
                        if not isinstance(item, dict):
                            break
                        items.append(item)
                        offset += consumed
                        offset += len(text[offset:]) - len(text[offset:].lstrip())
                        if text[offset:offset+1] != ',':
                            break
                        offset += 1
                    if items:
                        return {'request_id': request_id, 'items': items, 'memory_updates': {}}
                    break
                value, consumed = decoder.raw_decode(text[offset:])
                if key == 'request_id':
                    nonce = value
                offset += consumed
                offset += len(text[offset:]) - len(text[offset:].lstrip())
                if text[offset:offset+1] != ',':
                    break
                offset += 1
        except (ValueError, IndexError):
            pass
    return None


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
        "chỉ có thể tạo văn bản", "ngoài khả năng tôi được lập trình",
        "cannot view images", "unable to process images",
        "i cannot assist with", "i can’t assist with", "i can't assist with",
    ))


class GeminiWebTimeout(GeminiWebError):
    """Do not replay an expensive request automatically after its deadline."""


def response_wait_limits(timeout):
    """No-progress timeout plus a bounded allowance for an active stream."""
    idle = min(max(timeout, 30), 300)
    return idle, min(idle * 3, 900)


class GeminiWebVerificationRequired(GeminiWebError):
    """Google requires a person to verify the browser session."""


_conversation_scope = ContextVar('gemini_conversation_scope', default=None)
_wait_callback = ContextVar('gemini_wait_callback',default=None)


def profile_directory() -> Path:
    data_root = Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_DATA_HOME') or (Path.home() / '.local/share'))
    return data_root / 'LwentEditor' / 'GeminiWeb'


@contextmanager
def session_lock(profile=None):
    """Serialize sends even if web and desktop backends share this session."""
    profile = profile or profile_directory()
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
            raise GeminiSessionBusy('Phiên Gemini đang dịch cho tác vụ khác. Đang chờ đến lượt.') from None
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
            repaired = _escape_text_quotes(text[match.start():])
            if repaired == text[match.start():]:
                continue
            try:
                data, _ = decoder.raw_decode(repaired)
            except ValueError:
                continue
        if isinstance(data, dict) and data.get('request_id') == request_id and isinstance(data.get('items'), list):
            return data
    return None


class GeminiBrowser:
    provider_label = 'Gemini'
    home_url = 'https://gemini.google.com/app'
    host = 'gemini.google.com'
    chat_pattern = r'https://gemini\.google\.com/app/[A-Za-z0-9_-]+'
    response_selector = 'model-response'
    query_selector = 'user-query'
    stop_selector = 'button[aria-label*="Stop"], button[aria-label*="Dừng"], button[aria-label*="Ngừng"], button.stop-button'

    @staticmethod
    def profile_directory():
        return profile_directory()

    def _policy(self, mode):
        from .gemini_models import POLICIES
        from app.services.presets import get_processing_profile
        return POLICIES[get_processing_profile(mode).name]

    def _select_model(self, page, policy):
        return select_model(page, policy)

    def _before_send(self, page, prompt, conversation, cancelled, policy):
        pass

    def _on_sent(self, page, prompt, conversation):
        pass

    def _generation_activity(self, page, request_id):
        stop = page.locator(self.stop_selector)
        generating = any(stop.nth(i).is_visible() for i in range(stop.count()))
        queries = page.locator(self.query_selector)
        current = bool(generating and queries.count() and queries.last.evaluate(
            '(element, nonce) => element.textContent.includes(nonce)', request_id))
        return {'generating': generating, 'current_request': current}

    def _response_belongs(self, response, request_id):
        return response.evaluate('(element, data) => { const queries = document.querySelectorAll(data.selector); const query = queries[queries.length - 1]; return !!query && query.textContent.includes(data.id) && !!(query.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING); }', {'selector': self.query_selector, 'id': request_id})

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
        self._delivered_requests=set()

    @contextmanager
    def conversation(self, scope=None, processing_mode=None, on_wait=None):
        policy = self._policy(processing_mode)
        model_token = active_model_policy.set(policy)
        wait_token = _wait_callback.set(on_wait)
        token = _conversation_scope.set(hashlib.sha256(f'{scope or uuid.uuid4()}:{policy.cache_key}'.encode()).hexdigest())
        try:
            yield
        finally:
            _conversation_scope.reset(token)
            active_model_policy.reset(model_token)
            _wait_callback.reset(wait_token)

    def _conversation_urls(self):
        try:
            data = json.loads((self.profile_directory() / 'conversations.json').read_text(encoding='utf-8'))
            return {key: url for key, url in data.items() if isinstance(key, str) and isinstance(url, str)
                    and urlsplit(url).scheme == 'https' and urlsplit(url).netloc == self.host
                    and re.fullmatch(self.chat_pattern, url)}
        except (OSError, ValueError, AttributeError):
            return {}

    def _remember_conversation(self, page, scope):
        if not scope or not re.fullmatch(self.chat_pattern, page.url):
            return
        urls = self._conversation_urls()
        if urls.get(scope) == page.url:
            return
        urls.pop(scope, None)
        urls[scope] = page.url
        urls = dict(list(urls.items())[-200:])
        path = self.profile_directory() / 'conversations.json'
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
        path = self.profile_directory() / 'conversations.json'
        pending = path.with_suffix('.pending.json')
        pending.write_text(json.dumps(urls), encoding='utf-8')
        pending.replace(path)

    def _set_visible(self, visible):
        """Hide only native windows owned by this dedicated CDP browser."""
        if os.name != 'nt':
            if not visible:
                raise GeminiWebError(f'Ẩn cửa sổ {self.provider_label} hiện hỗ trợ Windows.')
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
            raise GeminiWebError(f'Không tìm được cửa sổ {self.provider_label} để ẩn/hiện. Bấm Mở {self.provider_label} rồi thử lại.')
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
            raise GeminiWebError(f'Không đổi được trạng thái cửa sổ {self.provider_label}. Thử mở lại {self.provider_label}.')
        self._hidden = not visible

    def _connect(self):
        from playwright.sync_api import sync_playwright
        if self._browser is not None and self._browser.is_connected():
            if self._page is not None and not self._page.is_closed():
                return self._page
            self._page = self._browser.contexts[0].new_page()
            self._active_conversation = None
            self._page.goto(self.home_url, wait_until='domcontentloaded', timeout=60000)
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
        profile = self.profile_directory()
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
                self.home_url,
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
            raise GeminiWebError(f'Không mở được phiên {self.provider_label}. Đóng cửa sổ {self.provider_label} riêng rồi thử lại.')
        port_file.write_text(json.dumps({'port': port}), encoding='utf-8')
        if self._playwright is not None:
            self._playwright.stop()
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(endpoint, timeout=30000)
        self._active_conversation = None
        context = self._browser.contexts[0]
        self._page = next((p for p in context.pages if urlsplit(p.url).hostname == self.host), None) or context.new_page()
        if urlsplit(self._page.url).hostname != self.host:
            self._page.goto(self.home_url, wait_until='domcontentloaded', timeout=60000)
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
                raise GeminiWebError(f'Đã hủy chờ {self.provider_label}.')
            status = self._status()
            if status.get('needs_verification'):
                self._set_visible(True)
                page.bring_to_front()
                raise GeminiWebVerificationRequired(status['message'])
            if status.get('ready') or status.get('needs_login') or time.monotonic() >= deadline:
                return bool(status.get('ready'))
            page.wait_for_timeout(250)

    def _complete(self, messages, timeout, cancelled, background=None, attachments=None, conversation=None, on_wait=None, policy=None):
        with session_lock(self.profile_directory()):
            return self._complete_unlocked(messages, timeout, cancelled, background, attachments, conversation, on_wait, policy)

    def _on_reply(self, page, request_id, conversation):
        """Provider hook for durable accounting after a matched reply."""

    def _complete_unlocked(self, messages, timeout, cancelled, background=None, attachments=None, conversation=None, on_wait=None, policy=None):
        from app.services.ai.request_journal import RequestJournal
        journal=RequestJournal(self.profile_directory(),conversation,messages,policy,attachments) if conversation and policy else None
        saved=journal.load() if journal else None
        delivery_key=(conversation,journal.key) if journal else None
        if saved and delivery_key not in self._delivered_requests and saved.get('status')=='received':
            response=saved.get('response')
            if isinstance(response,str) and parse_reply(response,saved.get('request_id')) is not None:
                self._delivered_requests.add(delivery_key)
                print(f'[{self.provider_label} Web] Recovered received reply after interruption; no resend.',flush=True)
                return response
        page = self._connect()
        self._set_visible(not (settings.gemini_web_background if background is None else background))
        if not self._wait_ready(page, cancelled):
            self._set_visible(True)
            page.bring_to_front()
            raise GeminiWebError(f'{self.provider_label} chưa sẵn sàng. Mở Cấu hình AI → Mở {self.provider_label} và đăng nhập trước.')
        destination = self._conversation_urls().get(conversation) if conversation else None
        if (not conversation or self._active_conversation != conversation
                or (destination and page.url != destination)):
            page.goto(destination or self.home_url, wait_until='domcontentloaded', timeout=60000)
            self._active_conversation = conversation
        if not self._wait_ready(page, cancelled):
            raise GeminiWebError(f'{self.provider_label} chưa tải xong ô nhập hoặc cần đăng nhập. Mở {self.provider_label} rồi thử lại.')
        if saved and delivery_key not in self._delivered_requests and saved.get('status') in {'prepared','sent'}:
            old_id=saved.get('request_id')
            if isinstance(old_id,str) and re.fullmatch(r'[a-f0-9]{32}',old_id):
                old_url=saved.get('url')
                if isinstance(old_url,str) and re.fullmatch(self.chat_pattern, old_url) and page.url!=old_url:
                    page.goto(old_url,wait_until='domcontentloaded',timeout=60000)
                    self._wait_ready(page,cancelled)
                end=time.monotonic()+20
                while time.monotonic()<end:
                    if cancelled.is_set():
                        raise GeminiWebError(f'Đã hủy khôi phục phản hồi {self.provider_label}.')
                    replies=page.locator(self.response_selector)
                    if replies.count():
                        recovered=parse_reply(replies.last.inner_text(),old_id)
                        if recovered is not None:
                            normalized=json.dumps(recovered,ensure_ascii=False)
                            self._on_reply(page, old_id, conversation)
                            journal.save('received',old_id,page.url,normalized)
                            self._delivered_requests.add(delivery_key)
                            return normalized
                    page.wait_for_timeout(500)
                self._stop_generation(page)
        if policy is not None:
            limit=settings.gemini_web_max_chat_turns
            if conversation and limit>0 and page.locator(self.query_selector).count()>=limit:
                # Every request already carries the locked character bible,
                # source neighbours and rolling memory. Rotate only the UI
                # history, preserving the old chat and all disk checkpoints.
                page.goto(self.home_url,wait_until='domcontentloaded',timeout=60000)
                if not self._wait_ready(page,cancelled):
                    raise GeminiWebError(f'{self.provider_label} chưa sẵn sàng sau khi chuyển phiên chat dài.')
                print(f'[{self.provider_label} Web] Rotated chat after {limit} turns; structured context retained.',flush=True)
            for selection_attempt in range(2):
                try:
                    chosen = self._select_model(page, policy)
                    break
                except ModelSelectionError as exc:
                    # After navigation Gemini can replace a menu mid-click.
                    # Retry only a DOM failure, before filling any prompt;
                    # explicit unavailable/mismatched choices remain errors.
                    if selection_attempt or exc.__cause__ is None:
                        raise GeminiWebModelError(str(exc)) from exc
                    if cancelled.is_set():
                        raise GeminiWebError(f'Đã hủy trước khi gửi {self.provider_label}.')
                    print(f'[{self.provider_label} Web] Model menu changed during navigation; checking once more.', flush=True)
                    page.wait_for_timeout(1000)
            print(f'[{self.provider_label} Web] Confirmed {chosen["model"]}, thinking={chosen["thinking"]}', flush=True)
        if cancelled.is_set():
            raise GeminiWebError(f'Đã hủy trước khi gửi {self.provider_label}.')
        editor = self._editor(page)
        if editor is None:
            raise GeminiWebError(f'Không tìm thấy ô nhập {self.provider_label}. Kiểm tra đăng nhập hoặc giao diện {self.provider_label}.')
        request_id = uuid.uuid4().hex
        prompt = '\n\n'.join(str(message.get('content', '')) for message in messages)
        prompt += ('\n\nChỉ trả JSON, không Markdown. Bổ sung trường request_id chính xác: '
                   + json.dumps(request_id) + '. Mỗi id trong current_items phải có text không rỗng. '
                   'Trong text, dùng dấu “…” khi trích dẫn để JSON không bị lỗi ngoặc kép. '
                   'Làm đúng nhiệm vụ đã yêu cầu; không thay items bằng lời giải thích, không gộp hoặc đổi id. Ví dụ cấu trúc: '
                   '{"request_id":"' + request_id + '","items":[{"id":1,"text":"kết quả"}],"memory_updates":{}}')
        self._before_send(page, prompt, conversation, cancelled, policy)
        editor = self._editor(page)
        if editor is None:
            raise GeminiWebError(f'Không tìm thấy ô nhập {self.provider_label} trước khi gửi.')
        upload_started = time.monotonic()
        if attachments:
            self._upload_images(page, attachments)
            print(f'[{self.provider_label} Web] Attached {len(attachments)} image(s) in {time.monotonic() - upload_started:.1f}s', flush=True)
        responses = page.locator(self.response_selector)
        if journal:
            journal.save('prepared',request_id,page.url)
        editor.fill(prompt, timeout=15000)
        # Enter is the normal submit action, Shift+Enter adds a newline.
        editor.press('Enter')
        self._on_sent(page, prompt, conversation)
        if journal:
            journal.save('sent',request_id,page.url)
        self._remember_conversation(page, conversation)
        response_started = time.monotonic()
        idle_timeout, hard_timeout = response_wait_limits(timeout)
        deadline = response_started + hard_timeout
        last_progress = response_started
        observed_reply = None
        observed_thought = None
        initial_generation_confirmed = False
        journal_url = page.url
        waiting_for_thought = False
        silence_limit = idle_timeout
        transport_refreshed = None
        transport_idle_since = response_started
        transport_stalled = False
        previous = None
        stable_since = 0.
        invalid_reply = None
        invalid_since = 0.
        next_verification_check = 0.
        next_wait_update = response_started + 15
        while time.monotonic() < deadline:
            waiting_for_thought = False
            belongs = False
            generating = False
            if cancelled.is_set():
                self._stop_generation(page)
                raise GeminiWebError(f'Đã hủy chờ {self.provider_label}.')
            if on_wait and time.monotonic() >= next_wait_update:
                on_wait(int(time.monotonic() - response_started))
                next_wait_update = time.monotonic() + 15
            if page.is_closed():
                raise GeminiWebError(f'Cửa sổ {self.provider_label} đã đóng. Mở lại và chạy tiếp từ checkpoint.')
            if time.monotonic() >= next_verification_check:
                next_verification_check = time.monotonic() + 2
                self._remember_conversation(page, conversation)
                if journal and page.url != journal_url:
                    # Gemini often assigns /app/<id> after submission. Retain
                    # that address before a crash or timeout, not only at success.
                    journal.save('sent', request_id, page.url)
                    journal_url = page.url
                verification = self._verification_message(page)
                if verification:
                    self._set_visible(True)
                    page.bring_to_front()
                    raise GeminiWebVerificationRequired(verification)
            activity = self._generation_activity(page, request_id)
            generating = activity.get('generating', False)
            initial_generation_confirmed = initial_generation_confirmed or activity.get('current_request', False)
            thought = activity.get('thinking')
            waiting_for_thought = thought is not None and generating
            if thought is not None and thought != observed_thought:
                observed_thought = thought
                last_progress = time.monotonic()
            # Gemini can reuse the same response element between chats. The
            # request ID, not a growing DOM count, identifies the new answer.
            if responses.count():
                reply = responses.last.inner_text()
                data = parse_reply(reply, request_id)
                belongs = data is not None or (bool(reply.strip()) and self._response_belongs(responses.last, request_id))
                if belongs and reply != observed_reply:
                    observed_reply = reply
                    last_progress = time.monotonic()
                waiting_for_thought = waiting_for_thought or bool(belongs and generating and not parse_partial_reply(reply, request_id))
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
                        self._on_reply(page, request_id, conversation)
                        if journal:
                            journal.save('received',request_id,page.url,normalized)
                            self._delivered_requests.add(delivery_key)
                            if len(self._delivered_requests)>2000:
                                self._delivered_requests={delivery_key}
                        print(f'[{self.provider_label} Web] Valid reply in {time.monotonic() - response_started:.1f}s', flush=True)
                        return normalized
                elif reply.strip() and not generating and belongs:
                    if reply != invalid_reply:
                        invalid_reply, invalid_since = reply, time.monotonic()
                    elif time.monotonic() - invalid_since >= (3 if is_refusal(reply) else 20):
                        excerpt = re.sub(r'\s+', ' ', reply).strip()[:240]
                        if is_refusal(reply):
                            self._forget_failed_conversation(conversation)
                            raise GeminiWebRefusal('Gemini từ chối yêu cầu. Đã dừng gửi lại và giữ checkpoint. Phản hồi: ' + excerpt)
                        raise GeminiWebMalformedReply('Gemini đã trả lời nhưng không đúng JSON; thu nhỏ nhóm cần dịch.',
                                                      partial=parse_partial_reply(reply, request_id))
                else:
                    invalid_reply, invalid_since = None, 0.
            # A Gemini SPA can display the submitted query but never start a
            # response (no current response and no Stop button). Reload the
            # same chat once to reconnect its transport before declaring timeout.
            if belongs or generating:
                transport_idle_since = time.monotonic()
            if not belongs and not generating and time.monotonic()-transport_idle_since >= 30:
                if transport_refreshed is None:
                    queries = page.locator(self.query_selector)
                    visible_request = queries.count() and queries.last.evaluate(
                        '(element, nonce) => element.textContent.includes(nonce)', request_id)
                    if visible_request:
                        page.reload(wait_until='domcontentloaded', timeout=60000)
                        if not self._wait_ready(page, cancelled):
                            raise GeminiWebError(f'{self.provider_label} chưa sẵn sàng sau khi kết nối lại; checkpoint được giữ.')
                        responses = page.locator(self.response_selector)
                        transport_refreshed = time.monotonic()
                        transport_idle_since = transport_refreshed
                        last_progress = transport_refreshed
                        previous = observed_reply = invalid_reply = None
                        print(f'[{self.provider_label} Web] Reconnected stalled chat once; waiting for the original request.', flush=True)
                        continue
                elif time.monotonic()-transport_refreshed >= 30:
                    transport_stalled = True
                    break
            # A confirmed current generation can spend its initial phase in
            # thought before emitting any complete item. Give that phase one
            # bounded extra window; stale answers and idle tabs get none.
            waiting_for_thought = waiting_for_thought or bool(
                initial_generation_confirmed and not parse_partial_reply(observed_reply or '', request_id))
            silence_limit = idle_timeout * (2 if waiting_for_thought else 1)
            if time.monotonic() - last_progress >= silence_limit:
                break
            page.wait_for_timeout(500)
        # A retry must not leave the previous generation running in the tab.
        self._stop_generation(page)
        self._remember_conversation(page, conversation)
        if journal:
            journal.save('sent', request_id, page.url)
        partial = parse_partial_reply(observed_reply or '', request_id)
        if partial and partial.get('items'):
            # Preserve complete, nonce-matched items. The caller validates their
            # IDs and retries only the missing cues; never invent a JSON tail.
            raise GeminiWebMalformedReply('Gemini dừng giữa câu trả lời; đã giữ các mục đầy đủ để xử lý phần còn thiếu.', partial=partial)
        reason = ('Gemini đã nhận yêu cầu nhưng không bắt đầu trả lời sau một lần kết nối lại'
                  if transport_stalled else f'Gemini không có nội dung mới trong {silence_limit} giây'
                  if time.monotonic()-last_progress >= silence_limit
                  else f'Gemini vượt giới hạn tổng {hard_timeout} giây')
        raise GeminiWebTimeout('Quá thời gian chờ Gemini: ' + reason + '. Đã giữ checkpoint và liên kết chat để khôi phục phản hồi đến muộn.')

    def _stop_generation(self, page):
        stop = page.locator(self.stop_selector)
        for i in range(stop.count()):
            if stop.nth(i).is_visible():
                stop.nth(i).click(timeout=5000)
                break

    @staticmethod
    def _upload_images(page, attachments):
        files = [str(Path(path).resolve()) for path in attachments]
        if not files or any(not Path(path).is_file() for path in files):
            raise GeminiWebError(f'Thiếu ảnh kiểm tra phụ đề để gửi {self.provider_label}.')
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
                raise GeminiWebError(f'Không mở được bộ chọn ảnh {self.provider_label}. Mở {self.provider_label} và thử lại.')
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
            raise GeminiWebError(f'{self.provider_label} đang xử lý một yêu cầu khác. Đợi xong rồi thử lại.')
        await self._gate.acquire()
        try:
            work = asyncio.get_running_loop().run_in_executor(self._executor, method, *args)
        except BaseException:
            self._gate.release()
            raise
        caller = asyncio.get_running_loop().create_future()

        def finished(future):
            # Cancelling the coroutine does not stop its Playwright thread.
            # The worker owns this gate until it actually exits; otherwise
            # cleanup can queue behind a live worker and hold the gate again.
            self._gate.release()
            if future.cancelled():
                if not caller.done():
                    caller.cancel()
                return
            error = future.exception()  # Observe late errors after cancellation.
            if not caller.done():
                if error is not None:
                    caller.set_exception(error)
                else:
                    caller.set_result(future.result())

        work.add_done_callback(finished)
        # Cancelling this relay does not cancel the thread's completion future.
        return await caller

    def _close(self):
        """Exit only the browser connected to our dedicated profile; retain login."""
        with session_lock(self.profile_directory()):
            connected = self._browser is not None and self._browser.is_connected()
            if connected:
                session = self._browser.new_browser_cdp_session()
                try:
                    session.send('Browser.close')
                except Exception:
                    # Chrome can disconnect before acknowledging Browser.close.
                    if self._browser.is_connected():
                        raise
                (self.profile_directory() / 'connection.json').unlink(missing_ok=True)
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
            queued=time.monotonic()
            callback=on_wait or _wait_callback.get()
            while True:
                try:
                    return await self._run(self._complete, messages, timeout, cancelled, background, attachments,
                                           _conversation_scope.get(), callback, active_model_policy.get())
                except GeminiSessionBusy:
                    if time.monotonic()-queued>=min(60,timeout):
                        raise GeminiWebTimeout(f'{self.provider_label.upper()}_SESSION_BUSY: Phiên đang phục vụ tác vụ khác; checkpoint được giữ.') from None
                    if callback:
                        callback(int(time.monotonic()-queued))
                    await asyncio.sleep(2)
        finally:
            cancelled.set()


browser = GeminiBrowser()


class GeminiWebClient:
    """Reuse the same ID validation, timing, memory and checkpoint pipeline."""
    def __init__(self, provider_browser=None):
        self.browser = provider_browser or browser
        self.chat = SimpleNamespace(completions=self)

    async def create(self, *, messages, timeout=180, **kwargs):
        try:
            content = await self.browser.complete(messages, timeout)
        except GeminiWebMalformedReply as exc:
            if exc.partial is not None:
                content = json.dumps(exc.partial, ensure_ascii=False)
            else:
                from app.services.ai.translation import TranslationBatchStructureError
                raise TranslationBatchStructureError(str(exc)) from exc
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
