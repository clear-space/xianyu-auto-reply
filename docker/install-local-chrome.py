"""
从本地 chrome-linux64.zip 安装 Chromium 到 playwright/patchright 缓存目录。
避免从 cdn.playwright.dev 下载（国内网络无法访问）。

用法：在 Dockerfile 中 COPY 此脚本和 chrome-linux64.zip，pip install 后执行。
"""
import importlib
import json
import os
import sys
import zipfile
from pathlib import Path


def get_expected_revision(package_name: str, browser_name: str) -> str | None:
    """从已安装的包读取浏览器预期 revision，不存在则返回 None"""
    pkg = importlib.import_module(package_name)
    pkg_dir = Path(pkg.__file__).parent
    browsers_json = pkg_dir / "driver" / "package" / "browsers.json"
    if not browsers_json.exists():
        browsers_json = pkg_dir / "driver" / "browsers.json"
    if not browsers_json.exists():
        return None
    browsers = json.loads(browsers_json.read_text(encoding="utf-8"))["browsers"]
    for b in browsers:
        if b.get("name") == browser_name:
            return str(b["revision"])
    return None


def _zip_dir_mapping(browser_name: str) -> tuple[str, str]:
    """返回 (zip内目录名, playwright缓存目录名)"""
    m = {
        "chromium": ("chrome-linux64", "chrome-linux"),
        "chromium-headless-shell": ("chrome-headless-shell-linux64", "chrome-headless-shell-linux"),
    }
    return m.get(browser_name, ("chrome-linux64", "chrome-linux"))


def install_from_zip(
    zip_path: str,
    package_name: str = "playwright",
    browser_name: str = "chromium",
    browsers_root: str | None = None,
):
    """将 chrome zip 解压到 playwright/patchright 浏览器缓存目录"""
    if browsers_root is None:
        browsers_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")

    revision = get_expected_revision(package_name, browser_name)
    if revision is None:
        print(f"[local-chrome] [{package_name}] {browser_name} 不在 browsers.json 中，跳过")
        return

    target_dir = os.path.join(browsers_root, f"{browser_name}-{revision}")
    zip_extracted, target_subdir = _zip_dir_mapping(browser_name)
    chrome_dir = os.path.join(target_dir, target_subdir)
    marker = os.path.join(target_dir, "INSTALLATION_COMPLETE")

    if os.path.exists(chrome_dir):
        print(f"[local-chrome] [{package_name}] {browser_name} r{revision} 已存在，跳过")
        return

    print(f"[local-chrome] [{package_name}] 从 {zip_path} 安装 {browser_name} r{revision} ...")
    os.makedirs(target_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(target_dir)

    extracted = os.path.join(target_dir, zip_extracted)
    if os.path.isdir(extracted) and not os.path.exists(chrome_dir):
        os.rename(extracted, chrome_dir)

    if not os.path.exists(chrome_dir):
        raise RuntimeError(
            f"解压后找不到目录: {chrome_dir}，"
            f"期望 {zip_extracted} -> {target_subdir}，"
            f"实际: {os.listdir(target_dir)}"
        )

    with open(marker, "w") as f:
        f.write(".\n")

    if os.path.exists(os.path.join(chrome_dir, "chrome")):
        print(f"[local-chrome] ✓ [{package_name}] {browser_name} r{revision} 安装完成")
    else:
        print(f"[local-chrome] ⚠ [{package_name}] chrome 可执行文件未找到")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="从本地 zip 安装 Chromium 到浏览器缓存")
    parser.add_argument("zip_file", help="chrome-linux64.zip 路径")
    parser.add_argument(
        "--package", default="playwright", help="包名: playwright 或 patchright (默认: playwright)"
    )
    parser.add_argument("--browser", default="chromium", help="浏览器名称 (默认: chromium)")
    parser.add_argument(
        "--root", default=None, help="缓存根目录 (默认: $PLAYWRIGHT_BROWSERS_PATH)"
    )
    args = parser.parse_args()

    install_from_zip(args.zip_file, args.package, args.browser, args.root)
