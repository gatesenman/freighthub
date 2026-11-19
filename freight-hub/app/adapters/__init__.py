from .xinhang import XinhangAdapter
from .v5 import ZhonghangAdapter, HuanyangAdapter
from .yuntu import YuntuAdapter
from .yanwen import YanwenAdapter
from .shunfeng import ShunfengAdapter
from .placeholder import YutongAdapter

ADAPTER_CLASSES = [YuntuAdapter, ZhonghangAdapter, XinhangAdapter, HuanyangAdapter, YanwenAdapter, ShunfengAdapter, YutongAdapter]


def build_registry(store):
    return {c.id: c(store) for c in ADAPTER_CLASSES}
