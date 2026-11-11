from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MonitorConfig:
    """与行情软件一致：最新价 >= 昨收×ratio 即触发；触发时刻对齐为所在分钟 M:00。"""

    session_start_minute: int = 9 * 60 + 30
    session_end_minute: int = 9 * 60 + 40
    poll_interval_s: float = 3.0
    extra_poll_second: float = 58.5
    trigger_ratio: float = 1.03
    quote_timeout_s: float = 4.0
    quote_retries: int = 0
    batch_size: int = 60
    max_parallel_batches: int = 2
    near_threshold_bps: float = 15.0
    mkline_confirm: bool = True
    # 推送在触发时刻之后的最小延迟（秒），模拟企微发送
    notify_delay_s: float = 1.0
