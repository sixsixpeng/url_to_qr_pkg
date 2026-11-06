# -*- coding: utf-8 -*-
"""
URL -> QR 工具（包版本）

此版本把所有输出定向到与脚本同级的 `output/QR` 目录下，从而避免写入全局路径。

使用方法（交互式）：
python Common_scripts/url_to_qr_pkg/url_to_qr.py

可选环境变量或命令行参数来覆盖保存目录：
--save-dir 可以传入绝对路径或相对路径（相对于当前工作目录）。

Basic Auth 凭据与免注入域名不属于仓库内容，请放在本地未跟踪配置
url_to_qr_private.json（与脚本同级，已被 .gitignore 忽略），或通过环境变量提供：
  URL_QR_BASIC_AUTH（如 "user:password"）
  URL_QR_NO_BASIC_AUTH_HOSTS（逗号分隔的域名列表）

依赖：qrcode, pillow
安装：pip install qrcode[pil]
"""
import os
import re
import json
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit
import qrcode
from PIL import Image, ImageDraw, ImageFont
import argparse

DEFAULT_SAVE_OPTIONS = {
    "format": "PNG",
    "box_size": 12,
    "border": 4,
    # file_name: 如果传入则覆盖自动生成的文件名
    "file_name": None,
}

# 全局保存选项（可在脚本中修改以调整保存行为）
SAVE_OPTIONS = None
# 字体相关：若要在二维码顶部显示 URL（包含中文），建议指定支持中文的 TTF 字体路径
FONT_PATH = None

# 基于脚本文件位置的默认输出目录：
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SAVE_DIR = str(BASE_DIR / "output" / "QR")

# 本地配置文件：与脚本同级的 url_to_qr_private.json（不纳入版本控制）
LOCAL_CONFIG_PATH = Path(__file__).resolve().with_name("url_to_qr_private.json")


def _load_basic_auth_settings() -> tuple[Optional[str], set[str]]:
    """合并本地配置文件与环境变量，返回 Basic Auth 凭据与免注入域名。"""
    configuration: dict[str, object] = {}
    try:
        with LOCAL_CONFIG_PATH.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        if isinstance(loaded, dict):
            configuration = loaded
    except (OSError, ValueError):
        configuration = {}

    userinfo = configuration.get("basic_auth")
    hosts = configuration.get("no_basic_auth_hosts")

    env_userinfo = os.environ.get("URL_QR_BASIC_AUTH")
    if env_userinfo is not None:
        userinfo = env_userinfo
    env_hosts = os.environ.get("URL_QR_NO_BASIC_AUTH_HOSTS")
    if env_hosts is not None:
        hosts = env_hosts.split(",")

    if isinstance(hosts, str):
        hosts = hosts.split(",")
    if not isinstance(hosts, (list, tuple, set)):
        hosts = []

    no_auth_hosts = {str(host).strip() for host in hosts if str(host).strip()}
    return (str(userinfo).strip() if userinfo else None, no_auth_hosts)


# 输入的 HTTP/HTTPS 地址未包含账号密码时，使用本地配置的 Basic Auth 凭据注入。
BASIC_AUTH_USERINFO, NO_BASIC_AUTH_HOSTS = _load_basic_auth_settings()


def add_basic_auth_if_missing(url: str) -> str:
    """为不带 userinfo 的 HTTP/HTTPS URL 注入本地配置的 Basic Auth 凭据。"""
    if not BASIC_AUTH_USERINFO:
        return url
    parsed = urlsplit(url)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.hostname in NO_BASIC_AUTH_HOSTS
    ):
        return url

    return urlunsplit((
        parsed.scheme,
        f"{BASIC_AUTH_USERINFO}@{parsed.netloc}",
        parsed.path,
        parsed.query,
        parsed.fragment,
    ))


def sanitize_filename(name: str, max_len: int = 200) -> str:
    if not name:
        return "unnamed"
    name = unquote(name)
    name = name.replace("/", "_").replace("\\\\", "_")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip(" _.")
    if not name:
        return "unnamed"
    if len(name) > max_len:
        name = name[:max_len]
    return name


def extract_name_from_url(url: str) -> str:
    if not url:
        return "unnamed"
    parsed = urlparse(url)
    parts = parsed.path or ""
    if parsed.query:
        parts += "?" + parsed.query
    if parsed.fragment:
        parts += "#" + parsed.fragment
    parts = re.sub(r"^[/:?#]+", "", parts)
    return parts or "root"


