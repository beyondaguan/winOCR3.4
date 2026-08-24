# -*- coding: utf-8 -*-
"""冒烟测试：验证插件架构核心契约，无需安装重型依赖。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from winocr.core import App, AppConfig
from winocr.core.registry import PluginRegistry
from winocr.services.translate.base import TranslateEngine
from winocr.services.translate.dispatcher import TranslateDispatcher


def test_plugin_discovery():
    """注册表能从 services/translate 自动发现 argos / glm 等引擎。"""
    reg = PluginRegistry()
    classes = reg.discover(TranslateEngine, "winocr.services.translate")
    assert "argos" in classes, "未发现有 argos 引擎"
    assert "glm" in classes, "未发现有 glm 引擎"
    # 发现阶段不应要求 ctranslate2（惰性导入）
    print("  [+] 发现翻译引擎:", sorted(classes.keys()))


def test_app_build_wires_services():
    """App 组合根能构建并发现各轴插件。"""
    app = App(AppConfig.defaults()).build()
    assert "translate" in app.discovered
    assert "ocr" in app.discovered
    assert "ai" in app.discovered
    assert app.pipeline is not None
    print("  [+] App 构建成功，已发现轴:",
          {k: sorted(v.keys()) for k, v in app.discovered.items()})


def test_dispatcher_fallback_order():
    """调度器按回退顺序选择首个可用引擎。"""

    class OfflineFake(TranslateEngine):
        name = "offline_fake"
        display_name = "Offline"
        online = False

        def translate(self, text, source, target):
            return f"[offline]{text}"

        def available(self):
            return True

    class OnlineFake(TranslateEngine):
        name = "online_fake"
        display_name = "Online"
        online = True

        def translate(self, text, source, target):
            return f"[online]{text}"

        def available(self):
            return True

    cfg = AppConfig.defaults().translate
    cfg.fallback_order = ["online_fake", "offline_fake"]
    disp = TranslateDispatcher(
        {"online_fake": OnlineFake(), "offline_fake": OfflineFake()}, cfg)
    # 顺序在前者优先
    assert disp.translate("hi", "en") == "[online]hi", "应优先选 online_fake"
    print("  [+] 调度器回退顺序正确")


def test_event_bus_decoupled():
    """事件总线解耦发布/订阅，无全局锁。"""
    from winocr.core import EventBus, Events
    bus = EventBus()
    got = []
    bus.subscribe(Events.STATUS, lambda p: got.append(p))
    bus.publish(Events.STATUS, "hello")
    assert got == ["hello"]
    print("  [+] 事件总线发布/订阅正常")


if __name__ == "__main__":
    test_plugin_discovery()
    test_app_build_wires_services()
    test_dispatcher_fallback_order()
    test_event_bus_decoupled()
    print("\n全部冒烟测试通过")
