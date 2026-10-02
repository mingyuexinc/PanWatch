"""deploy/Dockerfile 精简镜像的冒烟测试(对应 scripts/docker_smoke.py 的部署版)。

差异(镜像内不含这些依赖,不能用):
- 不 import tradingagents(部署镜像按需摘除,Agent 侧懒加载优雅降级)。
- 不用 pypdf 校验 PDF 文本(仅 WeasyPrint 主引擎 + %PDF 魔数/体积)。

用法(镜像 /app 工作目录,仓库只读挂载):
docker run --rm -v "$PWD:/checks:ro" panwatch:deploy \
    python /checks/scripts/docker_smoke_deploy.py
"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path.cwd()))


def check_packages() -> None:
    import importlib

    for name in ("marketdata", "pan_agent", "pan_agent_token_meter", "pan_agent_tool_research"):
        importlib.import_module(name)
    print("PASS: installed local packages import", flush=True)


def check_pdf() -> None:
    from src.modules.reporting.pdf_export import _render_weasyprint, render_analysis_pdf

    # 部署镜像没有回退引擎:直接测主渲染器,缺系统库时必须失败而非静默回退。
    primary = _render_weasyprint("中文报告", "<p>股票分析与风险提示</p>")
    assert primary.startswith(b"%PDF") and len(primary) > 2000
    pdf = render_analysis_pdf("中文报告", "# 广汽集团\n\n**持有**", language="zh-CN")
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000
    print("PASS: primary PDF renderer (WeasyPrint) and Chinese report export", flush=True)


async def check_screenshot() -> None:
    from PIL import Image
    from src.platform.marketdata.collectors.screenshot_collector import ScreenshotCollector

    collector = ScreenshotCollector()
    try:
        await collector._ensure_browser()
        page = await collector._browser.new_page(viewport={"width": 640, "height": 480})
        await page.set_content("""
            <style>body { font-family: 'Noto Sans CJK SC', sans-serif; }</style>
            <h1>股票行情 Stock chart</h1><canvas id="chart" width="500" height="300"></canvas>
            <script>
                const c = document.getElementById('chart').getContext('2d');
                c.fillStyle = '#16a34a'; c.fillRect(40, 40, 30, 100);
                c.fillStyle = '#dc2626'; c.fillRect(100, 80, 30, 120);
            </script>
        """)
        await page.evaluate("document.fonts.ready")
        png = await page.screenshot()
        image = Image.open(io.BytesIO(png)).convert("RGB")
        assert image.size == (640, 480)
        colors = {color for _, color in image.getcolors(640 * 480)}
        assert (22, 163, 74) in colors and (220, 38, 38) in colors
        assert await page.locator("h1").inner_text() == "股票行情 Stock chart"
    finally:
        await collector.close()
    print("PASS: production screenshot collector and canvas rendering", flush=True)


def check_http_startup() -> None:
    with tempfile.TemporaryDirectory(prefix="panwatch-deploy-smoke-") as data_dir:
        with tempfile.TemporaryFile(mode="w+") as log:
            env = {**os.environ, "DATA_DIR": data_dir}
            process = subprocess.Popen([sys.executable, "server.py"], env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2) as response:
                            assert response.status == 200
                        with urllib.request.urlopen("http://127.0.0.1:8000/", timeout=2) as response:
                            assert b"<html" in response.read().lower()
                        print("PASS: production startup, health endpoint, and frontend", flush=True)
                        return
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.5)
                log.seek(0)
                raise AssertionError("HTTP startup failed:\n" + log.read())
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main() -> None:
    check_packages()
    check_pdf()
    from server import setup_playwright

    setup_playwright()
    asyncio.run(check_screenshot())
    check_http_startup()


if __name__ == "__main__":
    main()
