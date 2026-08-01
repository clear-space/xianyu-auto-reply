"""
从本地 chrome-linux64.zip 安装 Chromium 到 playwright/patchright 缓存目录。
避免从 cdn.playwright.dev 下载（国内网络无法访问）。

用法：在 Dockerfile 中 COPY 此脚本和 chrome-linux64.zip，pip install 后执行。
"""
import json
import os
import sys
import zipfile


def find_package_site_packages(package_name: str):
    """查找包的 site-packages 路径"""
    for p in sys.path:
        if not p:
            continue
        candidate = os.path.join(p, package_name)
        if os.path.isdir(candidate):
            return p
    raise RuntimeError(f"找不到 {package_name} 包，请先 pip install")


def get_expected_revision(package_name: str, browser_name: str) -> str:
    """从已安装的包读取浏览器预期 revision"""
    site = find_package_site_packages(package_name)
    browsers_json = os.path.join(site, package_name, "driver", "browsers.json")
    if not os.path.exists(browsers_json):
        raise RuntimeError(f"找不到 browsers.json: {browsers_json}")
    with open(browsers_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    browsers = data.get("browsers", data) if isinstance(data, dict) else data
    for b in browsers:
        if b.get("name") == browser_name:
            return str(b["revision"])
    raise RuntimeError(f"{browsers_json} 中找不到 {browser_name}")


def install_from_zip(
    zip_path: str,
    package_name: str = "playwright",
    browser_name: str = "chromium",
    browsers_root: str | None = None,
):
    """将 chrome-linux64.zip 解压到浏览器缓存目录"""
    if browsers_root is None:
        browsers_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")

    revision = get_expected_revision(package_name, browser_name)
    target_dir = os.path.join(browsers_root, f"{browser_name}-{revision}")
    chrome_dir = os.path.join(target_dir, "chrome-linux")
    marker = os.path.join(target_dir, "INSTALLATION_COMPLETE")

    if os.path.exists(chrome_dir):
        print(f"[local-chrome] [{package_name}] {browser_name} r{revision} 已存在，跳过")
        return

    print(f"[local-chrome] [{package_name}] 从 {zip_path} 安装 {browser_name} r{revision} ...")
    os.makedirs(target_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(target_dir)

    # playwright/patchright 的 chrome-linux64.zip 解压后顶层是 chrome-linux64/
    extracted = os.path.join(target_dir, "chrome-linux64")
    if os.path.isdir(extracted) and not os.path.exists(chrome_dir):
        os.rename(extracted, chrome_dir)

    if not os.path.exists(chrome_dir):
        raise RuntimeError(f"解压后找不到 chrome 目录: {chrome_dir}，目录内容: {os.listdir(target_dir)}")

    # 标记安装完成
    with open(marker, "w") as f:
        f.write(".\n")

    chrome_bin = os.path.join(chrome_dir, "chrome")
    if os.path.exists(chrome_bin):
        print(f"[local-chrome] ✓ [{package_name}] {browser_name} r{revision} 安装完成")
    else:
        print(f"[local-chrome] ⚠ [{package_name}] chrome 可执行文件未找到: {chrome_bin}")


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
