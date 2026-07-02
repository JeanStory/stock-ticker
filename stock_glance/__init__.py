"""任务栏滚动股票行情组件。"""

from .config import Config
from .quotes import Quote, fetch_quotes
from .widget import TickerWidget

__version__ = "1.0.0"
__all__ = ["Config", "Quote", "fetch_quotes", "TickerWidget"]
