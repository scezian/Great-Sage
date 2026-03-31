
import sys
sys.path.insert(0, ".")
from plugin_manager import PluginEngine
engine = PluginEngine()
for rec in engine.all_plugins():
    print(rec.filename, "enabled=", rec.enabled)
    ok = rec.load_module()
    print("  load=", ok, "error=", rec.error)
