
import sys
sys.path.insert(0, ".")
from plugin_manager import PluginEngine, PluginsPage, PluginAPI
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
engine = PluginEngine()
page = PluginsPage(engine)
for rec in engine.enabled_plugins():
    api = PluginAPI(rec.name)
    try:
        w = rec.build_page(page, api)
        print(rec.filename, "build_page=", w is not None)
    except Exception as e:
        print(rec.filename, "ERROR:", e)
