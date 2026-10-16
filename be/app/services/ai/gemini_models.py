"""Model and thinking choices explicitly requested for subtitle modes."""
from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class GeminiModelPolicy:
    model: str
    thinking: str
    label_pattern: str

    @property
    def cache_key(self) -> str:
        return f"{'DeepSeek Web' if self.model == 'DeepSeek' else 'Gemini Web'}:{self.model}:{self.thinking}"


POLICIES = {
    'fast': GeminiModelPolicy('3.8 Flash', 'low', r'3\.8\s+Flash'),
    'balanced': GeminiModelPolicy('3.8 Flash', 'medium', r'3\.8\s+Flash'),
    'quality': GeminiModelPolicy('3.8 Flash', 'high', r'3\.8\s+Flash'),
}
active_model_policy: ContextVar[GeminiModelPolicy | None] = ContextVar('gemini_model_policy', default=None)


def model_policy(mode=None) -> GeminiModelPolicy:
    from app.services.presets import get_processing_profile
    from app.core import settings
    name = get_processing_profile(mode).name
    if settings.translation_engine.lower() == 'deepseek_web':
        return GeminiModelPolicy('DeepSeek', 'off' if name == 'fast' else 'on', 'DeepSeek')
    return POLICIES[name]


def current_model_key() -> str:
    policy = active_model_policy.get()
    return policy.cache_key if policy else 'Gemini Web:manual'


class ModelSelectionError(RuntimeError):
    pass


def _selected(item) -> bool:
    # data-active is keyboard focus, not the selected model.
    return item.evaluate("e => e.classList.contains('selected') || e.getAttribute('aria-checked') === 'true' || !!e.querySelector('.checkmark-only.selected, [data-mat-icon-name=check]')", timeout=3000)


def select_model(page, policy: GeminiModelPolicy) -> dict:
    """Select and re-read both menus before allowing any prompt to be sent."""
    picker = page.locator('[data-test-id="bard-mode-menu-button"]')

    def open_menu():
        menu = page.locator('[data-test-id="gem-mode-menu"]')
        # Immediately after navigation Gemini may replace the picker during
        # hydration. Reopen once; no prompt has been filled or submitted yet.
        for attempt in range(2):
            if menu.is_visible():
                return menu
            if picker.get_attribute('aria-expanded') == 'true':
                page.keyboard.press('Escape')
            picker.click(timeout=5000)
            try:
                menu.wait_for(state='visible', timeout=3000)
                return menu
            except Exception:
                if attempt:
                    raise
        raise ModelSelectionError('Không mở được menu model Gemini.')

    def target(menu):
        label = page.locator('.label').filter(has_text=re.compile(r'^\s*' + policy.label_pattern + r'\s*$', re.I))
        return menu.locator('[data-test-id^="bard-mode-option-"]').filter(has=label)

    try:
        menu = open_menu()
        item = target(menu)
        # The overlay can be visible before Angular has populated its rows.
        # A synchronous count() here incorrectly reported an available model
        # as missing immediately after restoring a long conversation.
        item.wait_for(state='visible', timeout=5000)
        if item.count() != 1:
            raise ModelSelectionError(f'GEMINI_MODEL_UNAVAILABLE: Không chọn được {policy.model}. Kiểm tra model trong cửa sổ Gemini; chưa gửi phụ đề.')
        if item.get_attribute('aria-disabled') == 'true':
            # A just-finished generation temporarily disables model choices.
            # This is not evidence the account lacks this model.
            page.wait_for_function("e => e.getAttribute('aria-disabled') !== 'true'", arg=item.element_handle(), timeout=15000)
        if not _selected(item):
            item.click(timeout=5000)
            menu.wait_for(state='hidden', timeout=5000)
            menu = open_menu()
            item = target(menu)
            item.wait_for(state='visible', timeout=5000)
        if not _selected(item):
            raise ModelSelectionError(f'GEMINI_MODEL_MISMATCH: Gemini chưa xác nhận {policy.model}; chưa gửi phụ đề.')

        thinking = menu.locator('[role="menuitem"][value="thinking_level"]')
        thinking.hover(timeout=5000)
        submenu_id = thinking.get_attribute('aria-controls')
        submenu = page.locator(f'[id="{submenu_id}"]') if submenu_id else page.locator('[role="menu"]').last
        if not submenu.is_visible():
            thinking.click(timeout=5000)
        submenu.wait_for(state='visible', timeout=5000)
        pattern = {'low': r'(Thấp|Low)', 'medium': r'(Trung bình|Medium)', 'high': r'(Cao|High)'}[policy.thinking]
        level = submenu.get_by_role('menuitem').filter(has=page.locator('.label').filter(has_text=re.compile(r'^\s*' + pattern + r'\s*$', re.I)))
        level.wait_for(state='visible', timeout=5000)
        if level.get_attribute('aria-disabled') == 'true':
            page.wait_for_function("e => e.getAttribute('aria-disabled') !== 'true'",arg=level.element_handle(),timeout=15000)
        if level.count() != 1:
            raise ModelSelectionError(f'GEMINI_THINKING_UNAVAILABLE: {policy.model} không có mức tư duy {policy.thinking}; chưa gửi phụ đề.')
        if not _selected(level):
            level.click(timeout=5000)
            submenu.wait_for(state='hidden', timeout=5000)
            menu = open_menu()
            thinking = menu.locator('[role="menuitem"][value="thinking_level"]')
            thinking.hover(timeout=5000)
            if not submenu.is_visible():
                thinking.click(timeout=5000)
            submenu.wait_for(state='visible', timeout=5000)
        if not _selected(level) or not _selected(target(menu)):
            raise ModelSelectionError('GEMINI_MODEL_MISMATCH: Model hoặc mức tư duy không khớp; chưa gửi phụ đề.')
        return {'model': policy.model, 'thinking': policy.thinking}
    except ModelSelectionError:
        raise
    except Exception as exc:
        raise ModelSelectionError(f'GEMINI_MODEL_SELECTION: Không xác nhận được {policy.model}/{policy.thinking}. Mở Gemini kiểm tra giao diện rồi thử lại; chưa gửi phụ đề.') from exc
    finally:
        page.keyboard.press('Escape')
        page.keyboard.press('Escape')