def extract_display_text_from_url(url: str) -> str:
    if not url:
        return "unnamed"
    parsed = urlparse(url)
    parts = parsed.path or "/"
    if parsed.query:
        parts += "?" + parsed.query
    if parsed.fragment:
        parts += "#" + parsed.fragment
    return parts or "/"


def build_timestamped_filename(file_name: str, ext: str) -> str:
    base_name = file_name
    if file_name.lower().endswith("." + ext):
        base_name = file_name[:-(len(ext) + 1)]

    base_name = sanitize_filename(base_name)
    if not base_name:
        base_name = "unnamed"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{base_name}.{ext}"


def resolve_non_overwriting_path(save_dir: str, file_name: str) -> str:
    full_path = os.path.abspath(os.path.join(save_dir, file_name))
    if not os.path.exists(full_path):
        return full_path

    stem, suffix = os.path.splitext(file_name)
    counter = 2
    while True:
        candidate_name = f"{stem}_{counter:02d}{suffix}"
        candidate_path = os.path.abspath(os.path.join(save_dir, candidate_name))
        if not os.path.exists(candidate_path):
            return candidate_path
        counter += 1


def url_to_qr(url: str, save_dir: str = DEFAULT_SAVE_DIR, save_options=None) -> str:
    if qrcode is None:
        raise RuntimeError("qrcode library is required. Install with: pip install qrcode[pil]")

    input_url = url.strip()
    url = add_basic_auth_if_missing(input_url)
    if url != input_url:
        print(f"已自动拼接认证信息: {url}")

    opts = DEFAULT_SAVE_OPTIONS.copy()
    if save_options:
        if isinstance(save_options, str):
            try:
                save_options = json.loads(save_options)
            except Exception:
                save_options = {}
        if isinstance(save_options, dict):
            opts.update(save_options)

    img_format = str(opts.get("format") or "PNG").upper()
    box_size = int(opts.get("box_size") or 10)
    border = int(opts.get("border") or 4)

    # 确保保存目录存在
    os.makedirs(save_dir, exist_ok=True)

    # 确定文件名
    file_name = opts.get("file_name")
    if not file_name:
        extracted = extract_name_from_url(url)
        file_name = sanitize_filename(extracted)
    if not file_name:
        file_name = "unnamed"
    file_name = str(file_name)

    ext = img_format.lower()
    file_name = build_timestamped_filename(file_name, ext)
    full_path = resolve_non_overwriting_path(save_dir, file_name)

    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    if isinstance(img, Image.Image):
        qr_img = img.convert('RGB')
    elif hasattr(img, 'get_image'):
        qr_img = img.get_image().convert('RGB')
    elif hasattr(img, 'convert'):
        qr_img = img.convert('RGB')
    else:
        raise TypeError(f"Unsupported QR image type: {type(img)!r}")

    font = None
    font_size = max(12, qr_img.width // 25)
    if isinstance(FONT_PATH, (str, os.PathLike)):
        try:
            font = ImageFont.truetype(FONT_PATH, font_size)
        except OSError:
            font = None

    if font is None:
        candidates = []
        if sys.platform.startswith('win'):
            candidates = [r"C:/Windows/Fonts/msyh.ttc", r"C:/Windows/Fonts/msyh.ttf", r"C:/Windows/Fonts/simhei.ttf", r"C:/Windows/Fonts/arial.ttf"]
        elif sys.platform == 'darwin':
            candidates = ['/System/Library/Fonts/STHeiti Medium.ttc', '/Library/Fonts/Arial Unicode.ttf', '/Library/Fonts/Arial.ttf']
        else:
            candidates = ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/freefont/FreeSans.ttf']
        for c in candidates:
            try:
                font = ImageFont.truetype(c, font_size)
                break
            except OSError:
                font = None

    if font is None:
        font = ImageFont.load_default()

    draw_tmp = ImageDraw.Draw(qr_img)
    padding = 8
    max_text_width = max(qr_img.width - padding * 2, min(1200, int(qr_img.width * 1.6)))

    def text_size(text, draw_obj, text_font):
        """Return text dimensions across supported Pillow versions."""
        if hasattr(draw_obj, 'textbbox'):
            left, top, right, bottom = draw_obj.textbbox((0, 0), text, font=text_font)
            return right - left, bottom - top
        left, top, right, bottom = text_font.getbbox(text)
        return right - left, bottom - top

    def wrap_text(text, draw_obj, text_font, max_width):
        has_whitespace = bool(re.search(r'\s', text))
        tokens = text.split() if has_whitespace else [tok for tok in re.split(r'([/:?&=#._-])', text) if tok]
        separator = ' ' if has_whitespace else ''
        if not tokens:
            return [text]
        lines = []
        cur = tokens[0]
        for token in tokens[1:]:
            test = cur + separator + token
            if text_size(test, draw_obj, text_font)[0] <= max_width:
                cur = test
            else:
                lines.append(cur)
                cur = token
        lines.append(cur)
        if len(lines) == 1 and text_size(lines[0], draw_obj, text_font)[0] > max_width:
            lines = []
            cur = ''
            for char in text:
                test = cur + char
                if cur and text_size(test, draw_obj, text_font)[0] > max_width:
                    lines.append(cur)
                    cur = char
                else:
                    cur = test
            if cur:
                lines.append(cur)
        return lines

    display_text = extract_display_text_from_url(url)
    lines = wrap_text(display_text, draw_tmp, font, max_text_width)
    line_height = text_size('Ay', draw_tmp, font)[1]
    text_width = max(text_size(line, draw_tmp, font)[0] for line in lines) if lines else 0
    text_block_height = line_height * len(lines) + padding * 2

    canvas_width = max(qr_img.width, text_width + padding * 2)
    new_img = Image.new('RGB', (canvas_width, text_block_height + qr_img.height), 'white')

    draw = ImageDraw.Draw(new_img)
    y = padding
    for line in lines:
        width, _ = text_size(line, draw, font)
        x = (new_img.width - width) // 2
        draw.text((x, y), line, font=font, fill='black')
        y += line_height

    qr_x = (new_img.width - qr_img.width) // 2
    new_img.paste(qr_img, (qr_x, text_block_height))
    new_img.save(full_path, img_format)

    return full_path


def _main():
    def open_file_with_default_app(path: str):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception:
            pass

    def process_urls(url_list, save_dir, save_options):
        results = []
        for u in url_list:
            u = u.strip()
            if not u:
                results.append(None)
                continue
            try:
                path = url_to_qr(u, save_dir=save_dir, save_options=save_options)
                print(path)
                results.append(path)
            except Exception as e:
                print(f"ERROR processing '{u}': {e}")
                results.append(None)
        return results

    parser = argparse.ArgumentParser(description='URL -> QR (package version)')
    parser.add_argument('urls', nargs='*', help='Optional URLs to process (if not provided, interactive input is used)')
    parser.add_argument('--save-dir', '-d', help='Override save directory (absolute or relative). Default: package-local output/QR', default=None)
    parser.add_argument('--save-options', help='JSON string or path to JSON file with save options', default=None)
    args = parser.parse_args()

    # Determine save dir: CLI > env CHOOSE_SAVE_DIR > package default
    save_dir = args.save_dir or os.environ.get('URL_QR_SAVE_DIR') or DEFAULT_SAVE_DIR
    # If save_dir is relative, make it relative to current working directory (not package), to respect user context
    save_dir = os.path.abspath(save_dir)
    os.makedirs(save_dir, exist_ok=True)

    save_options = None
    if args.save_options:
        # try as JSON string first, then as file path
        try:
            save_options = json.loads(args.save_options)
        except Exception:
            # try reading file
            if os.path.exists(args.save_options):
                try:
                    with open(args.save_options, 'r', encoding='utf-8') as f:
                        save_options = json.load(f)
                except Exception:
                    save_options = None
            else:
                save_options = None

    # If URLs passed on CLI, process and exit
    if args.urls:
        saved = process_urls(args.urls, save_dir, save_options)
        # open first if single
        if len(args.urls) == 1 and saved and saved[0]:
            open_file_with_default_app(saved[0])
        return

    # Interactive loop (same as original behavior)
    prompt = "请输入网址（一个或多个网址之间用空格分隔）。输入 quit 或 exit 停止。\n> "
    try:
        while True:
            try:
                line = input(prompt)
            except EOFError:
                print('\nEOF received, exiting.')
                break
            if not line:
                continue
            s = line.strip()
            if not s:
                continue
            if s.lower() in {"quit", "exit", "q"}:
                print("Exiting.")
                break
            tokens = s.split()
            saved = process_urls(tokens, save_dir, save_options)
            if len(tokens) == 1 and saved and saved[0]:
                open_file_with_default_app(saved[0])
    except KeyboardInterrupt:
        print('\nInterrupted, exiting.')


if __name__ == "__main__":
    _main()

