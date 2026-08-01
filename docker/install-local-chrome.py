"""
从本地 chrome-linux64.zip 安装 Chromium 到 playwright/patchright 缓存目录。
避免从 cdn.playwright.dev 下载（国内网络无法访问）。

用法：在 Dockerfile 中 COPY 此脚本和 chrome-linux64.zip，pip install 后执行。
"""
import importlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path


def get_browser_specs(package_name: str, browser_name: str) -> dict | None:
    """从已安装包的 browsers.json 读取浏览器规格（name, revision, installByDefault）"""
    pkg = importlib.import_module(package_name)
    pkg_dir = Path(pkg.__file__).parent
    for sub in ("driver/package/browsers.json", "driver/browsers.json"):
        f = pkg_dir / sub
        if f.exists():
            browsers = json.loads(f.read_text(encoding="utf-8"))["browsers"]
            for b in browsers:
                if b.get("name") == browser_name:
                    return b
    return None


def _zip_dir_mapping(browser_name: str) -> tuple[str, str]:
    """返回 (zip内目录名, 缓存目录名)"""
    m = {
        "chromium": ("chrome-linux64", "chrome-linux"),
        "chromium-headless-shell": ("chrome-headless-shell-linux64", "chrome-headless-shell-linux"),
    }
    return m.get(browser_name, ("chrome-linux64", "chrome-linux"))


def is_installed(browsers_root: str, browser_name: str, revision: str) -> bool:
    """检查浏览器是否已安装"""
    target_dir = os.path.join(browsers_root, f"{browser_name}-{revision}")
    _, target_subdir = _zip_dir_mapping(browser_name)
    chrome_dir = os.path.join(target_dir, target_subdir)
    return os.path.isdir(chrome_dir)


def install_from_zip(
    zip_path: str,
    package_name: str = "playwright",
    browser_name: str = "chromium",
    browsers_root: str | None = None,
):
    """将 chrome zip 解压到浏览器缓存目录"""
    if browsers_root is None:
        browsers_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")

    spec = get_browser_specs(package_name, browser_name)
    if spec is None:
        print(f"[local-chrome] [{package_name}] {browser_name} 不在 browsers.json 中，跳过")
        return

    revision = str(spec["revision"])

    if is_installed(browsers_root, browser_name, revision):
        print(f"[local-chrome] [{package_name}] {browser_name} r{revision} 已存在，跳过")
        return

    # 尝试复用已安装的其他 revision 的浏览器（playwright/patchright 版本不同但 Chromium 兼容）
    if _try_reuse_existing(browsers_root, browser_name, revision, package_name):
        return

    target_dir = os.path.join(browsers_root, f"{browser_name}-{revision}")
    zip_extracted, target_subdir = _zip_dir_mapping(browser_name)
    chrome_dir = os.path.join(target_dir, target_subdir)
    marker = os.path.join(target_dir, "INSTALLATION_COMPLETE")

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


def _try_reuse_existing(browsers_root: str, browser_name: str, target_revision: str, package_name: str) -> bool:
    """尝试从已安装的其他 revision 复用浏览器目录（playwright/patchright 版本不同时可用）"""
    prefix = f"{browser_name}-"
    if not os.path.isdir(browsers_root):
        return False

    existing = [
        d for d in os.listdir(browsers_root)
        if d.startswith(prefix) and d != f"{browser_name}-{target_revision}"
    ]
    if not existing:
        return False

    _, target_subdir = _zip_dir_mapping(browser_name)
    src_dir = os.path.join(browsers_root, existing[0])

    # 验证源目录有效
    for possible_subdir in ["chrome-linux", "chrome-headless-shell-linux"]:
        if os.path.isdir(os.path.join(src_dir, possible_subdir)):
            break
    else:
        return False

    dst_dir = os.path.join(browsers_root, f"{browser_name}-{target_revision}")
    src_revision = existing[0].replace(prefix, "")

    print(f"[local-chrome] [{package_name}] 复用 {browser_name} r{src_revision} -> r{target_revision}")
    shutil.copytree(src_dir, dst_dir)
    with open(os.path.join(dst_dir, "INSTALLATION_COMPLETE"), "w") as f:
        f.write(".\n")
    print(f"[local-chrome] ✓ [{package_name}] {browser_name} r{target_revision} 安装完成（复用）")
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="从本地 zip 安装 Chromium 到浏览器缓存")
    parser.add_argument("zip_file", help="chrome-linux64.zip 路径")
    parser.add_argument("--package", default="playwright", help="包名: playwright / patchright")
    parser.add_argument("--browser", default="chromium", help="浏览器名称")
    parser.add_argument("--root", default=None, help="缓存根目录")

    args = parser.parse_args()
    install_from_zip(args.zip_file, args.package, args.browser, args.root)
