"""DeepSeek UI adapter over the shared durable browser request pipeline."""
from __future__ import annotations

import os
import json
import time
import re
from pathlib import Path
from urllib.parse import urlsplit

from .gemini_models import GeminiModelPolicy, ModelSelectionError
from .gemini_web import GeminiBrowser, GeminiWebError, GeminiWebModelError, WebConversationLimit, GeminiWebVerificationRequired
from app.services.persistence import atomic_json, read_json


class DeepSeekRateLimit(GeminiWebVerificationRequired):
    """Explicit rejection: stop ordinary batch retries and preserve checkpoints."""


class DeepSeekBrowser(GeminiBrowser):
    provider_label = 'DeepSeek'
    home_url = 'https://chat.deepseek.com/'
    host = 'chat.deepseek.com'
    chat_pattern = r'https://chat\.deepseek\.com/a/chat/s/[A-Za-z0-9_-]+'
    # Thinking also uses .ds-markdown: only the final-answer container is a reply.
    response_selector = '.ds-assistant-message-main-content'
    query_selector = '.ds-message:has(.ds-collapsible-text)'
    # DeepSeek currently exposes no accessible name on its stop button.
    # Restrict the fallback to the square icon in the primary composer button.
    stop_selector = '[role="button"].ds-button--primary:has(svg path[d^="M2 4.88"])'

    @staticmethod
    def profile_directory():
        data = Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'LwentEditor'
        profile = data / 'DeepSeekWeb'
        # Retain the user's already authenticated test profile, without copying cookies.
        previous = data / 'DeepSeekBenchmark'
        return previous if previous.is_dir() and not profile.exists() else profile

    def _policy(self, mode):
        from app.services.presets import get_processing_profile
        return GeminiModelPolicy('DeepSeek', 'off' if get_processing_profile(mode).name == 'fast' else 'on', 'DeepSeek')

    def _select_model(self, page, policy):
        for label, wanted in [('Search', False), ('DeepThink', policy.thinking != 'off')]:
            toggle = page.locator('[aria-pressed]').filter(has_text=label)
            toggle.wait_for(state='visible', timeout=10000)
            if toggle.count() != 1:
                raise ModelSelectionError(f'DEEPSEEK_MODEL_SELECTION: Không xác định được nút {label}; chưa gửi phụ đề.')
            if (toggle.get_attribute('aria-pressed') == 'true') != wanted:
                toggle.click(timeout=5000)
            if (toggle.get_attribute('aria-pressed') == 'true') != wanted:
                raise ModelSelectionError(f'DEEPSEEK_MODEL_MISMATCH: Chưa xác nhận {label}; chưa gửi phụ đề.')
        return {'model': 'DeepSeek', 'thinking': policy.thinking}

    def _status(self, open_browser=False):
        if open_browser:
            self._connect()
            self._set_visible(True)
            self._page.bring_to_front()
        page = self._page
        if page is None or page.is_closed():
            return {'ok': False, 'ready': False, 'message': 'Bấm Mở DeepSeek để kiểm tra đăng nhập. Khi dịch, app tự mở lại phiên đã lưu.'}
        verification = self._verification_message(page)
        if verification:
            return {'ok': False, 'ready': False, 'needs_verification': True, 'message': verification}
        login = urlsplit(page.url).hostname != self.host or '/sign_in' in page.url
        ready = not login and self._editor(page) is not None
        return {'ok': ready, 'ready': ready, 'needs_login': login,
                'message': 'DeepSeek sẵn sàng.' if ready else 'Đăng nhập trong cửa sổ DeepSeek rồi bấm Kiểm tra phiên.'}

    def _verification_message(self, page):
        # Inline delivery errors are not toasts. Only inspect notices belonging
        # to the latest user turn, so an old rejected message cannot poison a
        # later successful request in the same chat.
        notice = page.evaluate('''nonce => {
            const users = Array.from(document.querySelectorAll('.ds-message')).filter(
                e => e.querySelector('.ds-collapsible-text'));
            const last = users.at(-1);
            if (!last || !last.textContent.includes(nonce)) return null;
            return Array.from(document.querySelectorAll('body *')).filter(e =>
                !e.children.length && e.getClientRects().length &&
                (!last || last.contains(e) || (last.compareDocumentPosition(e) & 4)) &&
                !e.closest('.ds-collapsible-text, .ds-markdown, textarea')
            ).map(e => e.textContent.trim()).find(t =>
                /^(Messages too frequent\\. Try again later\\.?|Too many requests\\.?|发送.*频繁.*|操作.*频繁.*)$/i.test(t)) || null;
        }''', self._active_request_id) if getattr(self, '_rate_monitoring', False) and getattr(self, '_active_request_id', None) else None
        if notice:
            raise DeepSeekRateLimit('DEEPSEEK_RATE_LIMIT: DeepSeek giới hạn tần suất gửi. Đã giữ checkpoint; hãy chờ trước khi tiếp tục.')
        limit = page.get_by_text('Length limit reached. Please start a new chat.', exact=False)
        if any(limit.nth(i).is_visible() for i in range(limit.count())):
            raise WebConversationLimit('DEEPSEEK_CHAT_LIMIT: Cuộc trò chuyện đạt giới hạn độ dài.')
        if page.locator('iframe[src*="challenges.cloudflare.com"]').count():
            return 'DeepSeek yêu cầu xác minh. Hoàn tất trong cửa sổ DeepSeek rồi tiếp tục từ checkpoint.'
        alerts = page.locator('[role="alert"], .ds-toast')
        for i in range(alerts.count()):
            text = alerts.nth(i).inner_text().lower()
            if any(word in text for word in ('rate limit', 'too many', 'server is busy', 'network error')):
                raise GeminiWebError('DEEPSEEK_UNAVAILABLE: DeepSeek đang bận hoặc mất kết nối; checkpoint được giữ.')
        return None

    def _wait_cooldown(self, cancelled, on_wait=None):
        data = read_json(self.profile_directory() / 'send-cooldown.json') or {}
        until = data.get('until', 0) if isinstance(data, dict) else 0
        if not isinstance(until, (int, float)):
            return
        # Bound corrupt/future timestamps as well as waits; cancellation stays
        # responsive and progress callbacks keep the job UI alive.
        remaining = min(600, max(0, until - time.time()))
        deadline = time.monotonic() + remaining
        next_update = 0
        try:
            while time.monotonic() < deadline:
                if cancelled.is_set():
                    raise GeminiWebError('Đã hủy chờ DeepSeek; checkpoint được giữ.')
                left = max(0, deadline - time.monotonic())
                self.wait_reason = f'DeepSeek giới hạn tần suất; tự thử lại sau {int(left) + 1}s'
                if on_wait and time.monotonic() >= next_update:
                    on_wait(int(remaining - left))
                    next_update = time.monotonic() + 5
                cancelled.wait(min(1, left))
        finally:
            self.wait_reason = None

    def _reject_delivery(self, args, kwargs, status):
        def argument(name, index, default=None):
            return kwargs.get(name, args[index] if len(args) > index else default)
        conversation = argument('conversation', 5)
        policy = argument('policy', 7)
        if status == 'rate_limited' and conversation:
            usage = self._usage()
            record = usage.get(conversation)
            nonce = getattr(self, '_active_request_id', None)
            if record and nonce and record.get('pending_request_id') == nonce:
                record['turns'] = max(0, record['turns'] - 1)
                record['chars'] = max(0, record['chars'] - record.pop('pending_prompt_chars', 0))
                record.pop('pending_request_id', None)
                record['rejected_ids'] = list(dict.fromkeys([*record.get('rejected_ids', []), nonce]))[-64:]
                self._save_usage(usage)
        if conversation and policy:
            from .request_journal import RequestJournal
            journal = RequestJournal(self.profile_directory(), conversation,
                argument('messages', 0), policy, argument('attachments', 4))
            saved = journal.load()
            if saved:
                journal.save(status, saved['request_id'], self._page.url)

    def _generation_activity(self, page, request_id):
        state = super()._generation_activity(page, request_id)
        # Scope thought text to the assistant immediately after this exact request.
        # A timer, old answer, or global spinner must never extend the idle window.
        thought = page.evaluate('''nonce => {
            const messages = Array.from(document.querySelectorAll('.ds-message'));
            let index = -1;
            messages.forEach((e, i) => {
                if (e.querySelector('.ds-collapsible-text') && e.textContent.includes(nonce)) index = i;
            });
            const reply = messages[index + 1];
            if (index < 0 || !reply || reply.querySelector('.ds-collapsible-text')) return null;
            const thoughts = Array.from(reply.querySelectorAll('.ds-markdown:not(.ds-assistant-message-main-content)'));
            return thoughts.length ? thoughts.map(e => e.textContent).join('\\n') : null;
        }''', request_id)
        if state['generating'] and thought is not None:
            state['thinking'] = thought
        return state

    def _usage(self):
        data = read_json(self.profile_directory() / 'chat-usage.json')
        if not isinstance(data, dict):
            return {}
        return {scope: record for scope, record in data.items()
                if isinstance(scope, str) and isinstance(record, dict)
                and isinstance(record.get('url'), str)
                and all(type(record.get(key)) is int and record[key] >= 0 for key in ('turns', 'chars'))}

    def _save_usage(self, data):
        atomic_json(self.profile_directory() / 'chat-usage.json', dict(list(data.items())[-200:]))

    def _remember_conversation(self, page, scope):
        super()._remember_conversation(page, scope)
        if scope and page.url.startswith(self.home_url + 'a/chat/s/'):
            usage = self._usage()
            record = usage.get(scope)
            if isinstance(record, dict) and record.get('url') != page.url:
                record['url'] = page.url
                self._save_usage(usage)

    def _rotate_chat(self, page, conversation, cancelled):
        self._forget_failed_conversation(conversation)
        usage = self._usage()
        usage.pop(conversation, None)
        self._save_usage(usage)
        page.goto(self.home_url, wait_until='domcontentloaded', timeout=60000)
        if not self._wait_ready(page, cancelled):
            raise GeminiWebError('DeepSeek chưa sẵn sàng trong chat mới; checkpoint được giữ.')
        print('[DeepSeek Web] Rotated conversation; source context and validated checkpoints retained.', flush=True)

    def _before_send(self, page, prompt, conversation, cancelled, policy):
        from app.core import settings
        usage = self._usage()
        record = usage.get(conversation, {}) if conversation else {}
        if record.get('url') != page.url:
            return
        # Virtualized DOM counts can shrink. Durable usage includes completed
        # answers and Thinking; visible history is an additional lower bound
        # for requests restored after a crash or edited manually in the UI.
        visible_chars = page.evaluate('''rejected => {
            let total = 0, skip = false;
            for (const node of document.querySelectorAll('.ds-message')) {
                if (node.querySelector('.ds-collapsible-text')) {
                    skip = rejected.some(nonce => node.textContent.includes(nonce));
                }
                if (!skip) total += node.textContent.length;
            }
            return total;
        }''', record.get('rejected_ids', [])) if hasattr(page, 'evaluate') else 0
        used_chars = max(record.get('chars', 0), visible_chars)
        if used_chars > record.get('chars', 0):
            record['chars'] = used_chars
            self._save_usage(usage)
        limit = min(8, settings.gemini_web_max_chat_turns) if settings.gemini_web_max_chat_turns > 0 else 0
        if (limit and record.get('turns', 0) >= limit) or used_chars + len(prompt) > 80000:
            self._rotate_chat(page, conversation, cancelled)
            if policy is not None:
                try:
                    self._select_model(page, policy)
                except ModelSelectionError as exc:
                    raise GeminiWebModelError(str(exc)) from exc

    def _on_sent(self, page, prompt, conversation):
        self._rate_monitoring = True
        nonces = re.findall(r'"request_id"\s*:\s*"([a-f0-9]{32})"', prompt)
        self._active_request_id = nonces[-1] if nonces else None
        if not conversation:
            return
        usage = self._usage()
        record = usage.get(conversation, {})
        if record.get('url') != page.url:
            record = {}
        usage[conversation] = {**record, 'url': page.url, 'turns': record.get('turns', 0) + 1,
                               'chars': record.get('chars', 0) + len(prompt),
                               'pending_request_id': self._active_request_id,
                               'pending_prompt_chars': len(prompt)}
        self._save_usage(usage)

    def _on_reply(self, page, request_id, conversation):
        if not conversation:
            return
        usage = self._usage()
        record = usage.get(conversation)
        if not record or record.get('url') != page.url or record.get('last_reply_id') == request_id:
            return
        chars = page.evaluate('''nonce => {
            const messages = Array.from(document.querySelectorAll('.ds-message'));
            let index = -1;
            messages.forEach((node, i) => {
                if (node.querySelector('.ds-collapsible-text') && node.textContent.includes(nonce)) index = i;
            });
            let size = 0;
            for (let i = index + 1; index >= 0 && i < messages.length; i++) {
                if (messages[i].querySelector('.ds-collapsible-text')) break;
                size += messages[i].textContent.length;
            }
            return size;
        }''', request_id)
        if type(chars) is int and chars >= 0:
            record['chars'] += chars
            record['last_reply_id'] = request_id
            record.pop('pending_request_id', None)
            record.pop('pending_prompt_chars', None)
            self._save_usage(usage)

    def _response_belongs(self, response, request_id):
        # Scope to the current chat, with an exact nonce in its submitted user message.
        return response.evaluate('''(element, nonce) => {
            const nodes = document.querySelectorAll('.ds-message');
            return Array.from(nodes).some(query => !query.contains(element)
                && query.textContent.includes(nonce)
                && !!(query.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING));
        }''', request_id)

    @staticmethod
    def _upload_images(page, attachments):
        files = [str(Path(path).resolve()) for path in attachments]
        if not files or any(not Path(path).is_file() for path in files):
            raise GeminiWebError('Thiếu ảnh kiểm tra phụ đề để gửi DeepSeek.')
        previous = page.locator('img').evaluate_all('images => images.map(image => image.src)')
        page.locator('input[type="file"]').set_input_files(files, timeout=15000)
        page.wait_for_function('''data => Array.from(document.querySelectorAll('img')).filter(
            img => img.complete && img.naturalWidth > 0 && !data.previous.includes(img.src)
            && img.getClientRects().length > 0).length >= data.count''',
            arg={'previous': previous, 'count': len(files)}, timeout=60000)
        page.wait_for_function('''() => !Array.from(document.querySelectorAll('[role="progressbar"]')).some(e => e.getClientRects().length)''', timeout=60000)
        # Allow DeepSeek's asynchronous attachment processing after the local preview.
        page.wait_for_timeout(3500)

    def _complete_unlocked(self, *args, **kwargs):
        cancelled = kwargs.get('cancelled', args[2] if len(args) > 2 else None)
        on_wait = kwargs.get('on_wait', args[6] if len(args) > 6 else None)
        self._wait_cooldown(cancelled, on_wait)
        rate_retries = 0
        chat_retried = False
        try:
            for attempt in range(6):
                try:
                    self._rate_monitoring = False
                    return super()._complete_unlocked(*args, **kwargs)
                except DeepSeekRateLimit:
                    self._reject_delivery(args, kwargs, 'rate_limited')
                    delay = 60 * 2 ** rate_retries
                    atomic_json(self.profile_directory() / 'send-cooldown.json',
                                {'until': time.time() + delay})
                    if rate_retries >= 3:
                        raise
                    rate_retries += 1
                    print(f'[DeepSeek Web] Rate limited; waiting {delay}s before retry {rate_retries}/3. Checkpoints retained.', flush=True)
                    self._wait_cooldown(cancelled, on_wait)
                    # Reload the same conversation, never open chats to evade
                    # the account limit. The rejected request is not pending.
                    self._page.reload(wait_until='domcontentloaded', timeout=60000)
                except WebConversationLimit:
                    if chat_retried:
                        raise
                    chat_retried = True
                    def argument(name, index, default=None):
                        return kwargs.get(name, args[index] if len(args) > index else default)
                    conversation = argument('conversation', 5)
                    messages, cancelled = argument('messages', 0), argument('cancelled', 2)
                    policy, attachments = argument('policy', 7), argument('attachments', 4)
                    if conversation and policy:
                        from .request_journal import RequestJournal
                        journal = RequestJournal(self.profile_directory(), conversation, messages, policy, attachments)
                        saved = journal.load()
                        if saved:
                            # Never restore the explicitly rejected chat as a pending request.
                            journal.save('chat_limit', saved['request_id'], self._page.url)
                    self._rotate_chat(self._page, conversation, cancelled)
        except GeminiWebError as exc:
            # Preserve typed timeout/refusal/partial errors used by cue recovery.
            exc.args = (str(exc).replace('Gemini', 'DeepSeek').replace('GEMINI_', 'DEEPSEEK_'),)
            raise


browser = DeepSeekBrowser()
