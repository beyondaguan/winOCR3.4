# -*- coding: utf-8 -*-
"""对话框入口（薄壳）。

3.4.18 按职责拆分为 dialogs_common / dialogs_settings / dialogs_data /
dialogs_misc；后续将 dialogs_settings 再拆出 dialogs_hotkey（热键）与
dialogs_test（连接测试）。本文件统一 re-export，保持旧 import
（from .dialogs import ...）兼容。
"""
from .dialogs_common import (
    _post_to_ui, _center, _modal, _ScrollableFrame,
    _record_key, _hotkey_status,
)
from .dialogs_hotkey import open_hotkey_settings
from .dialogs_settings import open_api_settings
from .dialogs_test import _test_ai, _test_translate, _test_ocr
from .dialogs_data import (
    ensure_import_templates, _run_import, open_import_dialog,
    open_knowledge, open_history,
)
from .dialogs_misc import open_about, open_plugins, open_first_run
